"""The verification engine: from a model and properties to evidence-backed verdicts.

Strategy ``shared`` (default) uses one exploration for every property:

* If all properties are safety properties (invariant, reachability, deadlock)
  the exploration stops as soon as every property is resolved.
* If any property is temporal (CTL / response) the whole reachable graph is
  explored once and every property is evaluated on it.

Strategy ``per-property`` runs a separate exploration for each property; it
exists for benchmarking and as a reference mode.

Verdict rules (see ``docs/formal-semantics.md``):

* A counterexample found is conclusive even if a resource limit was also hit.
* ``HOLDS`` for a safety property requires an *exhaustive* exploration.
* A limit hit without a verdict gives ``UNKNOWN``, never ``HOLDS``.
* A modelling error gives ``ERROR`` for unresolved properties only.
* Every trace is re-validated against the transition system before it is
  reported; a trace that fails validation becomes ``ERROR`` (an engine bug),
  never a bogus ``VIOLATED``.
"""

from __future__ import annotations

import time
import tracemalloc
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from prooftrail.errors import TraceError
from prooftrail.exploration.bfs import Exploration, Limits, explore
from prooftrail.exploration.indexing import INDEXING_KINDS
from prooftrail.model.state import State
from prooftrail.model.system import STUTTER, CachedSystem, TransitionSystem
from prooftrail.properties.base import Property
from prooftrail.properties.ctl import (
    CTLChecker,
    CTLProperty,
    ResponseProperty,
    TotalGraph,
)
from prooftrail.properties.deadlock import DeadlockFreedom
from prooftrail.properties.invariant import InvariantProperty
from prooftrail.properties.reachability import ReachabilityProperty
from prooftrail.verification.counterexample import (
    StateCheck,
    ctl_evidence,
    reconstruct_trace,
    validate_trace,
)
from prooftrail.verification.results import (
    PropertyResult,
    Status,
    Trace,
    VerificationRun,
)

STRATEGIES = ("shared", "per-property")


@dataclass
class _Tracker:
    """Tracks the first state that resolves a safety property."""

    prop: Property
    hit: int | None = None


def verify(
    system: TransitionSystem,
    properties: Iterable[Property],
    *,
    limits: Limits | None = None,
    indexing: str = "object",
    strategy: str = "shared",
    successor_cache: bool = False,
    measure_memory: bool = False,
) -> VerificationRun:
    """Check ``properties`` on ``system`` and return evidence-backed results."""
    if indexing not in INDEXING_KINDS:
        raise ValueError(f"indexing must be one of {INDEXING_KINDS}, got {indexing!r}")
    if strategy not in STRATEGIES:
        raise ValueError(f"strategy must be one of {STRATEGIES}, got {strategy!r}")
    limits = limits or Limits()
    props = list(properties)

    started_tracing = False
    if measure_memory and not tracemalloc.is_tracing():
        tracemalloc.start()
        started_tracing = True
    started = time.perf_counter()
    try:
        runner = CachedSystem(system) if successor_cache else system
        groups = [props] if strategy == "shared" else [[p] for p in props]
        results: list[PropertyResult] = []
        explorations: list[Exploration] = []
        for group in groups:
            if not group:
                continue
            group_results, exploration = _verify_group(
                system, runner, group, limits, indexing
            )
            results.extend(group_results)
            explorations.append(exploration)
        peak = tracemalloc.get_traced_memory()[1] if measure_memory else None
    finally:
        if started_tracing:
            tracemalloc.stop()
    elapsed = time.perf_counter() - started

    statistics = _combine_statistics(explorations, elapsed, peak)
    stop_reason = next((e.stop_reason for e in explorations if e.stop_reason), None)
    return VerificationRun(
        model_id=system.id,
        results=results,
        limits=limits.to_dict(),
        indexing=indexing,
        strategy=strategy,
        statistics=statistics,
        exhaustive=bool(explorations) and all(e.complete for e in explorations),
        stop_reason=stop_reason,
    )


def verify_property(
    system: TransitionSystem, prop: Property, **options: Any
) -> PropertyResult:
    """Check a single property and return its result."""
    return verify(system, [prop], **options).results[0]


