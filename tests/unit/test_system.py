"""Transition-system semantics: initial states, successors, domains, caching."""

from __future__ import annotations

import pytest
from support import system_from_dict

from prooftrail.errors import ModelError, SpecError
from prooftrail.model.expressions import BOOL, compile_expression
from prooftrail.model.state import State
from prooftrail.model.system import (
    STUTTER,
    CachedSystem,
    TransitionSystem,
    VariableDecl,
)
from prooftrail.model.transition import Transition


def counter_system(*, guard: str = "x < 3") -> TransitionSystem:
    return system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "c",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": 3, "initial": 0}
                },
                "transitions": [
                    {"id": "inc", "when": guard, "updates": {"x": "x + 1"}}
                ],
            },
        }
    )


def test_successors_of_a_chain() -> None:
    system = counter_system()
    state = State.from_dict({"x": 1})
    assert system.successors(state) == [("inc", State.from_dict({"x": 2}))]
    assert system.enabled_actions(state) == ["inc"]


def test_deadlock_has_no_successors_and_is_totalized_with_a_stutter() -> None:
    system = counter_system()
    end = State.from_dict({"x": 3})
    assert system.successors(end) == []
    assert system.is_deadlock(end)
    assert system.totalized_successors(end) == [(STUTTER, end)]
    assert not system.is_deadlock(State.from_dict({"x": 0}))


def test_successor_generation_is_deterministic() -> None:
    system = counter_system()
    state = State.from_dict({"x": 0})
    assert system.successors(state) == system.successors(state)


def test_updates_are_simultaneous() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "swap",
                "variables": {
                    "a": {"type": "integer", "min": 0, "max": 5, "initial": 1},
                    "b": {"type": "integer", "min": 0, "max": 5, "initial": 2},
                },
                "transitions": [{"id": "swap", "updates": {"a": "b", "b": "a"}}],
            },
        }
    )
    (initial,) = system.initial_states()
    ((_, after),) = system.successors(initial)
    assert after.as_dict() == {"a": 2, "b": 1}


def test_unmentioned_variables_keep_their_value() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {
                    "a": {"type": "boolean", "initial": True},
                    "b": {"type": "boolean", "initial": False},
                },
                "transitions": [{"id": "t", "updates": {"b": "true"}}],
            },
        }
    )
    (initial,) = system.initial_states()
    ((_, after),) = system.successors(initial)
    assert after.as_dict() == {"a": True, "b": True}


def test_two_actions_to_the_same_state_are_two_edges() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {"x": {"type": "boolean", "initial": False}},
                "transitions": [
                    {"id": "a", "updates": {"x": "true"}},
                    {"id": "b", "updates": {"x": "true"}},
                ],
            },
        }
    )
    (initial,) = system.initial_states()
    assert [a for a, _ in system.successors(initial)] == ["a", "b"]


def test_multiple_initial_values_form_a_product_without_duplicates() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {
                    "a": {"type": "integer", "min": 0, "max": 9, "initial": [0, 1]},
                    "b": {"type": "boolean", "initial": [False, True]},
                },
            },
        }
    )
    states = system.initial_states()
    assert len(states) == 4 == len(set(states))


def test_assigning_outside_the_domain_is_a_model_error() -> None:
    system = counter_system(guard="x <= 3")
    with pytest.raises(ModelError, match="outside its domain"):
        system.successors(State.from_dict({"x": 3}))


def test_division_by_zero_is_a_model_error_naming_the_transition() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": 5, "initial": 0}
                },
                "transitions": [{"id": "boom", "updates": {"x": "1 // x"}}],
            },
        }
    )
    with pytest.raises(ModelError, match="division by zero.*boom"):
        system.successors(State.from_dict({"x": 0}))


