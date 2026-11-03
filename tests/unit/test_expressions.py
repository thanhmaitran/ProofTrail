from __future__ import annotations

import ast
import pathlib

import pytest

from prooftrail.errors import ExpressionError, ModelError
from prooftrail.model.expressions import (
    MAX_AST_NODES,
    MAX_EXPR_LENGTH,
    MAX_INT,
    compile_expression,
)

SCOPE = {"x": "int", "y": "int", "b": "bool", "c": "bool", "m": "str"}


def run(text: str, **env: object) -> object:
    return compile_expression(text, SCOPE).evaluate(env)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("text", "env", "expected"),
    [
        ("1 + 2 * 3", {}, 7),
        ("(1 + 2) * 3", {}, 9),
        ("x - y", {"x": 1, "y": 4}, -3),
        ("-x", {"x": 5}, -5),
        ("+x", {"x": 5}, 5),
        ("7 // 2", {}, 3),
        ("-7 // 2", {}, -4),  # floor semantics, like Python
        ("7 % 3", {}, 1),
        ("-7 % 3", {}, 2),
        ("x == 1", {"x": 1}, True),
        ("x != 1", {"x": 1}, False),
        ("x < y", {"x": 1, "y": 2}, True),
        ("0 <= x <= 3", {"x": 3}, True),
        ("0 <= x <= 3", {"x": 4}, False),
        ("0 < x < y < 10", {"x": 1, "y": 5}, True),
        ("b and c", {"b": True, "c": False}, False),
        ("b or c", {"b": True, "c": False}, True),
        ("not b", {"b": True}, False),
        ("true and not false", {}, True),
        ("True or False", {}, True),
        ('m == "red"', {"m": "red"}, True),
        ('m != "red"', {"m": "green"}, True),
        ('m in ("red", "green")', {"m": "green"}, True),
        ('m not in ("red", "green")', {"m": "blue"}, True),
        ("x in (1, 2, 3)", {"x": 2}, True),
        ("x not in [1, 2]", {"x": 2}, False),
        ("1 if b else 2", {"b": False}, 2),
        ('"a" if x > 0 else "b"', {"x": 1}, "a"),
        ("b == c", {"b": True, "c": True}, True),
    ],
)
def test_evaluation(text: str, env: dict, expected: object) -> None:
    result = run(text, **env)
    assert result == expected
    assert type(result) is type(expected)


def test_boolean_operators_short_circuit() -> None:
    # The division by zero on the right must never be evaluated.
    assert run("b or (1 // y == 0)", b=True, y=0) is True
    assert run("b and (1 // y == 0)", b=False, y=0) is False


def test_conditional_expression_only_evaluates_the_taken_branch() -> None:
    assert run("1 if b else 1 // y", b=True, y=0) == 1


def test_division_by_zero_is_a_model_error() -> None:
    with pytest.raises(ModelError, match="division by zero"):
        run("x // y", x=1, y=0)
    with pytest.raises(ModelError, match="division by zero"):
        run("x % y", x=1, y=0)


def test_integer_overflow_is_a_model_error() -> None:
    with pytest.raises(ModelError, match="overflow"):
        run("x * x", x=MAX_INT)
    with pytest.raises(ModelError, match="overflow"):
        run("x + 1", x=MAX_INT)
    with pytest.raises(ModelError, match="overflow"):
        run("-x - 2", x=MAX_INT)


@pytest.mark.parametrize(
    "text",
    [
        "b + 1",  # bool is not an integer
        "x + true",
        "not x",
        "-b",
        "x and b",
        "x == b",  # mixed types
        "m == 1",
        'm < "a"',
        "b < c",
        "1 if x else 2",  # condition must be bool
        "1 if b else true",  # branch types differ
        "m in (1, 2)",
        "x in (true,)",
    ],
)
def test_type_errors(text: str) -> None:
    with pytest.raises(ExpressionError):
        compile_expression(text, SCOPE)