def verify_invariant(
    system: TransitionSystem, property: InvariantProperty, **options: Any
) -> PropertyResult:
    """Check one invariant (convenience wrapper around :func:`verify`)."""
    return verify_property(system, property, **options)


def verify_reachability(
    system: TransitionSystem, property: ReachabilityProperty, **options: Any
) -> PropertyResult:
    return verify_property(system, property, **options)


# ---------------------------------------------------------------- internals


def _combine_statistics(
    explorations: Sequence[Exploration], elapsed: float, peak: int | None
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "explorations": len(explorations),
        "states_explored": max(
            (e.stats.states_explored for e in explorations), default=0
        ),
        "states_expanded": sum(e.stats.states_expanded for e in explorations),
        "transitions_examined": sum(e.stats.transitions_examined for e in explorations),
        "max_depth_reached": max(
            (e.stats.max_depth_reached for e in explorations), default=0
        ),
        "deadlock_states": max(
            (e.stats.deadlock_states for e in explorations), default=0
        ),
        "elapsed_seconds": round(elapsed, 6),
    }
    if peak is not None:
        stats["peak_memory_bytes"] = peak
    return stats


def _verify_group(
    system: TransitionSystem,
    runner: Any,
    props: list[Property],
    limits: Limits,
    indexing: str,
) -> tuple[list[PropertyResult], Exploration]:
    trackers = {p.id: _Tracker(p) for p in props if not p.needs_graph}
    graph_props = [p for p in props if p.needs_graph]
    need_graph = bool(graph_props)

    unresolved = len(trackers)
    invariants = [t for t in trackers.values() if isinstance(t.prop, InvariantProperty)]
    reaches = [t for t in trackers.values() if isinstance(t.prop, ReachabilityProperty)]
    deadlocks = [t for t in trackers.values() if isinstance(t.prop, DeadlockFreedom)]
    stop_when_resolved = bool(trackers) and not need_graph

    def on_discover(i: int, state: State) -> bool:
        nonlocal unresolved
        for t in invariants:
            if t.hit is None and not t.prop.holds_in(state):  # type: ignore[attr-defined]
                t.hit = i
                unresolved -= 1
        for t in reaches:
            if t.hit is None and t.prop.reached_by(state):  # type: ignore[attr-defined]
                t.hit = i
                unresolved -= 1
        return stop_when_resolved and unresolved == 0

    def on_expand(i: int, state: State, successors: list[tuple[str, State]]) -> bool:
        nonlocal unresolved
        if not successors:
            for t in deadlocks:
                if t.hit is None and t.prop.is_violation(state, False):  # type: ignore[attr-defined]
                    t.hit = i
                    unresolved -= 1
        return stop_when_resolved and unresolved == 0

    exploration = explore(
        runner,
        limits=limits,
        indexing=indexing,
        track_parents=True,
        store_edges=need_graph,
        on_discover=on_discover if (invariants or reaches) else None,
        on_expand=on_expand if deadlocks else None,
    )

    initial_states = system.initial_states()
    stats = exploration.stats.to_dict()
    by_id: dict[str, PropertyResult] = {}

    for tracker in trackers.values():
        by_id[tracker.prop.id] = _safety_result(
            system, tracker, exploration, initial_states, stats
        )

    if graph_props:
        by_id.update(
            _graph_results(system, graph_props, exploration, initial_states, stats)
        )

    return [by_id[p.id] for p in props], exploration


def _inconclusive(
    prop: Property, exploration: Exploration, stats: dict
) -> PropertyResult:
    """Result for a property that no verdict could be reached for."""
    if exploration.error is not None:
        return PropertyResult(
            prop.id,
            prop.kind,
            Status.ERROR,
            prop.description,
            reason="model_error",
            message=str(exploration.error),
            statistics=stats,
        )
    reason = exploration.stop_reason or "unknown"
    return PropertyResult(
        prop.id,
        prop.kind,
        Status.UNKNOWN,
        prop.description,
        reason=reason,
        message=(
            f"Exploration stopped ({reason}) before the property could be decided; "
            "this is not a proof that it holds."
        ),
        statistics=stats,
    )


