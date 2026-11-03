"""Counterexample construction and independent trace validation."""

from __future__ import annotations

import pytest
from support import system_from_dict

from prooftrail.errors import TraceError
from prooftrail.model.state import State
from prooftrail.model.system import STUTTER
from prooftrail.verification.counterexample import validate_trace
from prooftrail.verification.results import Step, Trace


def system():
    return system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": 3, "initial": 0}
                },
                "transitions": [
                    {"id": "inc", "when": "x < 3", "updates": {"x": "x + 1"}},
                    {"id": "reset", "when": "x == 3", "updates": {"x": "0"}},
                ],
            },
        }
    )


def stuck_system():
    return system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "stuck",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": 1, "initial": 0}
                },
                "transitions": [{"id": "go", "when": "x == 0", "updates": {"x": "1"}}],
            },
        }
    )


def trace(*pairs: tuple[int, str | None], loop: tuple[int, str] | None = None) -> Trace:
    steps = tuple(
        Step(i, State.from_dict({"x": value}), action)
        for i, (value, action) in enumerate(pairs)
    )
    if loop is None:
        return Trace(steps)
    return Trace(steps, loop_start=loop[0], loop_action=loop[1])


GOOD = trace((0, None), (1, "inc"), (2, "inc"), (3, "inc"))


def test_a_genuine_trace_is_accepted() -> None:
    validate_trace(system(), GOOD)


def test_a_genuine_lasso_is_accepted() -> None:
    validate_trace(
        system(),
        trace(*[(i, None if i == 0 else "inc") for i in range(4)], loop=(0, "reset")),
    )


def rejects(bad: Trace, fragment: str, sys=None) -> None:
    with pytest.raises(TraceError, match=fragment):
        validate_trace(sys or system(), bad)


def test_empty_trace() -> None:
    rejects(Trace(()), "empty")


def test_wrong_start_state() -> None:
    rejects(trace((1, None), (2, "inc")), "not an initial state")


def test_first_step_must_not_have_an_action() -> None:
    rejects(trace((0, "inc"), (1, "inc")), "must not carry an action")


def test_a_missing_action() -> None:
    rejects(trace((0, None), (1, None)), "has no action")


def test_a_transition_that_does_not_exist() -> None:
    rejects(trace((0, None), (2, "inc")), "no transition 'inc'")
    rejects(trace((0, None), (1, "reset")), "no transition 'reset'")
    rejects(trace((0, None), (1, "ghost")), "no transition 'ghost'")


def test_a_disabled_transition() -> None:
    rejects(trace((0, None), (1, "inc"), (2, "inc"), (3, "inc"), (4, "inc")), "outside")


def test_indices_must_be_consecutive() -> None:
    bad = Trace(
        (Step(0, State.from_dict({"x": 0})), Step(2, State.from_dict({"x": 1}), "inc"))
    )
    rejects(bad, "has index 2")


def test_illegal_state_values() -> None:
    rejects(trace((0, None), (7, "inc")), "outside its domain")


def test_lasso_closing_edge_must_exist() -> None:
    rejects(trace((0, None), (1, "inc"), loop=(0, "inc")), "no transition")
    rejects(trace((0, None), (1, "inc"), loop=(5, "inc")), "out of range")


def test_stutter_only_at_a_real_deadlock() -> None:
    stuck = stuck_system()
    ok = trace((0, None), (1, "go"), loop=(1, STUTTER))
    validate_trace(stuck, ok)
    # x == 0 is not a deadlock, so a stutter there is a lie.
    rejects(trace((0, None), (0, STUTTER)), "stutter is only allowed", stuck)
    # A stutter must not change the state.
    rejects(trace((0, None), (1, "go"), (0, STUTTER)), "stutter", stuck)


def test_semantic_check_can_reject_a_trace() -> None:
    def check(t: Trace, i: int) -> str | None:
        return "nope" if i == 2 else None

    with pytest.raises(TraceError, match="step 2: nope"):
        validate_trace(system(), GOOD, state_check=check)


def test_initial_states_can_be_supplied_to_avoid_recomputation() -> None:
    validate_trace(system(), GOOD, initial_states=[State.from_dict({"x": 0})])
    with pytest.raises(TraceError, match="not an initial state"):
        validate_trace(system(), GOOD, initial_states=[State.from_dict({"x": 1})])


def test_trace_dictionary_roundtrip() -> None:
    lasso = trace((0, None), (1, "inc"), loop=(0, "reset"))
    data = lasso.to_dict()
    assert data["loop"] == {"back_to": 0, "action": "reset"}
    assert Trace.from_dict(data) == lasso
    assert Trace.from_dict(GOOD.to_dict()) == GOOD
    assert len(GOOD) == 4 and GOOD.last.state["x"] == 3 and not GOOD.is_lasso
    assert lasso.is_lasso


def test_malformed_trace_dictionaries() -> None:
    from prooftrail.errors import ProofTrailError

    for bad in [
        {},
        {"steps": [{"index": "a", "state": {}}]},
        {"steps": [{"state": {}}]},
    ]:
        with pytest.raises(ProofTrailError, match="Malformed trace"):
            Trace.from_dict(bad)
