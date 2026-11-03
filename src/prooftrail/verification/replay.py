"""Independent replay of the traces stored in a report.

``prooftrail explain --model MODEL`` uses this to re-check every counterexample
and witness in a saved report against the model, without trusting the engine
that produced the report. A replay confirms that

* the trace starts in a genuine initial state,
* every step (and the closing edge of a lasso) is a transition of the model,
* the trace really demonstrates what the verdict claims.

The semantic check is exact for invariants, reachability, deadlock freedom and
response properties. For general CTL formulas only the structural checks apply
(``semantic`` is ``False`` in the outcome).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from prooftrail.errors import ProofTrailError, TraceError
from prooftrail.model.expressions import Expression
from prooftrail.model.system import TransitionSystem
from prooftrail.properties.base import Property
from prooftrail.properties.ctl import ResponseProperty
from prooftrail.verification.counterexample import validate_trace
from prooftrail.verification.engine import safety_state_check
from prooftrail.verification.results import Trace


@dataclass(frozen=True)
class ReplayOutcome:
    property_id: str
    trace_kind: str  # "counterexample" or "witness"
    ok: bool
    semantic: bool
    message: str


def replay_report(
    system: TransitionSystem,
    properties: Sequence[Property],
    report: Mapping[str, Any],
) -> list[ReplayOutcome]:
    """Re-validate every trace in ``report`` against ``system``."""
    by_id = {p.id: p for p in properties}
    outcomes: list[ReplayOutcome] = []
    for item in report["results"]:
        for key in ("counterexample", "witness"):
            if key not in item:
                continue
            pid = item["property_id"]
            prop = by_id.get(pid)
            if prop is None:
                outcomes.append(
                    ReplayOutcome(
                        pid, key, False, False, "property not in the model file"
                    )
                )
                continue
            trace = Trace.from_dict(item[key])
            outcomes.append(_replay_one(system, prop, key, trace))
    return outcomes


def _replay_one(
    system: TransitionSystem, prop: Property, key: str, trace: Trace
) -> ReplayOutcome:
    semantic = True
    try:
        check = None
        if prop.kind in ("invariant", "reachability", "deadlock_free"):
            check = safety_state_check(system, prop)
        elif not isinstance(prop, ResponseProperty):
            semantic = False
        validate_trace(system, trace, state_check=check)
        if isinstance(prop, ResponseProperty):
            _check_response_trace(prop, trace)
    except (TraceError, ProofTrailError) as exc:
        return ReplayOutcome(prop.id, key, False, semantic, str(exc))
    note = "valid" if semantic else "structurally valid (no semantic check for CTL)"
    return ReplayOutcome(prop.id, key, True, semantic, note)


def _check_response_trace(prop: ResponseProperty, trace: Trace) -> None:
    """A response counterexample: trigger at k, response never from there on."""
    if trace.loop_start is None:
        raise TraceError("a response counterexample must be a lasso")
    trigger: Expression = prop.trigger
    response: Expression = prop.response
    for k, step in enumerate(trace.steps):
        if not trigger.evaluate(step.state):
            continue
        first_visited = min(k, trace.loop_start)
        if all(
            not response.evaluate(trace.steps[i].state)
            for i in range(first_visited, len(trace.steps))
        ):
            return
    raise TraceError("no trigger state is followed by a response-free infinite path")
