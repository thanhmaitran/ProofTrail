"""CTL: syntax, parser and a fixed-point model checker.

Syntax (atoms are state expressions in braces, or model labels)::

    formula  := implies
    implies  := or ('->' implies)?                    right associative
    or       := and ('||' and)*
    and      := unary ('&&' unary)*
    unary    := '!' unary
              | ('EX'|'AX'|'EF'|'AF'|'EG'|'AG') unary
              | 'E' '[' formula 'U' formula ']'
              | 'A' '[' formula 'U' formula ']'
              | '(' formula ')'
              | '{' expression '}'                    atomic proposition
              | label                                 declared model label
              | 'true' | 'false'

Example: ``AG ({request} -> AF {grant})`` or ``AG EF {counter == 0}``.

Semantics are defined over the *totalized* graph in which every deadlock state
has an implicit self-loop (see ``docs/formal-semantics.md``), so every path is
infinite and ``AF``/``AG``/``EG`` are well defined. No fairness assumption is
made: ``AF p`` fails if some path (including one where the scheduler never
picks a particular action) avoids ``p`` forever.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import ClassVar

from prooftrail.errors import ExpressionError, SpecError
from prooftrail.model.expressions import BOOL, Expression, compile_expression
from prooftrail.properties.base import Property

MAX_FORMULA_LENGTH = 2_000
MAX_FORMULA_DEPTH = 60

KEYWORDS = frozenset(
    {"E", "A", "U", "EX", "AX", "EF", "AF", "EG", "AG", "true", "false"}
)

# ---------------------------------------------------------------------- AST


class Formula:
    """Base class of CTL formula nodes."""

    __slots__ = ()


@dataclass(frozen=True, slots=True)
class Atom(Formula):
    expr: Expression


@dataclass(frozen=True, slots=True)
class Label(Formula):
    name: str
    expr: Expression


@dataclass(frozen=True, slots=True)
class Const(Formula):
    value: bool


@dataclass(frozen=True, slots=True)
class Not(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class And(Formula):
    left: Formula
    right: Formula


@dataclass(frozen=True, slots=True)
class Or(Formula):
    left: Formula
    right: Formula


@dataclass(frozen=True, slots=True)
class Implies(Formula):
    left: Formula
    right: Formula


@dataclass(frozen=True, slots=True)
class EX(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class AX(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class EF(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class AF(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class EG(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class AG(Formula):
    arg: Formula


@dataclass(frozen=True, slots=True)
class EU(Formula):
    left: Formula
    right: Formula


@dataclass(frozen=True, slots=True)
class AU(Formula):
    left: Formula
    right: Formula


_UNARY_TEMPORAL = {"EX": EX, "AX": AX, "EF": EF, "AF": AF, "EG": EG, "AG": AG}


def to_string(formula: Formula) -> str:
    """Render a formula back to (fully parenthesised) concrete syntax."""
    match formula:
        case Atom(expr):
            return "{" + expr.source + "}"
        case Label(name, _):
            return name
        case Const(value):
            return "true" if value else "false"
        case Not(arg):
            return f"!{to_string(arg)}"
        case And(left, right):
            return f"({to_string(left)} && {to_string(right)})"
        case Or(left, right):
            return f"({to_string(left)} || {to_string(right)})"
        case Implies(left, right):
            return f"({to_string(left)} -> {to_string(right)})"
        case EU(left, right):
            return f"E[{to_string(left)} U {to_string(right)}]"
        case AU(left, right):
            return f"A[{to_string(left)} U {to_string(right)}]"
        case EX(arg) | AX(arg) | EF(arg) | AF(arg) | EG(arg) | AG(arg):
            return f"{type(formula).__name__} {to_string(arg)}"
    raise TypeError(f"Unknown formula node {formula!r}")


# ------------------------------------------------------------------- parser

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<atom>\{[^{}]*\})
      | (?P<arrow>->)
      | (?P<and>&&)
      | (?P<or>\|\|)
      | (?P<not>!)
      | (?P<punct>[()\[\]])
      | (?P<ident>[A-Za-z_][A-Za-z0-9_]*)
    )""",
    re.VERBOSE,
)


