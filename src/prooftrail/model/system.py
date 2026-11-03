from __future__ import annotations

import itertools
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from prooftrail.errors import ModelError, SpecError
from prooftrail.model.expressions import BOOL, INT, STR, Expression
from prooftrail.model.state import Scalar, State
from prooftrail.model.transition import Transition

INTEGER_KIND = "integer"
BOOLEAN_KIND = "boolean"
ENUM_KIND = "enum"
VARIABLE_KINDS = (INTEGER_KIND, BOOLEAN_KIND, ENUM_KIND)

# Action name used for the implicit self-loop that totalizes deadlock states
# when temporal properties are evaluated. Real action ids can never start with
# "<", so there is no clash.
STUTTER = "<stutter>"

MAX_INITIAL_STATES = 100_000

_KIND_TO_EXPR_TYPE = {INTEGER_KIND: INT, BOOLEAN_KIND: BOOL, ENUM_KIND: STR}


@dataclass(frozen=True)
class VariableDecl:
    """Declaration of one state variable and its finite or bounded domain."""

    name: str
    kind: str
    initial: tuple[Scalar, ...]
    minimum: int | None = None
    maximum: int | None = None
    values: tuple[str, ...] = ()

    @property
    def expr_type(self) -> str:
        return _KIND_TO_EXPR_TYPE[self.kind]

    def contains(self, value: object) -> bool:
        """Is ``value`` a legal value of this variable?"""
        if self.kind == BOOLEAN_KIND:
            return type(value) is bool
        if self.kind == ENUM_KIND:
            return type(value) is str and value in self.values
        if type(value) is not int:
            return False
        if self.minimum is not None and value < self.minimum:
            return False
        return self.maximum is None or value <= self.maximum

    def describe_domain(self) -> str:
        if self.kind == BOOLEAN_KIND:
            return "boolean"
        if self.kind == ENUM_KIND:
            return "enum {" + ", ".join(self.values) + "}"
        low = "-inf" if self.minimum is None else str(self.minimum)
        high = "+inf" if self.maximum is None else str(self.maximum)
        return f"integer in [{low}, {high}]"