@pytest.mark.parametrize(
    "text",
    [
        "abs(x)",
        "__import__('os').system('true')",
        "x.real",
        "(1).__class__",
        "[x][0]",
        "{x: 1}",
        "lambda: 1",
        "[i for i in (1,)]",
        "x ** 2",
        "x / 2",
        "x & 1",
        "x | 1",
        "x ^ 1",
        "x << 1",
        "~x",
        "(y := 2)",
        'f"{x}"',
        "*x",
        "x is x",
        "x is not x",
        "1.5",
        "None",
        "b'bytes'",
        "x in y",  # membership needs constants
        "x in ()",
        "x in (1, y)",
        "1 < x in (1,)",
        "await x",
    ],
)
def test_unsupported_constructs_are_rejected(text: str) -> None:
    with pytest.raises(ExpressionError):
        compile_expression(text, SCOPE)


def test_unknown_variable_names_the_known_ones() -> None:
    with pytest.raises(ExpressionError, match="unknown variable 'z'.*known variables"):
        compile_expression("z + 1", SCOPE)


@pytest.mark.parametrize("bad", [None, 5, True, ["x"], b"x == 1"])
def test_non_string_source_is_rejected(bad: object) -> None:
    with pytest.raises(ExpressionError, match="expected a string"):
        compile_expression(bad, SCOPE)


@pytest.mark.parametrize("text", ["", "   ", "\n"])
def test_empty_expression_is_rejected(text: str) -> None:
    with pytest.raises(ExpressionError, match="empty"):
        compile_expression(text, SCOPE)


def test_syntax_errors_are_expression_errors() -> None:
    with pytest.raises(ExpressionError, match="syntax error"):
        compile_expression("x +", SCOPE)
    with pytest.raises(ExpressionError):
        compile_expression("x = 1", SCOPE)  # statement, not expression


def test_null_byte_is_rejected_not_crashing() -> None:
    with pytest.raises(ExpressionError):
        compile_expression("x\x00 == 1", SCOPE)


def test_size_and_depth_limits() -> None:
    with pytest.raises(ExpressionError, match="longer than"):
        compile_expression("1 + " * (MAX_EXPR_LENGTH // 4 + 5) + "1", SCOPE)
    with pytest.raises(ExpressionError, match="nodes"):
        # wide and shallow, so the node limit (not the depth limit) triggers
        compile_expression(" or ".join(["x < y"] * (MAX_AST_NODES // 3 + 10)), SCOPE)
    with pytest.raises(ExpressionError, match="nested deeper"):
        compile_expression("-" * 60 + "1", SCOPE)
    with pytest.raises(ExpressionError, match="nested deeper"):
        compile_expression("not " * 60 + "b", SCOPE)
    # Redundant parentheses do not add AST depth, and absurd nesting is
    # rejected by Python's own parser, which we report as an expression error.
    assert compile_expression("(" * 50 + "1" + ")" * 50, SCOPE).evaluate({}) == 1
    with pytest.raises(ExpressionError):
        compile_expression("(" * 500 + "1" + ")" * 500, SCOPE)


def test_expected_type_is_enforced() -> None:
    assert compile_expression("x + 1", SCOPE, expected="int").type == "int"
    with pytest.raises(ExpressionError, match="expected a bool"):
        compile_expression("x + 1", SCOPE, expected="bool")


def test_metadata_records_names_and_strings() -> None:
    expr = compile_expression('m == "red" and x > y', SCOPE)
    assert expr.names == {"m", "x", "y"}
    assert expr.strings == {"red"}
    assert expr.type == "bool"
    assert expr({"m": "red", "x": 2, "y": 1}) is True


def test_package_never_calls_eval_exec_or_import_dynamically() -> None:
    """Specification text must never reach eval/exec; guard the whole package."""
    package = pathlib.Path(__file__).resolve().parents[2] / "src" / "prooftrail"
    forbidden = {"eval", "exec", "__import__", "compile", "open_code"}
    offenders: list[str] = []
    for path in package.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden
            ):
                offenders.append(f"{path.name}:{node.lineno}:{node.func.id}")
    assert offenders == []