def _safety_result(
    system: TransitionSystem,
    tracker: _Tracker,
    exploration: Exploration,
    initial_states: list[State],
    stats: dict,
) -> PropertyResult:
    prop = tracker.prop
    base = {"property_id": prop.id, "kind": prop.kind, "description": prop.description}

    if tracker.hit is None:
        if not exploration.complete:
            return _inconclusive(prop, exploration, stats)
        if isinstance(prop, ReachabilityProperty):
            return PropertyResult(
                **base,
                status=Status.VIOLATED,
                reason="unreachable",
                message=(
                    "No reachable state satisfies the target; the whole state "
                    f"space ({stats['states_explored']} states) was explored."
                ),
                statistics=stats,
            )
        return PropertyResult(**base, status=Status.HOLDS, statistics=stats)

    trace = reconstruct_trace(exploration, tracker.hit)
    check = safety_state_check(system, prop)
    try:
        validate_trace(system, trace, initial_states=initial_states, state_check=check)
    except TraceError as exc:
        return _validation_failure(prop, exc, stats)

    if isinstance(prop, ReachabilityProperty):
        return PropertyResult(
            **base, status=Status.HOLDS, witness=trace, statistics=stats
        )
    reason = "deadlock" if isinstance(prop, DeadlockFreedom) else None
    return PropertyResult(
        **base,
        status=Status.VIOLATED,
        reason=reason,
        counterexample=trace,
        statistics=stats,
    )


def safety_state_check(system: TransitionSystem, prop: Property) -> StateCheck:
    def check(trace: Trace, i: int) -> str | None:
        state = trace.steps[i].state
        last = i == len(trace.steps) - 1
        if isinstance(prop, InvariantProperty):
            holds = prop.holds_in(state)
            if last and holds:
                return "final state does not violate the invariant"
            if not last and not holds:
                return "an earlier state already violates the invariant"
        elif isinstance(prop, ReachabilityProperty):
            if last and not prop.reached_by(state):
                return "final state does not satisfy the target"
        elif isinstance(prop, DeadlockFreedom) and last:
            if not prop.is_violation(state, not system.is_deadlock(state)):
                return "final state is not a deadlock (or is an allowed terminal state)"
        return None

    return check


def _validation_failure(prop: Property, exc: Exception, stats: dict) -> PropertyResult:
    return PropertyResult(
        prop.id,
        prop.kind,
        Status.ERROR,
        prop.description,
        reason="trace_validation_failed",
        message=f"Internal error: the trace failed validation ({exc}).",
        statistics=stats,
    )


def _graph_results(
    system: TransitionSystem,
    props: list[Property],
    exploration: Exploration,
    initial_states: list[State],
    stats: dict,
) -> dict[str, PropertyResult]:
    if not exploration.complete or exploration.edges is None:
        return {p.id: _inconclusive(p, exploration, stats) for p in props}

    n = len(exploration.index)
    graph = TotalGraph(
        [exploration.state(i) for i in range(n)],
        exploration.edges,
        exploration.initial,
        STUTTER,
    )
    checker = CTLChecker(graph)
    out: dict[str, PropertyResult] = {}
    for prop in props:
        assert isinstance(prop, CTLProperty | ResponseProperty)
        formula = prop.formula
        holds = checker.holds_initially(formula)
        status = Status.HOLDS if holds else Status.VIOLATED
        result = PropertyResult(
            prop.id, prop.kind, status, prop.description, statistics=stats
        )
        evidence = ctl_evidence(formula, graph, checker, exploration, holds)
        if evidence is not None:
            try:
                validate_trace(
                    system,
                    evidence.trace,
                    initial_states=initial_states,
                    state_check=evidence.state_check,
                )
            except TraceError as exc:
                out[prop.id] = _validation_failure(prop, exc, stats)
                continue
            if evidence.is_witness:
                result.witness = evidence.trace
            else:
                result.counterexample = evidence.trace
        elif not holds:
            result.reason = "no_trace_for_formula_shape"
            result.message = (
                "The formula is violated, but its shape has no trace extractor, "
                "so no counterexample is shown."
            )
        out[prop.id] = result
    return out


__all__ = [
    "STRATEGIES",
    "verify",
    "verify_invariant",
    "verify_property",
    "verify_reachability",
]
