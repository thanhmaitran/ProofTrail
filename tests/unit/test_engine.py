"""Verification verdicts: HOLDS / VIOLATED / UNKNOWN / ERROR and their evidence."""

from __future__ import annotations

import pytest

from prooftrail.exploration.bfs import Limits
from prooftrail.model.parser import parse_specification
from prooftrail.verification.engine import (
    verify,
    verify_invariant,
    verify_property,
    verify_reachability,
)
from prooftrail.verification.results import Status


def spec(model: dict, properties: list[dict]):
    return parse_specification(
        {"schema_version": "1.0", "model": model, "properties": properties}
    )


def counter(*, guard: str = "x < 3", maximum: int = 9) -> dict:
    return {
        "id": "counter",
        "variables": {"x": {"type": "integer", "min": 0, "max": maximum, "initial": 0}},
        "transitions": [{"id": "inc", "when": guard, "updates": {"x": "x + 1"}}],
    }


def only(result_run, pid: str):
    return next(r for r in result_run.results if r.property_id == pid)


# ------------------------------------------------------------------- safety


def test_invariant_that_holds() -> None:
    s = spec(counter(), [{"id": "p", "type": "invariant", "expression": "x <= 3"}])
    run = verify(s.system, s.properties)
    result = run.results[0]
    assert result.status is Status.HOLDS and result.complete
    assert result.counterexample is None
    assert run.exhaustive and run.stop_reason is None
    assert run.statistics["states_explored"] == 4


def test_invariant_violation_gives_a_shortest_counterexample() -> None:
    s = spec(
        counter(guard="x <= 3"),
        [{"id": "p", "type": "invariant", "expression": "x <= 3"}],
    )
    result = verify(s.system, s.properties).results[0]
    assert result.status is Status.VIOLATED
    trace = result.counterexample
    assert trace is not None
    assert [step.state["x"] for step in trace.steps] == [0, 1, 2, 3, 4]
    assert [step.action for step in trace.steps] == [None] + ["inc"] * 4
    assert not trace.is_lasso


def test_violation_in_an_initial_state_is_a_zero_step_trace() -> None:
    s = spec(counter(), [{"id": "p", "type": "invariant", "expression": "x > 0"}])
    trace = verify(s.system, s.properties).results[0].counterexample
    assert trace is not None and len(trace.steps) == 1


def test_reachability_gives_a_witness_when_reachable() -> None:
    s = spec(counter(), [{"id": "r", "type": "reachability", "target": "x == 2"}])
    result = verify(s.system, s.properties).results[0]
    assert result.status is Status.HOLDS
    assert result.witness is not None and len(result.witness.steps) == 3
    assert result.counterexample is None


def test_unreachable_target_is_violated_with_reason_unreachable() -> None:
    s = spec(counter(), [{"id": "r", "type": "reachability", "target": "x == 5"}])
    result = verify(s.system, s.properties).results[0]
    assert result.status is Status.VIOLATED and result.reason == "unreachable"
    assert result.witness is None and result.counterexample is None


def test_deadlock_detection() -> None:
    s = spec(
        counter(),
        [
            {"id": "any", "type": "deadlock_free"},
            {"id": "allowed", "type": "deadlock_free", "unless": "x == 3"},
        ],
    )
    run = verify(s.system, s.properties)
    bad = only(run, "any")
    assert bad.status is Status.VIOLATED and bad.reason == "deadlock"
    assert bad.counterexample is not None and bad.counterexample.last.state["x"] == 3
    assert only(run, "allowed").status is Status.HOLDS


def test_a_cycle_has_no_deadlock() -> None:
    model = counter()
    model["transitions"][0] = {"id": "inc", "updates": {"x": "(x + 1) % 4"}}
    s = spec(model, [{"id": "d", "type": "deadlock_free"}])
    assert verify(s.system, s.properties).results[0].status is Status.HOLDS


def test_safety_only_runs_stop_as_soon_as_everything_is_decided() -> None:
    model = counter(maximum=1000, guard="x < 1000")
    s = spec(model, [{"id": "r", "type": "reachability", "target": "x == 2"}])
    run = verify(s.system, s.properties)
    assert run.stop_reason == "stopped_early" and not run.exhaustive
    assert run.statistics["states_explored"] < 10
    assert run.results[0].status is Status.HOLDS


# ------------------------------------------------------------------- limits