def test_validate_state_rejects_wrong_variables_and_values() -> None:
    system = counter_system()
    system.validate_state(State.from_dict({"x": 2}))
    with pytest.raises(ModelError, match="do not match"):
        system.validate_state(State.from_dict({"y": 2}))
    with pytest.raises(ModelError, match="outside its domain"):
        system.validate_state(State.from_dict({"x": 9}))
    with pytest.raises(ModelError, match="outside its domain"):
        system.validate_state(State.from_dict({"x": True}))


def test_labels_are_evaluated_per_state() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": 5, "initial": 0}
                },
                "labels": {"big": "x > 2"},
            },
        }
    )
    assert not system.label_holds("big", State.from_dict({"x": 1}))
    assert system.label_holds("big", State.from_dict({"x": 4}))


def test_accessors() -> None:
    system = counter_system()
    assert system.variable_names() == ["x"]
    assert system.action_ids() == ["inc"]
    assert system.variable("x").kind == "integer"
    assert "TransitionSystem" in repr(system)


# ------------------------------------------------------ direct construction


def decl(**kw) -> VariableDecl:
    base = {"name": "x", "kind": "integer", "initial": (0,), "minimum": 0, "maximum": 3}
    base.update(kw)
    return VariableDecl(**base)


@pytest.mark.parametrize(
    "variables, fragment",
    [
        ([], "at least one variable"),
        ([decl(), decl()], "unique"),
        ([decl(kind="float")], "unknown type"),
        ([decl(kind="enum", initial=("a",), minimum=None, maximum=None)], "distinct"),
        (
            [
                decl(
                    kind="enum",
                    initial=("a",),
                    minimum=None,
                    maximum=None,
                    values=("a", "a"),
                )
            ],
            "distinct",
        ),
        ([decl(values=("a",))], "Only enum"),
        (
            [decl(kind="boolean", initial=(True,), minimum=0, maximum=None)],
            "Only integer variables may have bounds",
        ),
        ([decl(initial=())], "no initial value"),
        ([decl(initial=(1, 1))], "duplicate initial"),
        ([decl(initial=(9,))], "outside its domain"),
        ([decl(initial=(True,))], "outside its domain"),
    ],
)
def test_direct_construction_validates_declarations(variables, fragment: str) -> None:
    with pytest.raises(SpecError, match=fragment):
        TransitionSystem("m", variables, [])


def test_transition_validation_catches_bad_references() -> None:
    scope = {"x": "int"}
    guard = compile_expression("x < 3", scope, expected=BOOL)
    update = compile_expression("x + 1", scope)
    with pytest.raises(SpecError, match="unknown variable"):
        TransitionSystem("m", [decl()], [Transition("t", guard, (("nope", update),))])
    with pytest.raises(SpecError, match="more than once"):
        TransitionSystem(
            "m", [decl()], [Transition("t", guard, (("x", update), ("x", update)))]
        )
    with pytest.raises(SpecError, match="must be bool"):
        TransitionSystem("m", [decl()], [Transition("t", update)])
    other_scope = {"y": "int"}
    bad_guard = compile_expression("y < 1", other_scope, expected=BOOL)
    with pytest.raises(SpecError, match="unknown variable"):
        TransitionSystem("m", [decl()], [Transition("t", bad_guard)])


# ------------------------------------------------------------------- cache


def test_cached_system_matches_the_plain_system_and_counts_hits() -> None:
    system = counter_system()
    cached = CachedSystem(system)
    state = State.from_dict({"x": 0})
    assert cached.successors(state) == system.successors(state)
    assert (cached.hits, cached.misses, len(cached)) == (0, 1, 1)
    cached.successors(state)
    assert (cached.hits, cached.misses) == (1, 1)
    end = State.from_dict({"x": 3})
    assert cached.totalized_successors(end) == [(STUTTER, end)]
    assert cached.initial_states() == system.initial_states()
    assert cached.id == system.id  # attribute delegation
    cached.clear()
    assert (cached.hits, cached.misses, len(cached)) == (0, 0, 0)
