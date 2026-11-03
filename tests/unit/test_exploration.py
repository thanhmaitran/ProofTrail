"""Breadth-first exploration, limits, indexing and predecessor reconstruction."""

from __future__ import annotations

import pytest
from support import system_from_dict

from prooftrail.exploration.bfs import (
    STOP_EARLY,
    STOP_ERROR,
    STOP_MAX_DEPTH,
    STOP_MAX_STATES,
    STOP_TIMEOUT,
    Limits,
    explore,
)
from prooftrail.exploration.indexing import (
    INDEXING_KINDS,
    ObjectIndex,
    PackedIndex,
    make_index,
)
from prooftrail.exploration.predecessor import path_to
from prooftrail.model.state import State


def chain(n: int, *, loop: bool = False):
    guard = "true" if loop else f"x < {n - 1}"
    update = f"(x + 1) % {n}" if loop else "x + 1"
    return system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "chain",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": n - 1, "initial": 0}
                },
                "transitions": [
                    {"id": "step", "when": guard, "updates": {"x": update}}
                ],
            },
        }
    )


def test_explores_every_reachable_state_of_a_chain() -> None:
    result = explore(chain(6))
    assert result.complete and result.stop_reason is None and result.exhaustive
    assert result.stats.states_explored == 6
    assert result.stats.transitions_examined == 5
    assert result.stats.max_depth_reached == 5
    assert result.stats.deadlock_states == 1
    assert result.depth == [0, 1, 2, 3, 4, 5]


def test_a_cycle_terminates() -> None:
    result = explore(chain(5, loop=True))
    assert result.complete
    assert result.stats.states_explored == 5
    assert result.stats.transitions_examined == 5
    assert result.stats.deadlock_states == 0


def test_discovery_order_is_breadth_first() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "tree",
                "variables": {
                    "n": {"type": "integer", "min": 0, "max": 20, "initial": 1}
                },
                "transitions": [
                    {"id": "l", "when": "n < 8", "updates": {"n": "2 * n"}},
                    {"id": "r", "when": "n < 8", "updates": {"n": "2 * n + 1"}},
                ],
            },
        }
    )
    result = explore(system)
    assert result.depth == sorted(result.depth)


def test_path_to_gives_a_shortest_path() -> None:
    result = explore(chain(5))
    steps = path_to(result, 4)
    assert [action for _, action in steps] == [None, "step", "step", "step", "step"]
    assert [index for index, _ in steps] == [0, 1, 2, 3, 4]


def test_path_to_needs_parent_tracking() -> None:
    result = explore(chain(3), track_parents=False)
    with pytest.raises(ValueError):
        path_to(result, 2)


def test_max_states_stops_with_an_incomplete_exploration() -> None:
    result = explore(chain(10), limits=Limits(max_states=4))
    assert not result.complete and not result.exhaustive
    assert result.stop_reason == STOP_MAX_STATES
    assert result.stats.states_explored == 4


def test_max_states_exactly_equal_to_the_state_space_still_completes() -> None:
    result = explore(chain(10), limits=Limits(max_states=10))
    assert result.complete and result.stats.states_explored == 10


def test_max_depth_truncates_and_reports_it() -> None:
    result = explore(chain(10), limits=Limits(max_depth=3))
    assert not result.complete
    assert result.stop_reason == STOP_MAX_DEPTH
    assert result.stats.states_explored == 4


def test_max_depth_beyond_the_diameter_is_complete() -> None:
    result = explore(chain(4), limits=Limits(max_depth=3))
    assert result.complete


def test_timeout_stops_the_search() -> None:
    result = explore(chain(2000, loop=True), limits=Limits(timeout_seconds=1e-9))
    assert not result.complete
    assert result.stop_reason == STOP_TIMEOUT


def test_callbacks_can_stop_the_search_early() -> None:
    seen: list[int] = []

    def on_discover(index: int, state: State) -> bool:
        seen.append(index)
        return state["x"] == 2

    result = explore(chain(10), on_discover=on_discover)
    assert result.stop_reason == STOP_EARLY and not result.complete
    assert seen == [0, 1, 2]

    expanded: list[int] = []
    result = explore(
        chain(10), on_expand=lambda i, s, succ: expanded.append(i) or i == 1
    )
    assert result.stop_reason == STOP_EARLY
    assert expanded == [0, 1]


def test_initial_states_are_reported_to_on_discover() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {"b": {"type": "boolean", "initial": [False, True]}},
            },
        }
    )
    found: list[State] = []
    result = explore(system, on_discover=lambda i, s: found.append(s) or False)
    assert len(found) == 2 and result.initial == [0, 1]


def test_max_states_smaller_than_the_initial_states() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {"b": {"type": "boolean", "initial": [False, True]}},
            },
        }
    )
    result = explore(system, limits=Limits(max_states=1))
    assert result.stop_reason == STOP_MAX_STATES and result.stats.states_explored == 1


def test_a_model_error_is_captured_not_raised() -> None:
    system = system_from_dict(
        {
            "schema_version": "1.0",
            "model": {
                "id": "m",
                "variables": {
                    "x": {"type": "integer", "min": 0, "max": 2, "initial": 0}
                },
                "transitions": [{"id": "t", "updates": {"x": "x + 1"}}],
            },
        }
    )
    result = explore(system)
    assert result.stop_reason == STOP_ERROR and not result.complete
    assert result.error is not None and "outside its domain" in str(result.error)
    assert result.stats.states_explored == 3


def test_edges_are_stored_on_request_and_include_back_edges() -> None:
    result = explore(chain(3, loop=True), store_edges=True)
    assert result.edges == [[("step", 1)], [("step", 2)], [("step", 0)]]
    assert explore(chain(3)).edges is None


def test_stats_dictionary() -> None:
    stats = explore(chain(3)).stats.to_dict(include_timing=False)
    assert "elapsed_seconds" not in stats
    assert stats["states_explored"] == 3
    assert "elapsed_seconds" in explore(chain(3)).stats.to_dict()


def test_limits_to_dict() -> None:
    assert Limits(5, 2, 1.5).to_dict() == {
        "max_states": 5,
        "max_depth": 2,
        "timeout_seconds": 1.5,
    }


# ------------------------------------------------------------------ indexing


@pytest.mark.parametrize("kind", INDEXING_KINDS)
def test_index_assigns_dense_stable_ids(kind: str) -> None:
    index = make_index(kind)
    a, b = State.from_dict({"x": 1, "f": True}), State.from_dict({"x": 2, "f": True})
    assert index.add(a) == (0, True)
    assert index.add(b) == (1, True)
    assert index.add(State.from_dict({"f": True, "x": 1})) == (0, False)
    assert len(index) == 2
    assert index.lookup(b) == 1
    assert index.lookup(State.from_dict({"x": 9, "f": True})) is None
    assert index.state(0) == a and index.state(1) == b


def test_unknown_indexing_kind() -> None:
    with pytest.raises(ValueError, match="Unknown indexing"):
        make_index("bloom")
    assert isinstance(make_index("object"), ObjectIndex)
    assert isinstance(make_index("packed"), PackedIndex)


@pytest.mark.parametrize("system", [chain(7), chain(6, loop=True)])
def test_indexing_kinds_explore_identically(system) -> None:
    a = explore(system, indexing="object", store_edges=True)
    b = explore(system, indexing="packed", store_edges=True)
    assert [a.state(i) for i in range(len(a.index))] == [
        b.state(i) for i in range(len(b.index))
    ]
    assert a.edges == b.edges and a.depth == b.depth
    assert a.parent == b.parent and a.parent_action == b.parent_action