def test_hitting_a_limit_without_a_verdict_is_unknown_never_holds() -> None:
    s = spec(
        counter(maximum=500, guard="x < 500"),
        [{"id": "p", "type": "invariant", "expression": "x <= 500"}],
    )
    run = verify(s.system, s.properties, limits=Limits(max_states=10))
    result = run.results[0]
    assert result.status is Status.UNKNOWN and not result.complete
    assert result.reason == "max_states" and "not a proof" in (result.message or "")
    assert not run.exhaustive and run.stop_reason == "max_states"
    assert run.exit_code() == 3


def test_a_counterexample_found_before_the_limit_is_still_conclusive() -> None:
    s = spec(
        counter(maximum=500, guard="x < 500"),
        [{"id": "p", "type": "invariant", "expression": "x <= 3"}],
    )
    run = verify(s.system, s.properties, limits=Limits(max_states=10))
    assert run.results[0].status is Status.VIOLATED


def test_limits_make_unreachable_targets_unknown_not_violated() -> None:
    s = spec(
        counter(maximum=500, guard="x < 500"),
        [{"id": "r", "type": "reachability", "target": "x == 400"}],
    )
    result = verify(s.system, s.properties, limits=Limits(max_states=20)).results[0]
    assert result.status is Status.UNKNOWN


def test_depth_limit_gives_unknown_unless_the_whole_space_fits() -> None:
    s = spec(counter(), [{"id": "p", "type": "invariant", "expression": "x <= 3"}])
    assert (
        verify(s.system, s.properties, limits=Limits(max_depth=1)).results[0].status
        is Status.UNKNOWN
    )
    assert (
        verify(s.system, s.properties, limits=Limits(max_depth=3)).results[0].status
        is Status.HOLDS
    )


def test_temporal_properties_are_unknown_when_the_graph_is_incomplete() -> None:
    s = spec(
        counter(maximum=500, guard="x < 500"),
        [{"id": "t", "type": "ctl", "formula": "AF {x == 500}"}],
    )
    result = verify(s.system, s.properties, limits=Limits(max_states=10)).results[0]
    assert result.status is Status.UNKNOWN


# ------------------------------------------------------------- model errors


def test_a_model_error_yields_error_status_for_unresolved_properties_only() -> None:
    s = spec(
        counter(guard="x <= 9", maximum=9),
        [
            {"id": "early", "type": "reachability", "target": "x == 2"},
            {"id": "late", "type": "invariant", "expression": "x < 100"},
        ],
    )
    run = verify(s.system, s.properties)
    assert only(run, "early").status is Status.HOLDS
    late = only(run, "late")
    assert late.status is Status.ERROR and late.reason == "model_error"
    assert "outside its domain" in (late.message or "")
    assert run.overall() is Status.ERROR and run.exit_code() == 2


def test_an_error_does_not_hide_a_found_violation() -> None:
    s = spec(
        counter(guard="x <= 9", maximum=9),
        [{"id": "p", "type": "invariant", "expression": "x < 2"}],
    )
    assert verify(s.system, s.properties).results[0].status is Status.VIOLATED


# ---------------------------------------------------------------- temporal


def test_ctl_holding_and_violated() -> None:
    s = spec(
        counter(),
        [
            {"id": "ok", "type": "ctl", "formula": "AF {x == 3}"},
            {"id": "bad", "type": "ctl", "formula": "AF {x == 4}"},
            {"id": "never", "type": "ctl", "formula": "AG {x < 3}"},
            {"id": "witness", "type": "ctl", "formula": "EF {x == 3}"},
        ],
    )
    run = verify(s.system, s.properties)
    assert only(run, "ok").status is Status.HOLDS
    bad = only(run, "bad")
    assert bad.status is Status.VIOLATED and bad.counterexample is not None
    assert bad.counterexample.is_lasso  # an infinite path avoiding x == 4
    never = only(run, "never")
    assert (
        never.counterexample is not None and never.counterexample.last.state["x"] == 3
    )
    witness = only(run, "witness")
    assert witness.witness is not None and len(witness.witness.steps) == 4


