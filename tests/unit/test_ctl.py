"""CTL parsing and fixed-point model checking, against hand-derived answers."""

from __future__ import annotations

import pytest
from support import ExplicitGraph, graph_system

from prooftrail.errors import ExpressionError, SpecError
from prooftrail.exploration.bfs import explore
from prooftrail.model.expressions import BOOL, compile_expression
from prooftrail.model.system import STUTTER
from prooftrail.properties import ctl
from prooftrail.properties.ctl import (
    AF,
    AG,
    AU,
    AX,
    EF,
    EU,
    EX,
    And,
    Atom,
    Const,
    CTLChecker,
    Implies,
    Label,
    Not,
    Or,
    TotalGraph,
    evaluate_propositional,
    formula_expressions,
    is_propositional,
    parse_ctl,
    to_string,
)

SCOPE = {"s": "int", "b": "bool"}

# 0 -> 1 -> 2 -> 2 (self loop);  0 -> 3 (deadlock);  4 is unreachable.
#
#       +--> 1 --> 2 --+
#   0 --+             (loop)
#       +--> 3 (stuck)
HAND = ExplicitGraph(5, ((0, 1), (1, 2), (2, 2), (0, 3)), (0,))


def checker_for(graph: ExplicitGraph = HAND):
    system = graph_system(graph)
    exploration = explore(system, store_edges=True)
    assert exploration.edges is not None
    total = TotalGraph(
        [exploration.state(i) for i in range(len(exploration.index))],
        exploration.edges,
        exploration.initial,
        STUTTER,
    )
    return CTLChecker(total), total


def sat_states(text: str, graph: ExplicitGraph = HAND) -> set[int]:
    """The values of ``s`` of the states satisfying ``text``."""
    checker, total = checker_for(graph)
    formula = parse_ctl(text, {"s": "int"})
    return {total.states[i]["s"] for i in checker.sat(formula)}


# ---------------------------------------------------------------- semantics


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("{s == 2}", {2}),
        ("true", {0, 1, 2, 3}),
        ("false", set()),
        ("!{s == 2}", {0, 1, 3}),
        ("{s == 2} || {s == 3}", {2, 3}),
        ("{s >= 1} && {s <= 2}", {1, 2}),
        ("{s == 0} -> {s == 9}", {1, 2, 3}),
        ("EX {s == 3}", {0, 3}),
        ("AX {s == 2}", {1, 2}),
        ("EF {s == 2}", {0, 1, 2}),
        ("AF {s == 2}", {1, 2}),
        ("EG {s != 3}", {0, 1, 2}),
        ("AG {s != 3}", {1, 2}),
        ("E[{s != 3} U {s == 2}]", {0, 1, 2}),
        ("A[{s != 3} U {s == 2}]", {1, 2}),
        ("AG EF {s == 2}", {1, 2}),
        ("EF AG {s == 3}", {0, 3}),
        ("AF AG {s == 2}", {1, 2}),
    ],
)
def test_operators_on_a_hand_graph(text: str, expected: set[int]) -> None:
    assert sat_states(text) == expected


def test_deadlock_states_are_totalized_with_a_self_loop() -> None:
    # State 3 has no real successor; with the stutter loop EG/AG hold there.
    assert 3 in sat_states("EG {s == 3}")
    assert 3 in sat_states("AG {s == 3}")
    assert 3 in sat_states("AX {s == 3}")
    # ...and AF only needs the (stuttering) future to contain the target.
    assert 3 in sat_states("AF {s == 3}")
    assert 3 not in sat_states("AF {s == 2}")


def test_au_is_not_eu() -> None:
    # From 0 one path escapes through 3 (not p, not q): E-until holds, A-until not.
    assert 0 in sat_states("E[{s != 3} U {s == 2}]")
    assert 0 not in sat_states("A[{s != 3} U {s == 2}]")


def test_af_requires_every_path_not_just_a_target_state() -> None:
    # EF finds the target by search; AF must also rule out the path to 3.
    assert 0 in sat_states("EF {s == 2}") and 0 not in sat_states("AF {s == 2}")


def test_holds_initially_checks_every_initial_state() -> None:
    graph = ExplicitGraph(3, ((0, 1), (1, 1), (2, 1)), (0, 2))
    checker, _ = checker_for(graph)
    assert checker.holds_initially(parse_ctl("AF AG {s == 1}", {"s": "int"}))
    assert not checker.holds_initially(parse_ctl("{s == 0}", {"s": "int"}))


def test_unreachable_states_are_not_in_the_graph() -> None:
    _, total = checker_for()
    assert {st["s"] for st in total.states} == {0, 1, 2, 3}


def test_total_graph_predecessors_count_multi_edges() -> None:
    graph = ExplicitGraph(2, ((0, 1), (0, 1), (1, 1)), (0,))
    _, total = checker_for(graph)
    one = next(i for i, st in enumerate(total.states) if st["s"] == 1)
    assert (
        total.pred[one].count(
            next(i for i, st in enumerate(total.states) if st["s"] == 0)
        )
        == 2
    )
    assert len(total) == 2


