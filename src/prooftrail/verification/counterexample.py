"""Building and independently validating counterexample / witness traces."""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from prooftrail.errors import TraceError
from prooftrail.exploration.bfs import Exploration
from prooftrail.exploration.predecessor import path_to
from prooftrail.model.state import State
from prooftrail.model.system import STUTTER, TransitionSystem
from prooftrail.properties.ctl import (
    AF,
    AG,
    EF,
    EG,
    And,
    CTLChecker,
    Formula,
    Implies,
    Not,
    TotalGraph,
    evaluate_propositional,
    is_propositional,
)
from prooftrail.verification.results import Step, Trace

StateCheck = Callable[[Trace, int], "str | None"]


def reconstruct_trace(exploration: Exploration, target: int) -> Trace:
    """Shortest trace from an initial state to the state with id ``target``."""
    steps = tuple(
        Step(position, exploration.state(index), action)
        for position, (index, action) in enumerate(path_to(exploration, target))
    )
    return Trace(steps)


def lasso_from(graph: TotalGraph, region: frozenset[int], start: int) -> Trace:
    """An infinite path from ``start`` staying inside ``region``.

    ``region`` must be a greatest fixed point of ``Z = P ∩ EX Z`` (the states
    with an infinite path inside ``P``), so every state in it has a successor
    in it and the walk cannot get stuck. The walk takes the first such
    successor each time, so the result is valid but not necessarily shortest.
    """
    if start not in region:
        raise ValueError("start state is not in the region")
    steps: list[Step] = [Step(0, graph.states[start], None)]
    seen = {start: 0}
    current = start
    while True:
        step = next(((a, n) for a, n in graph.edges[current] if n in region), None)
        if step is None:  # pragma: no cover - impossible for a true greatest fixpoint
            raise ValueError("region is not closed under successors")
        action, nxt = step
        if nxt in seen:
            return Trace(tuple(steps), loop_start=seen[nxt], loop_action=action)
        seen[nxt] = len(steps)
        steps.append(Step(len(steps), graph.states[nxt], action))
        current = nxt


def append_lasso(prefix: Trace, lasso: Trace) -> Trace:
    """Join a finite prefix ending in the lasso's first state with the lasso."""
    offset = len(prefix.steps) - 1
    joined = list(prefix.steps)
    for step in lasso.steps[1:]:
        joined.append(Step(offset + step.index, step.state, step.action))
    assert lasso.loop_start is not None
    return Trace(tuple(joined), offset + lasso.loop_start, lasso.loop_action)


# ---------------------------------------------------------------- validation


def validate_trace(
    system: TransitionSystem,
    trace: Trace,
    *,
    initial_states: Sequence[State] | None = None,
    state_check: StateCheck | None = None,
) -> None:
    """Re-check ``trace`` against the transition system; raise on any defect.

    Checked: it is non-empty with consecutive indices; it starts in a genuine
    initial state; every state is legal; every step is a transition that really
    exists in the system (a stutter step is accepted only at a deadlock state);
    for a lasso, the closing edge exists as well. ``state_check(trace, i)`` may
    return an error message to reject step ``i`` semantically.

    This re-derives each edge from the transition system rather than trusting
    the exploration's predecessor bookkeeping, so it catches defects in path
    reconstruction. It shares the guard/update evaluator with the engine, so it
    is not an independent check of expression semantics.
    """
    if not trace.steps:
        raise TraceError("trace is empty")
    for expected, step in enumerate(trace.steps):
        if step.index != expected:
            raise TraceError(f"step {expected} has index {step.index}")
    first = trace.steps[0]
    if first.action is not None:
        raise TraceError("the first step must not carry an action")
    initial = set(
        initial_states if initial_states is not None else system.initial_states()
    )
    if first.state not in initial:
        raise TraceError(f"first state is not an initial state: {first.state}")

    for step in trace.steps:
        try:
            system.validate_state(step.state)
        except Exception as exc:
            raise TraceError(f"step {step.index}: {exc}") from None
        if state_check is not None:
            problem = state_check(trace, step.index)
            if problem:
                raise TraceError(f"step {step.index}: {problem}")

    for previous, step in zip(trace.steps, trace.steps[1:], strict=False):
        _require_edge(system, previous.state, step.action, step.state, step.index)

    if trace.loop_start is not None:
        if not 0 <= trace.loop_start < len(trace.steps):
            raise TraceError(f"loop start {trace.loop_start} is out of range")
        _require_edge(
            system,
            trace.steps[-1].state,
            trace.loop_action,
            trace.steps[trace.loop_start].state,
            trace.loop_start,
        )


def _require_edge(
    system: TransitionSystem,
    source: State,
    action: str | None,
    target: State,
    where: int,
) -> None:
    if action is None:
        raise TraceError(f"step {where} has no action")
    if action == STUTTER:
        if source != target or not system.is_deadlock(source):
            raise TraceError(
                f"step {where}: stutter is only allowed as a self-loop at a deadlock"
            )
        return
    if (action, target) not in system.successors(source):
        raise TraceError(
            f"step {where}: no transition {action!r} leads from "
            f"[{source}] to [{target}]"
        )


# ------------------------------------------------------------ CTL evidence


