"""State indexing: assign each distinct state a dense integer id.

Two interchangeable strategies are provided so their cost can be measured:

* :class:`ObjectIndex` keys a dictionary by the ``State`` objects themselves.
* :class:`PackedIndex` keys it by the bare tuple of values and rebuilds
  ``State`` objects on demand, which stores less per state but pays a decode
  cost whenever a state is expanded.

Both give the same ids in the same order for the same exploration, so the
choice never changes a verification result.
"""

from __future__ import annotations

from typing import Protocol

from prooftrail.model.state import Scalar, State

INDEXING_KINDS = ("object", "packed")


class StateIndex(Protocol):
    def __len__(self) -> int: ...

    def lookup(self, state: State) -> int | None:
        """Id of ``state`` if it has been added, else ``None``."""

    def add(self, state: State) -> tuple[int, bool]:
        """Add ``state``; return ``(id, is_new)``."""

    def state(self, index: int) -> State:
        """Return the state with the given id."""


class ObjectIndex:
    """Index keyed by ``State`` objects."""

    kind = "object"

    def __init__(self) -> None:
        self._ids: dict[State, int] = {}
        self._states: list[State] = []

    def __len__(self) -> int:
        return len(self._states)

    def lookup(self, state: State) -> int | None:
        return self._ids.get(state)

    def add(self, state: State) -> tuple[int, bool]:
        existing = self._ids.get(state)
        if existing is not None:
            return existing, False
        index = len(self._states)
        self._ids[state] = index
        self._states.append(state)
        return index, True

    def state(self, index: int) -> State:
        return self._states[index]


class PackedIndex:
    """Index keyed by value tuples; ``State`` objects are rebuilt on demand."""

    kind = "packed"

    def __init__(self) -> None:
        self._ids: dict[tuple[Scalar, ...], int] = {}
        self._packed: list[tuple[Scalar, ...]] = []
        self._names: tuple[str, ...] | None = None

    def __len__(self) -> int:
        return len(self._packed)

    def _key(self, state: State) -> tuple[Scalar, ...]:
        if self._names is None:
            self._names = state.names
        return state.packed

    def lookup(self, state: State) -> int | None:
        return self._ids.get(self._key(state))

    def add(self, state: State) -> tuple[int, bool]:
        key = self._key(state)
        existing = self._ids.get(key)
        if existing is not None:
            return existing, False
        index = len(self._packed)
        self._ids[key] = index
        self._packed.append(key)
        return index, True

    def state(self, index: int) -> State:
        assert self._names is not None
        return State.from_packed(self._names, self._packed[index])


def make_index(kind: str) -> StateIndex:
    if kind == "object":
        return ObjectIndex()
    if kind == "packed":
        return PackedIndex()
    raise ValueError(f"Unknown indexing kind {kind!r}; choose from {INDEXING_KINDS}.")
