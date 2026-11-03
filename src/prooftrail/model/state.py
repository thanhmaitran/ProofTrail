from __future__ import annotations

from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field

type Scalar = int | bool | str


@dataclass(frozen=True, slots=True)
class State:
    """An immutable state represented by named scalar variables.

    ``values`` is a tuple of ``(name, value)`` pairs kept sorted by name, so two
    states with the same variable values are equal and hash identically
    regardless of the order in which the variables were supplied.

    Note on ``bool`` versus ``int``: ``True == 1`` in Python, so a ``State``
    alone cannot tell them apart. A ``TransitionSystem`` enforces exactly one
    declared type per variable and checks every state it produces, so the two
    can never be confused inside a model.
    """

    values: tuple[tuple[str, Scalar], ...]
    _lookup: dict[str, Scalar] = field(
        init=False, repr=False, compare=False, hash=False
    )

    def __post_init__(self) -> None:
        names = [name for name, _ in self.values]
        if len(names) != len(set(names)):
            raise ValueError("State variable names must be unique.")
        if any(not name for name in names):
            raise ValueError("State variable names must not be empty.")

        # bool is a subclass of int in Python, so validate types explicitly.
        for name, value in self.values:
            if type(value) not in (int, bool, str):
                raise TypeError(f"Unsupported value type for variable {name!r}.")

        if names != sorted(names):
            object.__setattr__(self, "values", tuple(sorted(self.values)))
        object.__setattr__(self, "_lookup", dict(self.values))

    # ------------------------------------------------------------------ build

    @classmethod
    def from_dict(cls, values: Mapping[str, Scalar]) -> State:
        return cls(tuple(sorted(values.items())))

    @classmethod
    def from_packed(cls, names: tuple[str, ...], packed: tuple[Scalar, ...]) -> State:
        """Rebuild a state from :attr:`packed` without re-validating.

        ``names`` must be the sorted variable names the packed tuple was made
        with. This is the trusted fast path used by the compact state index.
        """
        obj = object.__new__(cls)
        object.__setattr__(obj, "values", tuple(zip(names, packed, strict=True)))
        object.__setattr__(obj, "_lookup", dict(zip(names, packed, strict=True)))
        return obj

    # ----------------------------------------------------------------- access

    def as_dict(self) -> dict[str, Scalar]:
        return dict(self._lookup)

    def get(self, name: str) -> Scalar:
        """Return the value of ``name``; raise ``KeyError`` if it is unknown.

        Unlike ``dict.get`` there is deliberately no default: asking a state
        for a variable it does not have is always a bug.
        """
        return self._lookup[name]

    def __getitem__(self, name: str) -> Scalar:
        return self._lookup[name]

    def __contains__(self, name: object) -> bool:
        return name in self._lookup

    def __iter__(self) -> Iterator[str]:
        return iter(self._lookup)

    def __len__(self) -> int:
        return len(self.values)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(name for name, _ in self.values)

    @property
    def packed(self) -> tuple[Scalar, ...]:
        """Just the values, in sorted-name order (a compact hashable key)."""
        return tuple(value for _, value in self.values)

    # ----------------------------------------------------------------- derive

    def with_updates(self, updates: Mapping[str, Scalar]) -> State:
        """Return a new state with ``updates`` applied; unknown names raise."""
        merged = dict(self._lookup)
        for name, value in updates.items():
            if name not in merged:
                raise KeyError(name)
            merged[name] = value
        return State(tuple(sorted(merged.items())))

    def __str__(self) -> str:
        return ", ".join(
            f"{name}={_format_value(value)}" for name, value in self.values
        )


def _format_value(value: Scalar) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