class TransitionSystem:
    """A finite (or finitely explorable) transition system.

    This realises the tuple ``M = (S, S0, A, T, L)``: the variable declarations
    define ``S``, ``initial_states()`` yields ``S0``, transitions define ``A`` and
    ``T``, and ``labels`` give the atomic propositions ``L``.
    """

    def __init__(
        self,
        id: str,
        variables: Sequence[VariableDecl],
        transitions: Sequence[Transition],
        labels: Mapping[str, Expression] | None = None,
        description: str = "",
    ) -> None:
        self.id = id
        self.description = description
        self.variables = tuple(variables)
        self.transitions = tuple(transitions)
        self.labels: dict[str, Expression] = dict(labels or {})
        self._decl = {decl.name: decl for decl in self.variables}
        self.validate()

    # ------------------------------------------------------------- validation

    def validate(self) -> None:
        """Check the model is well formed; raise :class:`SpecError` if not."""
        if not self.variables:
            raise SpecError("A model needs at least one variable.")
        if len({d.name for d in self.variables}) != len(self.variables):
            raise SpecError("Variable names must be unique.")
        for decl in self.variables:
            self._validate_variable(decl)

        ids = [t.id for t in self.transitions]
        if len(ids) != len(set(ids)):
            raise SpecError("Transition ids must be unique.")
        for transition in self.transitions:
            self._validate_transition(transition)
        for name, expr in self.labels.items():
            self._validate_expression(expr, BOOL, f"label {name!r}")

        count = 1
        for decl in self.variables:
            count *= len(decl.initial)
            if count > MAX_INITIAL_STATES:
                raise SpecError(
                    f"The model has more than {MAX_INITIAL_STATES} initial states."
                )

    def _validate_variable(self, decl: VariableDecl) -> None:
        if decl.kind not in VARIABLE_KINDS:
            raise SpecError(f"Variable {decl.name!r} has unknown type {decl.kind!r}.")
        if decl.kind == ENUM_KIND:
            if not decl.values or len(set(decl.values)) != len(decl.values):
                raise SpecError(
                    f"Enum variable {decl.name!r} needs distinct, non-empty values."
                )
        elif decl.values:
            raise SpecError(f"Only enum variables may declare values ({decl.name!r}).")
        if decl.kind != INTEGER_KIND and (
            decl.minimum is not None or decl.maximum is not None
        ):
            raise SpecError(f"Only integer variables may have bounds ({decl.name!r}).")
        if (
            decl.minimum is not None
            and decl.maximum is not None
            and decl.minimum > decl.maximum
        ):
            raise SpecError(f"Variable {decl.name!r} has min greater than max.")
        if not decl.initial:
            raise SpecError(f"Variable {decl.name!r} has no initial value.")
        if len(set(decl.initial)) != len(decl.initial):
            raise SpecError(f"Variable {decl.name!r} lists duplicate initial values.")
        for value in decl.initial:
            if not decl.contains(value):
                raise SpecError(
                    f"Initial value {value!r} of {decl.name!r} is outside its "
                    f"domain ({decl.describe_domain()})."
                )

    def _validate_expression(self, expr: Expression, expected: str, what: str) -> None:
        unknown = expr.names - self._decl.keys()
        if unknown:
            raise SpecError(f"{what} uses unknown variable(s): {sorted(unknown)}.")
        if expr.type != expected:
            raise SpecError(f"{what} must be {expected}, got {expr.type}.")

    def _validate_transition(self, transition: Transition) -> None:
        where = f"transition {transition.id!r}"
        self._validate_expression(transition.guard, BOOL, f"{where} guard")
        seen: set[str] = set()
        for name, expr in transition.updates:
            decl = self._decl.get(name)
            if decl is None:
                raise SpecError(f"{where} updates unknown variable {name!r}.")
            if name in seen:
                raise SpecError(f"{where} updates {name!r} more than once.")
            seen.add(name)
            self._validate_expression(
                expr, decl.expr_type, f"{where} update of {name!r}"
            )

    def validate_state(self, state: State) -> None:
        """Check ``state`` has exactly the declared variables with legal values."""
        if set(state.names) != self._decl.keys():
            raise ModelError(
                f"State variables {sorted(state.names)} do not match the model's "
                f"variables {sorted(self._decl)}."
            )
        for name, value in state.values:
            decl = self._decl[name]
            if not decl.contains(value):
                raise ModelError(
                    f"Value {value!r} of {name!r} is outside its domain "
                    f"({decl.describe_domain()})."
                )

    # ------------------------------------------------------------ semantics

    def initial_states(self) -> list[State]:
        """All initial states, deduplicated, in a deterministic order."""
        names = [d.name for d in self.variables]
        seen: dict[State, None] = {}
        for combo in itertools.product(*(d.initial for d in self.variables)):
            seen.setdefault(State.from_dict(dict(zip(names, combo, strict=True))))
        return list(seen)

    def enabled_actions(self, state: State) -> list[str]:
        """Ids of the transitions enabled in ``state`` (declaration order)."""
        return [t.id for t in self.transitions if t.enabled(state)]

    def is_deadlock(self, state: State) -> bool:
        """True if no transition is enabled in ``state``."""
        return not any(t.enabled(state) for t in self.transitions)

    def successors(self, state: State) -> list[tuple[str, State]]:
        """Every ``(action id, next state)`` pair enabled in ``state``.

        Edges are returned in transition declaration order. Two different
        actions that lead to the same state both appear. Generation is
        deterministic: the same state always yields the same list.
        """
        result: list[tuple[str, State]] = []
        for transition in self.transitions:
            try:
                if not transition.enabled(state):
                    continue
                updates = transition.evaluate_updates(state)
            except ModelError as exc:
                raise ModelError(
                    f"{exc} [transition {transition.id!r}, state: {state}]"
                ) from None
            for name, value in updates.items():
                decl = self._decl[name]
                if not decl.contains(value):
                    raise ModelError(
                        f"Transition {transition.id!r} assigns {value!r} to "
                        f"{name!r}, outside its domain ({decl.describe_domain()}), "
                        f"in state: {state}"
                    )
            result.append(
                (transition.id, state.with_updates(updates) if updates else state)
            )
        return result

    def totalized_successors(self, state: State) -> list[tuple[str, State]]:
        """Like :meth:`successors`, but a deadlock state gets a stutter self-loop."""
        result = self.successors(state)
        return result or [(STUTTER, state)]

    def label_holds(self, name: str, state: State) -> bool:
        return bool(self.labels[name].evaluate(state))

    def variable(self, name: str) -> VariableDecl:
        return self._decl[name]

    def variable_names(self) -> list[str]:
        return [d.name for d in self.variables]

    def action_ids(self) -> list[str]:
        return [t.id for t in self.transitions]

    def __repr__(self) -> str:
        return (
            f"TransitionSystem(id={self.id!r}, variables={len(self.variables)}, "
            f"transitions={len(self.transitions)})"
        )


class CachedSystem:
    """A memoising wrapper around a :class:`TransitionSystem`.

    Successor lists are cached per state, so repeated explorations (for example
    one per property) skip re-evaluating guards and updates. The cache holds
    every state it has seen, so it trades memory for time; see the benchmarks.
    """

    def __init__(self, system: TransitionSystem) -> None:
        self._system = system
        self._cache: dict[State, list[tuple[str, State]]] = {}
        self.hits = 0
        self.misses = 0

    def __getattr__(self, name: str) -> object:
        return getattr(self._system, name)

    def successors(self, state: State) -> list[tuple[str, State]]:
        cached = self._cache.get(state)
        if cached is not None:
            self.hits += 1
            return cached
        self.misses += 1
        result = self._system.successors(state)
        self._cache[state] = result
        return result

    def totalized_successors(self, state: State) -> list[tuple[str, State]]:
        return self.successors(state) or [(STUTTER, state)]

    def initial_states(self) -> list[State]:
        return self._system.initial_states()

    def clear(self) -> None:
        self._cache.clear()
        self.hits = 0
        self.misses = 0

    def __len__(self) -> int:
        return len(self._cache)
