"""Guards for the semantic pitfalls the design explicitly calls out.

Each test pins one rule that is easy to break by accident. They are named after
the rule, not after a ticket: when a real defect is found, add a test here that
reproduces it first, then fix it.
"""

from __future__ import annotations

import pytest

from prooftrail.cli import EXIT_ERROR, EXIT_UNKNOWN, main
from prooftrail.errors import SpecError
from prooftrail.exploration.bfs import Limits
from prooftrail.model.expressions import compile_expression
from prooftrail.model.parser import parse_specification
from prooftrail.model.state import State
from prooftrail.verification.engine import verify
from prooftrail.verification.results import Status


def spec(variables: dict, transitions: list, properties: list):
    return parse_specification(
        {
            "schema_version": "1.0",
            "model": {"id": "m", "variables": variables, "transitions": transitions},
            "properties": properties,
        }
    )


def test_true_is_not_the_integer_one() -> None:
    # In Python True == 1 and True + 1 == 2. In a model they are different types.
    scope = {"n": "int", "flag": "bool"}
    for source in ("flag + 1", "n and flag", "n == flag", "not n", "flag < 2"):
        with pytest.raises(SpecError):
            compile_expression(source, scope)
    with pytest.raises(SpecError, match="not a valid integer"):
        spec({"n": {"type": "integer", "initial": True}}, [], [])
    with pytest.raises(SpecError, match="not a valid boolean"):
        spec({"b": {"type": "boolean", "initial": 1}}, [], [])


def test_states_differing_only_in_bool_versus_int_are_still_validated() -> None:
    system = spec(
        {"n": {"type": "integer", "min": 0, "max": 1, "initial": 0}}, [], []
    ).system
    with pytest.raises(Exception, match="outside its domain"):
        system.validate_state(State.from_dict({"n": True}))


def test_a_syntax_error_is_an_error_never_a_violation(capsys, spec_file) -> None:
    path = spec_file(
        'schema_version: "1.0"\nmodel:\n  id: m\n  variables:\n'
        "    x: {type: integer, min: 0, max: 3, initial: 0}\n"
        "properties:\n  - {id: p, type: invariant, expression: 'x <='}\n"
    )
    assert main(["check", str(path)]) == EXIT_ERROR  # not 1 (violated)
    captured = capsys.readouterr()
    assert "VIOLATED" not in captured.out + captured.err


def test_running_out_of_states_is_never_reported_as_success(capsys, spec_file) -> None:
    path = spec_file(
        'schema_version: "1.0"\nmodel:\n  id: m\n  variables:\n'
        "    x: {type: integer, min: 0, max: 99, initial: 0}\n"
        "  transitions:\n    - {id: t, when: 'x < 99', updates: {x: 'x + 1'}}\n"
        "properties:\n  - {id: p, type: invariant, expression: 'x >= 0'}\n"
    )
    assert main(["check", str(path), "--max-states", "10"]) == EXIT_UNKNOWN
    out = capsys.readouterr().out
    assert "HOLDS" not in out and "[PASS]" not in out and "UNKNOWN" in out


def test_exhausting_the_limit_exactly_at_the_end_is_still_a_full_proof() -> None:
    s = spec(
        {"x": {"type": "integer", "min": 0, "max": 9, "initial": 0}},
        [{"id": "t", "when": "x < 9", "updates": {"x": "x + 1"}}],
        [{"id": "p", "type": "invariant", "expression": "x >= 0"}],
    )
    assert (
        verify(s.system, s.properties, limits=Limits(max_states=10)).results[0].status
        is Status.HOLDS
    )
    assert (
        verify(s.system, s.properties, limits=Limits(max_states=9)).results[0].status
        is Status.UNKNOWN
    )


def test_duplicate_initial_values_in_the_product_do_not_duplicate_states() -> None:
    s = spec(
        {
            "a": {"type": "integer", "min": 0, "max": 1, "initial": [0, 1]},
            "b": {"type": "integer", "min": 0, "max": 0, "initial": 0},
        },
        [],
        [],
    )
    assert len(s.system.initial_states()) == 2


def test_a_deadlock_is_not_a_violation_of_an_invariant() -> None:
    s = spec(
        {"x": {"type": "integer", "min": 0, "max": 1, "initial": 0}},
        [{"id": "t", "when": "x == 0", "updates": {"x": "1"}}],
        [
            {"id": "inv", "type": "invariant", "expression": "x >= 0"},
            {"id": "dl", "type": "deadlock_free"},
        ],
    )
    run = verify(s.system, s.properties)
    assert [r.status for r in run.results] == [Status.HOLDS, Status.VIOLATED]


def test_unreachable_reachability_target_is_proven_only_by_exhaustion() -> None:
    s = spec(
        {"x": {"type": "integer", "min": 0, "max": 50, "initial": 0}},
        [{"id": "t", "when": "x < 50", "updates": {"x": "x + 1"}}],
        [{"id": "r", "type": "reachability", "target": "x == 40"}],
    )
    assert (
        verify(s.system, s.properties, limits=Limits(max_depth=10)).results[0].status
        is Status.UNKNOWN
    )
    assert verify(s.system, s.properties).results[0].status is Status.HOLDS


def test_eventually_is_not_the_same_as_reachable() -> None:
    # EF finds the target by search; AF must rule out every escaping path.
    s = spec(
        {"s": {"type": "enum", "values": ["a", "b", "c"], "initial": "a"}},
        [
            {"id": "ab", "when": 's == "a"', "updates": {"s": "b"}},
            {"id": "ac", "when": 's == "a"', "updates": {"s": "c"}},
        ],
        [
            {"id": "ef", "type": "ctl", "formula": 'EF {s == "b"}'},
            {"id": "af", "type": "ctl", "formula": 'AF {s == "b"}'},
        ],
    )
    ef, af = verify(s.system, s.properties).results
    assert ef.status is Status.HOLDS and af.status is Status.VIOLATED


def test_liveness_counterexample_is_an_infinite_path_not_a_prefix() -> None:
    s = spec(
        {"x": {"type": "integer", "min": 0, "max": 2, "initial": 0}},
        [{"id": "t", "updates": {"x": "(x + 1) % 3"}}],
        [{"id": "p", "type": "ctl", "formula": "AF {x == 7}"}],
    )
    trace = verify(s.system, s.properties).results[0].counterexample
    assert trace is not None and trace.is_lasso and trace.loop_start == 0


def test_a_terminated_run_is_a_stuttering_infinite_path() -> None:
    s = spec(
        {"x": {"type": "integer", "min": 0, "max": 1, "initial": 0}},
        [{"id": "t", "when": "x == 0", "updates": {"x": "1"}}],
        [
            {"id": "ag", "type": "ctl", "formula": "AG {x == 1}"},
            {"id": "af", "type": "ctl", "formula": "AF AG {x == 1}"},
        ],
    )
    ag, af = verify(s.system, s.properties).results
    assert ag.status is Status.VIOLATED
    assert af.status is Status.HOLDS  # the final state repeats forever


def test_unknown_properties_do_not_leave_stale_counterexamples() -> None:
    s = spec(
        {"x": {"type": "integer", "min": 0, "max": 300, "initial": 0}},
        [{"id": "t", "when": "x < 300", "updates": {"x": "x + 1"}}],
        [{"id": "p", "type": "invariant", "expression": "x < 250"}],
    )
    result = verify(s.system, s.properties, limits=Limits(max_states=20)).results[0]
    assert result.status is Status.UNKNOWN and result.counterexample is None