@dataclass(frozen=True, slots=True)
class _Token:
    kind: str
    text: str
    pos: int


def _tokenize(text: str, context: str) -> list[_Token]:
    tokens: list[_Token] = []
    pos = 0
    while pos < len(text):
        if text[pos:].strip() == "":
            break
        match = _TOKEN.match(text, pos)
        if match is None or match.end() == pos:
            snippet = text[pos : pos + 12].strip()
            raise ExpressionError(
                f"{context}: unexpected input {snippet!r} at position {pos} "
                f"in formula {text!r}."
            )
        kind = match.lastgroup or ""
        tokens.append(_Token(kind, match.group(kind), match.start(kind)))
        pos = match.end()
    return tokens


class _Parser:
    def __init__(
        self,
        text: str,
        scope: Mapping[str, str],
        labels: Mapping[str, Expression],
        context: str,
    ) -> None:
        self.text = text
        self.scope = scope
        self.labels = labels
        self.context = context
        self.tokens = _tokenize(text, context)
        self.i = 0
        self.depth = 0

    def fail(self, message: str) -> ExpressionError:
        return ExpressionError(f"{self.context}: {message} in formula {self.text!r}.")

    def peek(self) -> _Token | None:
        return self.tokens[self.i] if self.i < len(self.tokens) else None

    def take(self) -> _Token:
        token = self.peek()
        if token is None:
            raise self.fail("unexpected end of formula")
        self.i += 1
        return token

    def expect(self, kind: str, text: str | None = None) -> _Token:
        token = self.take()
        if token.kind != kind or (text is not None and token.text != text):
            wanted = text or kind
            raise self.fail(f"expected {wanted!r} but found {token.text!r}")
        return token

    def parse(self) -> Formula:
        if not self.tokens:
            raise self.fail("formula is empty")
        formula = self.implies()
        extra = self.peek()
        if extra is not None:
            raise self.fail(f"unexpected {extra.text!r}")
        return formula

    def enter(self) -> None:
        self.depth += 1
        if self.depth > MAX_FORMULA_DEPTH:
            raise self.fail(f"formula is nested deeper than {MAX_FORMULA_DEPTH} levels")

    def leave(self) -> None:
        self.depth -= 1

    def implies(self) -> Formula:
        self.enter()
        left = self.disjunction()
        token = self.peek()
        if token is not None and token.kind == "arrow":
            self.take()
            left = Implies(left, self.implies())
        self.leave()
        return left

    def disjunction(self) -> Formula:
        left = self.conjunction()
        while (token := self.peek()) is not None and token.kind == "or":
            self.take()
            left = Or(left, self.conjunction())
        return left

    def conjunction(self) -> Formula:
        left = self.unary()
        while (token := self.peek()) is not None and token.kind == "and":
            self.take()
            left = And(left, self.unary())
        return left

    def unary(self) -> Formula:
        self.enter()
        try:
            token = self.take()
            if token.kind == "not":
                return Not(self.unary())
            if token.kind == "punct" and token.text == "(":
                inner = self.implies()
                self.expect("punct", ")")
                return inner
            if token.kind == "atom":
                inner_text = token.text[1:-1]
                expr = compile_expression(
                    inner_text, self.scope, expected=BOOL, context=self.context
                )
                return Atom(expr)
            if token.kind == "ident":
                return self.identifier(token)
            raise self.fail(f"unexpected {token.text!r}")
        finally:
            self.leave()

    def identifier(self, token: _Token) -> Formula:
        name = token.text
        if name in _UNARY_TEMPORAL:
            return _UNARY_TEMPORAL[name](self.unary())
        if name in ("E", "A"):
            self.expect("punct", "[")
            left = self.implies()
            self.expect("ident", "U")
            right = self.implies()
            self.expect("punct", "]")
            return EU(left, right) if name == "E" else AU(left, right)
        if name == "true":
            return Const(True)
        if name == "false":
            return Const(False)
        if name in self.labels:
            return Label(name, self.labels[name])
        known = ", ".join(sorted(self.labels)) or "none"
        raise self.fail(
            f"unknown identifier {name!r}; write state conditions in braces such "
            f"as {{x == 1}} (declared labels: {known})"
        )