@dataclass(frozen=True)
class Evidence:
    """A trace plus an optional semantic check for it.

    ``is_witness`` is true for a trace demonstrating a property that holds
    (``EF``), false for a counterexample to one that fails.
    """

    trace: Trace
    is_witness: bool
    state_check: StateCheck | None = None


def ctl_evidence(
    formula: Formula,
    graph: TotalGraph,
    checker: CTLChecker,
    exploration: Exploration,
    holds: bool,
) -> Evidence | None:
    """Extract a trace for the common shapes of top-level CTL formulas.

    * violated ``AG g``  -> shortest path to a state where ``g`` fails, extended
      by a lasso when ``g`` is ``AF h`` or ``a -> AF h`` (a response property);
    * violated ``AF g``  -> a lasso on which ``g`` never holds;
    * holding ``EF g``   -> a shortest witness path to a state satisfying ``g``.

    Any other shape returns ``None``: the verdict stands but no trace is
    offered, rather than offering one that is not a real explanation.
    """
    sat = checker.sat
    if not holds:
        match formula:
            case AG(g):
                outside = sorted(set(range(len(graph))) - sat(g))
                target = outside[0]  # lowest index = shallowest in BFS order
                prefix = reconstruct_trace(exploration, target)
                lasso = _extension(g, target, graph, checker)
                trace = append_lasso(prefix, lasso) if lasso else prefix
                prefix_last = len(prefix.steps) - 1
                check = (
                    _check_response_lasso(g, prefix_last)
                    if lasso
                    else _check_ag_finite(g)
                )
                return Evidence(trace, False, check)
            case AF(g):
                region = sat(EG(Not(g)))
                start = min(i for i in graph.initial if i in region)
                return Evidence(
                    lasso_from(graph, region, start), False, _check_never(g, 0)
                )
        return None

    match formula:
        case EF(g):
            trace = _shortest_trace(graph, graph.initial[0], sat(g))
            return Evidence(trace, True, _check_last(g, want=True))
    return None


def _extension(
    g: Formula, state: int, graph: TotalGraph, checker: CTLChecker
) -> Trace | None:
    """If ``g`` fails at ``state`` because an ``AF`` fails, return its lasso."""
    sat = checker.sat
    match g:
        case AF(h):
            if state in sat(g):
                return None
            return lasso_from(graph, sat(EG(Not(h))), state)
        case Implies(_, b):
            return _extension(b, state, graph, checker)
        case And(a, b):
            for part in (a, b):
                if state not in sat(part):
                    return _extension(part, state, graph, checker)
    return None


def _shortest_trace(graph: TotalGraph, source: int, targets: frozenset[int]) -> Trace:
    parent: dict[int, tuple[int, str] | None] = {source: None}
    queue = deque([source])
    found = source if source in targets else None
    while queue and found is None:
        i = queue.popleft()
        for action, j in graph.edges[i]:
            if j not in parent:
                parent[j] = (i, action)
                if j in targets:
                    found = j
                    break
                queue.append(j)
    if found is None:  # pragma: no cover - EF holds, so a target is reachable
        raise ValueError("no target reachable from the source")
    chain: list[tuple[int, str | None]] = []
    node: int | None = found
    while node is not None:
        link = parent[node]
        chain.append((node, link[1] if link else None))
        node = link[0] if link else None
    chain.reverse()
    return Trace(
        tuple(Step(k, graph.states[i], action) for k, (i, action) in enumerate(chain))
    )


# Semantic checks. They only exist when the relevant sub-formulas contain no
# temporal operator, because only then can a single state be judged alone.


def _check_ag_finite(g: Formula) -> StateCheck | None:
    if not is_propositional(g):
        return None

    def check(trace: Trace, i: int) -> str | None:
        holds = evaluate_propositional(g, trace.steps[i].state)
        if i == len(trace.steps) - 1:
            return (
                "final state satisfies the formula that should fail" if holds else None
            )
        return "an earlier state already violates the formula" if not holds else None

    return check


def _response_parts(g: Formula) -> tuple[Formula | None, Formula] | None:
    match g:
        case AF(h) if is_propositional(h):
            return None, h
        case Implies(a, AF(h)) if is_propositional(a) and is_propositional(h):
            return a, h
    return None


def _check_response_lasso(g: Formula, start: int) -> StateCheck | None:
    parts = _response_parts(g)
    if parts is None:
        return None
    trigger, response = parts

    def check(trace: Trace, i: int) -> str | None:
        state = trace.steps[i].state
        if (
            i == start
            and trigger is not None
            and not evaluate_propositional(trigger, state)
        ):
            return "the trigger does not hold where the lasso starts"
        if i >= start and evaluate_propositional(response, state):
            return "the response holds on the lasso, so it is not a counterexample"
        return None

    return check


def _check_never(g: Formula, start: int) -> StateCheck | None:
    if not is_propositional(g):
        return None

    def check(trace: Trace, i: int) -> str | None:
        if i >= start and evaluate_propositional(g, trace.steps[i].state):
            return "the awaited condition holds on the lasso"
        return None

    return check


def _check_last(g: Formula, *, want: bool) -> StateCheck | None:
    if not is_propositional(g):
        return None

    def check(trace: Trace, i: int) -> str | None:
        if i == len(trace.steps) - 1:
            if evaluate_propositional(g, trace.steps[i].state) != want:
                return "final state does not satisfy the target condition"
        return None

    return check
