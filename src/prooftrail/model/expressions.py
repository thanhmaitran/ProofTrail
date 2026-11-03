"""A small, statically typed, side-effect-free expression language.

Expressions come from untrusted specification files, so they are **never**
passed to ``eval``/``exec``. Instead the text is parsed with Python's ``ast``
module (parsing alone executes nothing), every node is checked against an
explicit allow-list, and the result is compiled into closures that only ever
touch the variables of the model.

Types
-----
``int``   integers (``bool`` is *not* an integer here)
``bool``  Booleans
``str``   enum literals, written as string constants such as ``"red"``

Supported constructs
--------------------
* literals: integers, ``true``/``false`` (``True``/``False`` also accepted),
  short string constants
* model variables
* ``not``, ``and``, ``or`` (short-circuit, left to right)
* ``+ - * // %`` on integers (floor semantics, like Python), unary ``-`` / ``+``
* ``== != < <= > >=`` (``==``/``!=`` need equal types, ordering needs ints;
  chains such as ``0 <= x <= 3`` are allowed)
* ``x in ("a", "b")`` and ``x not in (1, 2, 3)`` with constant members
* conditional expressions ``a if cond else b``

Everything else (calls, attribute access, subscripts, lambdas, comprehensions,
``**``, ``/``, bit operations, ...) is rejected with an :class:`ExpressionError`.
"""

from __future__ import annotations

import ast
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from prooftrail.errors import ExpressionError, ModelError
from prooftrail.model.state import Scalar

MAX_EXPR_LENGTH = 1_000
MAX_AST_NODES = 300
MAX_AST_DEPTH = 40
MAX_INT = 2**63 - 1
MAX_STRING_LENGTH = 64
MAX_MEMBERSHIP_ITEMS = 64

INT = "int"
BOOL = "bool"
STR = "str"

_TRUE_NAMES = frozenset({"true", "True"})
_FALSE_NAMES = frozenset({"false", "False"})

Env = Mapping[str, Scalar]
_Fn = Callable[[Env], Scalar]


@dataclass(frozen=True)
class Expression:
    """A compiled, type-checked expression."""

    source: str
    type: str
    names: frozenset[str]
    _fn: _Fn = field(compare=False, repr=False)
    strings: frozenset[str] = frozenset()  # string (enum) literals used

    def evaluate(self, env: Env) -> Scalar:
        """Evaluate against ``env`` (a ``State`` or any name -> value mapping)."""
        return self._fn(env)

    def __call__(self, env: Env) -> Scalar:
        return self._fn(env)


def compile_expression(
    source: object,
    scope: Mapping[str, str],
    *,
    expected: str | None = None,
    context: str = "expression",
) -> Expression:
    """Parse, type-check and compile ``source``.

    ``scope`` maps each variable name to its expression type (``int``, ``bool``
    or ``str``). If ``expected`` is given the result type must match it.
    """
    if not isinstance(source, str):
        raise ExpressionError(
            f"{context}: expected a string, got {type(source).__name__}."
        )
    text = source.strip()
    if not text:
        raise ExpressionError(f"{context}: expression is empty.")
    if len(text) > MAX_EXPR_LENGTH:
        raise ExpressionError(
            f"{context}: expression is longer than {MAX_EXPR_LENGTH} characters."
        )

    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(
            f"{context}: syntax error in {text!r}: {exc.msg}."
        ) from None
    except (ValueError, RecursionError, MemoryError):
        raise ExpressionError(
            f"{context}: {text!r} cannot be parsed (too deeply nested or invalid)."
        ) from None

    compiler = _Compiler(text, scope, context)
    fn, etype = compiler.compile(tree.body, 0)
    if expected is not None and etype != expected:
        raise ExpressionError(
            f"{context}: expected a {expected} expression but {text!r} is {etype}."
        )
    return Expression(
        text, etype, frozenset(compiler.names), fn, frozenset(compiler.strings)
    )