def parse_ctl(
    text: object,
    scope: Mapping[str, str],
    labels: Mapping[str, Expression] | None = None,
    *,
    context: str = "formula",
) -> Formula:
    """Parse a CTL formula; atoms are checked against ``scope`` (variable types)."""
    if not isinstance(text, str):
        raise ExpressionError(f"{context}: expected a string formula.")
    if len(text) > MAX_FORMULA_LENGTH:
        raise ExpressionError(
            f"{context}: formula is longer than {MAX_FORMULA_LENGTH} characters."
        )
    try:
        return _Parser(text, scope, labels or {}, context).parse()
    except RecursionError:
        raise ExpressionError(f"{context}: formula is nested too deeply.") from None


def is_propositional(formula: Formula) -> bool:
    """True if the formula contains no temporal operator."""
    match formula:
        case Atom() | Label() | Const():
            return True
        case Not(arg):
            return is_propositional(arg)
        case And(a, b) | Or(a, b) | Implies(a, b):
            return is_propositional(a) and is_propositional(b)
    return False


def evaluate_propositional(formula: Formula, state: object) -> bool:
    """Evaluate a temporal-operator-free formula in a single state."""
    match formula:
        case Atom(expr) | Label(_, expr):
            return bool(expr.evaluate(state))  # type: ignore[arg-type]
        case Const(value):
            return value
        case Not(arg):
            return not evaluate_propositional(arg, state)
        case And(a, b):
            return evaluate_propositional(a, state) and evaluate_propositional(b, state)
        case Or(a, b):
            return evaluate_propositional(a, state) or evaluate_propositional(b, state)
        case Implies(a, b):
            return (not evaluate_propositional(a, state)) or evaluate_propositional(
                b, state
            )
    raise ValueError("formula contains a temporal operator")


# -------------------------------------------------------------- properties


@dataclass(frozen=True)
class CTLProperty(Property):
    """A CTL formula that must hold in every initial state."""

    formula: Formula = None  # type: ignore[assignment]
    source: str = ""

    kind: ClassVar[str] = "ctl"

    @property
    def needs_graph(self) -> bool:
        return True


@dataclass(frozen=True)
class ResponseProperty(Property):
    """``AG (trigger -> AF response)``: every trigger is eventually answered.

    Without fairness assumptions. A violation is a lasso: a path to a state
    where ``trigger`` holds, followed by an infinite continuation in which
    ``response`` never becomes true.
    """

    trigger: Expression = None  # type: ignore[assignment]
    response: Expression = None  # type: ignore[assignment]

    kind: ClassVar[str] = "response"

    @property
    def needs_graph(self) -> bool:
        return True

    @property
    def formula(self) -> Formula:
        return AG(Implies(Atom(self.trigger), AF(Atom(self.response))))


# ------------------------------------------------------------ model checker


class TotalGraph:
    """The explored graph with deadlock states totalized by stutter self-loops.

    ``edges[i]`` lists ``(action, j)`` pairs; ``pred[j]`` lists predecessor
    indices with one entry per edge (so multi-edges are counted consistently).
    """

    def __init__(
        self,
        states: list,
        raw_edges: list[list[tuple[str, int]]],
        initial: list[int],
        stutter: str,
    ) -> None:
        self.states = states
        self.initial = initial
        n = len(states)
        self.edges: list[list[tuple[str, int]]] = []
        for i in range(n):
            out = raw_edges[i] or [(stutter, i)]
            self.edges.append(out)
        self.succ: list[list[int]] = [[j for _, j in out] for out in self.edges]
        self.pred: list[list[int]] = [[] for _ in range(n)]
        for i, targets in enumerate(self.succ):
            for j in targets:
                self.pred[j].append(i)

    def __len__(self) -> int:
        return len(self.states)


