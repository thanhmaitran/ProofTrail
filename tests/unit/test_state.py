from __future__ import annotations

import pytest

from prooftrail.model.state import State


def test_state_is_independent_of_dictionary_order() -> None:
    first = State.from_dict({"counter": 2, "enabled": True})
    second = State.from_dict({"enabled": True, "counter": 2})
    assert first == second
    assert hash(first) == hash(second)


def test_state_can_be_used_in_a_set() -> None:
    first = State.from_dict({"counter": 2})
    second = State.from_dict({"counter": 2})
    assert len({first, second}) == 1


def test_duplicate_variable_names_are_rejected() -> None:
    with pytest.raises(ValueError, match="unique"):
        State((("counter", 1), ("counter", 2)))


def test_unknown_variable_raises_key_error() -> None:
    state = State.from_dict({"counter": 2})
    with pytest.raises(KeyError):
        state.get("missing")
    with pytest.raises(KeyError):
        state["missing"]


def test_empty_variable_name_is_rejected() -> None:
    with pytest.raises(ValueError, match="empty"):
        State((("", 1),))


@pytest.mark.parametrize("bad", [1.5, None, (1,), [1], b"x"])
def test_unsupported_value_types_are_rejected(bad: object) -> None:
    with pytest.raises(TypeError):
        State((("x", bad),))  # type: ignore[arg-type]


def test_unsorted_values_are_normalised() -> None:
    state = State((("b", 1), ("a", 2)))
    assert state.names == ("a", "b")
    assert state == State.from_dict({"a": 2, "b": 1})


def test_accessors() -> None:
    state = State.from_dict({"a": 1, "flag": True, "mode": "red"})
    assert state["a"] == 1 and state.get("flag") is True
    assert "mode" in state and "zzz" not in state
    assert list(state) == ["a", "flag", "mode"]
    assert len(state) == 3
    assert state.as_dict() == {"a": 1, "flag": True, "mode": "red"}
    assert str(state) == "a=1, flag=true, mode=red"


def test_with_updates_returns_a_new_state_and_rejects_unknown_names() -> None:
    state = State.from_dict({"x": 1, "y": 2})
    changed = state.with_updates({"x": 5})
    assert changed.as_dict() == {"x": 5, "y": 2}
    assert state.as_dict() == {"x": 1, "y": 2}
    with pytest.raises(KeyError):
        state.with_updates({"z": 1})


def test_packed_round_trip() -> None:
    state = State.from_dict({"x": 3, "b": False, "m": "g"})
    rebuilt = State.from_packed(state.names, state.packed)
    assert rebuilt == state and hash(rebuilt) == hash(state)
    assert rebuilt.as_dict() == state.as_dict()


def test_state_is_immutable() -> None:
    state = State.from_dict({"x": 1})
    with pytest.raises(AttributeError):
        state.values = ()  # type: ignore[misc]


def test_bool_and_int_values_stay_distinguishable_by_type() -> None:
    # True == 1 in Python, so State alone cannot tell them apart; the system
    # layer enforces one declared type per variable (see test_system).
    assert State.from_dict({"x": True}).get("x") is True
    assert type(State.from_dict({"x": 1}).get("x")) is int