def test_response_property_without_fairness() -> None:
    model = {
        "id": "starve",
        "variables": {
            "a": {"type": "boolean", "initial": False},
            "b": {"type": "boolean", "initial": False},
        },
        "transitions": [
            {"id": "spin", "updates": {"b": "not b"}},
            {"id": "serve", "when": "not a", "updates": {"a": "true"}},
        ],
    }
    s = spec(
        model,
        [
            {"id": "served", "type": "response", "trigger": "true", "response": "a"},
            {"id": "toggles", "type": "response", "trigger": "true", "response": "b"},
        ],
    )
    run = verify(s.system, s.properties)
    # The scheduler may run `spin` forever, so `a` is never forced.
    served = only(run, "served")
    assert served.status is Status.VIOLATED
    assert served.counterexample is not None and served.counterexample.is_lasso
    # `serve` can be taken only once; after that only `spin` is enabled, so
    # every infinite path toggles b and `b` is eventually true.
    assert only(run, "toggles").status is Status.HOLDS


def test_ctl_shape_without_a_trace_extractor_is_flagged_not_faked() -> None:
    s = spec(counter(), [{"id": "p", "type": "ctl", "formula": "EX {x == 5}"}])
    result = verify(s.system, s.properties).results[0]
    assert result.status is Status.VIOLATED
    assert result.counterexample is None
    assert result.reason == "no_trace_for_formula_shape"


# ------------------------------------------------------ options / conveniences


def test_options_never_change_verdicts() -> None:
    s = spec(
        counter(guard="x <= 3"),
        [
            {"id": "inv", "type": "invariant", "expression": "x <= 3"},
            {"id": "reach", "type": "reachability", "target": "x == 4"},
            {"id": "ctl", "type": "ctl", "formula": "AF {x == 4}"},
        ],
    )
    baseline = {
        r.property_id: (
            r.status,
            len(r.counterexample.steps) if r.counterexample else 0,
        )
        for r in verify(s.system, s.properties).results
    }
    for indexing in ("object", "packed"):
        for strategy in ("shared", "per-property"):
            for cache in (False, True):
                run = verify(
                    s.system,
                    s.properties,
                    indexing=indexing,
                    strategy=strategy,
                    successor_cache=cache,
                )
                got = {
                    r.property_id: (
                        r.status,
                        len(r.counterexample.steps) if r.counterexample else 0,
                    )
                    for r in run.results
                }
                assert got == baseline, (indexing, strategy, cache)


def test_peak_memory_is_measured_on_request() -> None:
    s = spec(counter(), [{"id": "p", "type": "invariant", "expression": "x <= 3"}])
    assert "peak_memory_bytes" not in verify(s.system, s.properties).statistics
    run = verify(s.system, s.properties, measure_memory=True)
    assert run.statistics["peak_memory_bytes"] > 0


def test_invalid_options_are_rejected() -> None:
    s = spec(counter(), [{"id": "p", "type": "invariant", "expression": "x <= 3"}])
    with pytest.raises(ValueError, match="indexing"):
        verify(s.system, s.properties, indexing="nope")
    with pytest.raises(ValueError, match="strategy"):
        verify(s.system, s.properties, strategy="nope")


def test_convenience_wrappers() -> None:
    s = spec(
        counter(),
        [
            {"id": "i", "type": "invariant", "expression": "x <= 3"},
            {"id": "r", "type": "reachability", "target": "x == 3"},
        ],
    )
    assert verify_invariant(s.system, s.properties[0]).status is Status.HOLDS
    assert verify_reachability(s.system, s.properties[1]).status is Status.HOLDS
    assert verify_property(s.system, s.properties[0]).property_id == "i"


def test_no_properties_gives_an_empty_holding_run() -> None:
    s = spec(counter(), [])
    run = verify(s.system, s.properties)
    assert run.results == [] and run.overall() is Status.HOLDS
    assert run.counts() == {"holds": 0, "violated": 0, "unknown": 0, "error": 0}


def test_overall_status_precedence() -> None:
    s = spec(
        counter(maximum=500, guard="x < 500"),
        [
            {"id": "a", "type": "invariant", "expression": "x <= 3"},
            {"id": "b", "type": "invariant", "expression": "x <= 400"},
        ],
    )
    run = verify(s.system, s.properties, limits=Limits(max_states=50))
    assert only(run, "a").status is Status.VIOLATED
    assert only(run, "b").status is Status.UNKNOWN  # undecided within 50 states
    assert run.overall() is Status.VIOLATED and run.exit_code() == 1


def test_status_is_a_string_enum() -> None:
    assert Status.HOLDS == "HOLDS" and str(Status.UNKNOWN) == "UNKNOWN"
    assert Status("VIOLATED") is Status.VIOLATED