class _Compiler:
    def __init__(self, source: str, scope: Mapping[str, str], context: str) -> None:
        self.source = source
        self.scope = scope
        self.context = context
        self.names: set[str] = set()
        self.strings: set[str] = set()
        self.nodes = 0

    # ------------------------------------------------------------------ utils

    def fail(self, message: str) -> ExpressionError:
        return ExpressionError(f"{self.context}: {message} (in {self.source!r}).")

    def require(self, etype: str, expected: str, what: str) -> None:
        if etype != expected:
            raise self.fail(f"{what} must be {expected}, got {etype}")

    def checked(self, value: int) -> int:
        if not -MAX_INT <= value <= MAX_INT:
            raise ModelError(
                f"{self.context}: integer overflow beyond 64 bits in {self.source!r}."
            )
        return value

    # --------------------------------------------------------------- dispatch

    def compile(self, node: ast.expr, depth: int) -> tuple[_Fn, str]:
        self.nodes += 1
        if self.nodes > MAX_AST_NODES:
            raise self.fail(f"expression has more than {MAX_AST_NODES} nodes")
        if depth > MAX_AST_DEPTH:
            raise self.fail(f"expression is nested deeper than {MAX_AST_DEPTH} levels")

        method = getattr(self, f"_c_{type(node).__name__}", None)
        if method is None:
            raise self.fail(f"unsupported construct {type(node).__name__}")
        return method(node, depth + 1)

    # ----------------------------------------------------------------- leaves

    def _c_Constant(self, node: ast.Constant, depth: int) -> tuple[_Fn, str]:
        value = node.value
        if isinstance(value, bool):
            return (lambda env, v=value: v), BOOL
        if isinstance(value, int):
            if abs(value) > MAX_INT:
                raise self.fail("integer constant exceeds 64 bits")
            return (lambda env, v=value: v), INT
        if isinstance(value, str):
            if len(value) > MAX_STRING_LENGTH:
                raise self.fail(f"string constant longer than {MAX_STRING_LENGTH}")
            self.strings.add(value)
            return (lambda env, v=value: v), STR
        raise self.fail(f"unsupported literal of type {type(value).__name__}")

    def _c_Name(self, node: ast.Name, depth: int) -> tuple[_Fn, str]:
        name = node.id
        if name in _TRUE_NAMES:
            return (lambda env: True), BOOL
        if name in _FALSE_NAMES:
            return (lambda env: False), BOOL
        if name not in self.scope:
            known = ", ".join(sorted(self.scope)) or "none"
            raise self.fail(f"unknown variable {name!r} (known variables: {known})")
        self.names.add(name)
        return (lambda env, n=name: env[n]), self.scope[name]

    # -------------------------------------------------------------- operators

    def _c_UnaryOp(self, node: ast.UnaryOp, depth: int) -> tuple[_Fn, str]:
        operand, etype = self.compile(node.operand, depth)
        if isinstance(node.op, ast.Not):
            self.require(etype, BOOL, "operand of 'not'")
            return (lambda env: not operand(env)), BOOL
        if isinstance(node.op, ast.USub):
            self.require(etype, INT, "operand of unary '-'")
            return (lambda env: self.checked(-operand(env))), INT
        if isinstance(node.op, ast.UAdd):
            self.require(etype, INT, "operand of unary '+'")
            return operand, INT
        raise self.fail(f"unsupported unary operator {type(node.op).__name__}")

    def _c_BinOp(self, node: ast.BinOp, depth: int) -> tuple[_Fn, str]:
        op = node.op
        if not isinstance(op, ast.Add | ast.Sub | ast.Mult | ast.FloorDiv | ast.Mod):
            raise self.fail(
                f"unsupported operator {type(op).__name__} (allowed: + - * // %)"
            )
        left, ltype = self.compile(node.left, depth)
        right, rtype = self.compile(node.right, depth)
        self.require(ltype, INT, "left operand of arithmetic")
        self.require(rtype, INT, "right operand of arithmetic")
        checked = self.checked
        source = self.source
        context = self.context

        if isinstance(op, ast.Add):
            return (lambda env: checked(left(env) + right(env))), INT
        if isinstance(op, ast.Sub):
            return (lambda env: checked(left(env) - right(env))), INT
        if isinstance(op, ast.Mult):
            return (lambda env: checked(left(env) * right(env))), INT

        is_div = isinstance(op, ast.FloorDiv)

        def divide(env: Env) -> int:
            divisor = right(env)
            if divisor == 0:
                raise ModelError(f"{context}: division by zero in {source!r}.")
            dividend = left(env)
            return checked(dividend // divisor if is_div else dividend % divisor)

        return divide, INT

    def _c_BoolOp(self, node: ast.BoolOp, depth: int) -> tuple[_Fn, str]:
        parts = []
        for value in node.values:
            fn, etype = self.compile(value, depth)
            self.require(etype, BOOL, "operand of boolean operator")
            parts.append(fn)
        if isinstance(node.op, ast.And):
            return (lambda env: all(p(env) for p in parts)), BOOL
        return (lambda env: any(p(env) for p in parts)), BOOL

    def _c_IfExp(self, node: ast.IfExp, depth: int) -> tuple[_Fn, str]:
        test, ttype = self.compile(node.test, depth)
        body, btype = self.compile(node.body, depth)
        orelse, otype = self.compile(node.orelse, depth)
        self.require(ttype, BOOL, "condition of conditional expression")
        if btype != otype:
            raise self.fail(
                "branches of conditional expression differ in type "
                f"({btype} vs {otype})"
            )
        return (lambda env: body(env) if test(env) else orelse(env)), btype

    def _c_Compare(self, node: ast.Compare, depth: int) -> tuple[_Fn, str]:
        ops = node.ops
        if any(isinstance(op, ast.In | ast.NotIn) for op in ops):
            return self._membership(node, depth)

        operands = [self.compile(node.left, depth)]
        operands.extend(self.compile(c, depth) for c in node.comparators)
        checks: list[Callable[[Scalar, Scalar], bool]] = []
        for op, (_, ltype), (_, rtype) in zip(
            ops, operands, operands[1:], strict=False
        ):
            if isinstance(op, ast.Eq | ast.NotEq):
                if ltype != rtype:
                    raise self.fail(f"cannot compare {ltype} with {rtype}")
                checks.append(_EQUALITY[type(op)])
            elif isinstance(op, ast.Lt | ast.LtE | ast.Gt | ast.GtE):
                self.require(ltype, INT, "left side of an ordering comparison")
                self.require(rtype, INT, "right side of an ordering comparison")
                checks.append(_ORDERING[type(op)])
            else:
                raise self.fail(f"unsupported comparison {type(op).__name__}")
        fns = [fn for fn, _ in operands]

        if len(checks) == 1:
            check = checks[0]
            lhs, rhs = fns
            return (lambda env: check(lhs(env), rhs(env))), BOOL

        def chain(env: Env) -> bool:
            current = fns[0](env)
            for check, fn in zip(checks, fns[1:], strict=True):
                nxt = fn(env)
                if not check(current, nxt):
                    return False
                current = nxt
            return True

        return chain, BOOL

    def _membership(self, node: ast.Compare, depth: int) -> tuple[_Fn, str]:
        if len(node.ops) != 1:
            raise self.fail("'in' / 'not in' cannot be chained")
        lhs, ltype = self.compile(node.left, depth)
        container = node.comparators[0]
        if not isinstance(container, ast.Tuple | ast.List | ast.Set):
            raise self.fail("right side of 'in' must be a tuple of constants")
        if not container.elts:
            raise self.fail("right side of 'in' must not be empty")
        if len(container.elts) > MAX_MEMBERSHIP_ITEMS:
            raise self.fail(f"'in' lists more than {MAX_MEMBERSHIP_ITEMS} items")
        members = []
        for elt in container.elts:
            self.nodes += 1
            if not isinstance(elt, ast.Constant):
                raise self.fail("members of 'in' must be constants")
            fn, etype = self._c_Constant(elt, depth)
            if etype != ltype:
                raise self.fail(f"'in' member of type {etype} does not match {ltype}")
            members.append(fn({}))
        member_set = frozenset(members)
        if isinstance(node.ops[0], ast.In):
            return (lambda env: lhs(env) in member_set), BOOL
        return (lambda env: lhs(env) not in member_set), BOOL


_EQUALITY: dict[type, Callable[[Scalar, Scalar], bool]] = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
}

_ORDERING: dict[type, Callable[[Scalar, Scalar], bool]] = {
    ast.Lt: lambda a, b: a < b,  # type: ignore[operator]
    ast.LtE: lambda a, b: a <= b,  # type: ignore[operator]
    ast.Gt: lambda a, b: a > b,  # type: ignore[operator]
    ast.GtE: lambda a, b: a >= b,  # type: ignore[operator]
}