class CTLChecker:
    """Computes ``Sat(formula)``: the set of states satisfying a formula.

    Every temporal operator is a least or greatest fixed point evaluated with
    a worklist over predecessor lists, so each operator costs ``O(|V| + |E|)``.
    """

    def __init__(self, graph: TotalGraph) -> None:
        self.graph = graph
        self.all = set(range(len(graph)))
        self._memo: dict[Formula, frozenset[int]] = {}

    def sat(self, formula: Formula) -> frozenset[int]:
        cached = self._memo.get(formula)
        if cached is None:
            cached = frozenset(self._compute(formula))
            self._memo[formula] = cached
        return cached

    def holds_initially(self, formula: Formula) -> bool:
        return set(self.graph.initial) <= self.sat(formula)

    # -- operators ---------------------------------------------------------

    def _compute(self, f: Formula) -> set[int] | frozenset[int]:
        graph = self.graph
        match f:
            case Atom(expr) | Label(_, expr):
                states = graph.states
                return {i for i in range(len(states)) if expr.evaluate(states[i])}
            case Const(value):
                return set(self.all) if value else set()
            case Not(arg):
                return self.all - self.sat(arg)
            case And(a, b):
                return set(self.sat(a)) & self.sat(b)
            case Or(a, b):
                return set(self.sat(a)) | self.sat(b)
            case Implies(a, b):
                return (self.all - self.sat(a)) | self.sat(b)
            case EX(arg):
                return self._pre_exists(self.sat(arg))
            case AX(arg):
                return self.all - self._pre_exists(self.all - self.sat(arg))
            case EU(a, b):
                return self._until(self.sat(a), self.sat(b))
            case EF(arg):
                return self._until(self.all, self.sat(arg))
            case EG(arg):
                return self._globally(self.sat(arg))
            case AF(arg):
                return self.all - self._globally(self.all - self.sat(arg))
            case AG(arg):
                return self.all - self._until(self.all, self.all - self.sat(arg))
            case AU(a, b):
                not_b = self.all - self.sat(b)
                not_a_not_b = not_b - self.sat(a)
                bad = self._until(not_b, not_a_not_b) | self._globally(not_b)
                return self.all - bad
        raise TypeError(f"Unknown formula node {f!r}")

    def _pre_exists(self, target: frozenset[int] | set[int]) -> set[int]:
        pred = self.graph.pred
        result: set[int] = set()
        for j in target:
            result.update(pred[j])
        return result

    def _until(
        self, allowed: frozenset[int] | set[int], target: frozenset[int] | set[int]
    ) -> set[int]:
        """Least fixed point of ``Z = target  U  (allowed  ∩  EX Z)``."""
        pred = self.graph.pred
        result = set(target)
        stack = list(result)
        while stack:
            j = stack.pop()
            for i in pred[j]:
                if i not in result and i in allowed:
                    result.add(i)
                    stack.append(i)
        return result

    def _globally(self, region: frozenset[int] | set[int]) -> set[int]:
        """Greatest fixed point of ``Z = region ∩ EX Z`` by edge counting."""
        graph = self.graph
        succ, pred = graph.succ, graph.pred
        z = set(region)
        count = {i: sum(1 for j in succ[i] if j in z) for i in z}
        stack = [i for i in z if count[i] == 0]
        removed: set[int] = set()
        while stack:
            j = stack.pop()
            if j in removed:
                continue
            removed.add(j)
            z.discard(j)
            for i in pred[j]:
                if i in z:
                    count[i] -= 1
                    if count[i] == 0:
                        stack.append(i)
        return z


def formula_expressions(formula: Formula) -> list[Expression]:
    """Every atomic state expression occurring in ``formula``."""
    match formula:
        case Atom(expr) | Label(_, expr):
            return [expr]
        case Const():
            return []
        case Not(arg) | EX(arg) | AX(arg) | EF(arg) | AF(arg) | EG(arg) | AG(arg):
            return formula_expressions(arg)
        case And(a, b) | Or(a, b) | Implies(a, b) | EU(a, b) | AU(a, b):
            return formula_expressions(a) + formula_expressions(b)
    raise TypeError(f"Unknown formula node {formula!r}")


def validate_labels(labels: Mapping[str, Expression]) -> None:
    for name in labels:
        if name in KEYWORDS:
            raise SpecError(f"Label name {name!r} clashes with a CTL keyword.")