def test_labels_are_atoms() -> None:
    expr = compile_expression("s == 2", {"s": "int"}, expected=BOOL)
    formula = parse_ctl("EF two", {"s": "int"}, {"two": expr})
    assert formula == EF(Label("two", expr))
    checker, total = checker_for()
    assert {total.states[i]["s"] for i in checker.sat(formula)} == {0, 1, 2}


# ------------------------------------------------------------------ parsing


def atoms(text: str):
    return parse_ctl(text, SCOPE)


def test_precedence_and_associativity() -> None:
    a, b, c = "{s == 1}", "{s == 2}", "{s == 3}"
    parsed = atoms(f"{a} || {b} && {c}")
    assert isinstance(parsed, Or) and isinstance(parsed.right, And)
    parsed = atoms(f"{a} -> {b} -> {c}")  # right associative
    assert isinstance(parsed, Implies) and isinstance(parsed.right, Implies)
    parsed = atoms(f"!{a} && {b}")
    assert isinstance(parsed, And) and isinstance(parsed.left, Not)
    parsed = atoms(f"AG {a} -> {b}")  # unary binds tighter than ->
    assert isinstance(parsed, Implies) and isinstance(parsed.left, AG)


def test_nested_temporal_operators_and_parentheses() -> None:
    parsed = atoms("AG (({s == 1}) -> AF {b})")
    assert isinstance(parsed, AG)
    assert isinstance(parsed.arg, Implies) and isinstance(parsed.arg.right, AF)
    assert isinstance(atoms("EX AX EF {b}"), EX)
    assert isinstance(atoms("E[{b} U A[{b} U {s == 1}]]"), EU)
    assert isinstance(atoms("A[{b} U {b}]"), AU)
    assert isinstance(atoms("AX EG {b}"), AX)
    assert atoms("true") == Const(True) and atoms("false") == Const(False)


def test_to_string_roundtrips_to_an_equal_formula() -> None:
    for text in [
        "AG ({s == 1} -> AF {s == 2})",
        "E[{s == 1} U {s == 2}] || !{b}",
        "AX EX {s > 0} && EG {b}",
        "A[true U false]",
    ]:
        first = atoms(text)
        assert atoms(to_string(first)) == first


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "formula is empty"),
        ("   ", "formula is empty"),
        ("AG", "unexpected end"),
        ("AG {s == 1} {s == 2}", "unexpected"),
        ("{s == 1", "unexpected input"),
        ("{unknown_var == 1}", "unknown variable"),
        ("{s}", "expected a bool"),
        ("foo", "unknown identifier"),
        ("(({s == 1})", "unexpected end"),
        ("E[{s == 1} {s == 2}]", "expected 'U'"),
        ("E {s == 1}", r"expected '\['"),
        ("{s == 1} & {s == 2}", "unexpected input"),
        ("{s == 1} => {s == 2}", "unexpected input"),
        ("-> {s == 1}", "unexpected"),
        ("A[{s == 1} U {s == 2}", "unexpected end"),
    ],
)
def test_bad_formulas_are_rejected_with_a_clear_message(
    text: str, fragment: str
) -> None:
    with pytest.raises(ExpressionError, match=fragment):
        parse_ctl(text, SCOPE, context="test")


def test_non_string_and_oversized_and_too_deep_formulas() -> None:
    with pytest.raises(ExpressionError, match="expected a string"):
        parse_ctl(5, SCOPE)
    with pytest.raises(ExpressionError, match="longer than"):
        parse_ctl("{b} && " * 400 + "{b}", SCOPE)
    with pytest.raises(ExpressionError, match="nested deeper"):
        parse_ctl("!" * (ctl.MAX_FORMULA_DEPTH + 5) + "{b}", SCOPE)
    with pytest.raises(ExpressionError, match="nested deeper"):
        parse_ctl("(" * 200 + "{b}" + ")" * 200, SCOPE)


def test_label_names_clashing_with_keywords_are_rejected() -> None:
    expr = compile_expression("b", {"b": "bool"}, expected=BOOL)
    ctl.validate_labels({"fine": expr})
    for keyword in ("AG", "E", "U", "true"):
        with pytest.raises(SpecError, match="clashes"):
            ctl.validate_labels({keyword: expr})


# ------------------------------------------------------------------- helpers


def test_propositional_detection_and_evaluation() -> None:
    p = atoms("({s == 1} || {b}) && !{s == 2} -> {s > 0}")
    assert is_propositional(p)
    assert evaluate_propositional(p, {"s": 1, "b": False})
    assert not is_propositional(atoms("{b} && EF {b}"))
    assert not is_propositional(atoms("AG {b}"))
    with pytest.raises(ValueError):
        evaluate_propositional(atoms("EF {b}"), {"s": 1, "b": True})


def test_formula_expressions_lists_every_atom() -> None:
    sources = [e.source for e in formula_expressions(atoms("AG ({s == 1} -> EF {b})"))]
    assert sources == ["s == 1", "b"]
    assert formula_expressions(Const(True)) == []
    assert isinstance(atoms("{b}"), Atom)
