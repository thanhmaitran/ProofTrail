"""Independent reference implementations used to cross-check the engine.

Nothing here imports the engine's exploration, CTL or verification code. The
reference works on plain Python data (dicts of successor lists) with the most
naive algorithms that are obviously correct: set closure for reachability,
``networkx`` for shortest paths, and *naive iteration to a fixed point* for CTL
(using a different formulation than the engine: ``AF`` as a least fixed point
of ``p or AX Z`` rather than as the dual of ``EG``).
"""

from __future__ import annotations

import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass

import networkx as nx

from prooftrail.model.parser import parse_specification
from prooftrail.model.system import TransitionSystem

# --------------------------------------------------------------- explicit graphs


@dataclass(frozen=True)
class ExplicitGraph:
    """A small labelled graph over states ``0..n-1``."""

    n: int
    edges: tuple[tuple[int, int], ...]  # (source, target); duplicates allowed
    initial: tuple[int, ...]

    def succ(self) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {i: [] for i in range(self.n)}
        for a, b in self.edges:
            out[a].append(b)
        return out


def random_graph(rng: random.Random, max_states: int = 10) -> ExplicitGraph:
    n = rng.randint(1, max_states)
    edge_count = rng.randint(0, n * 2)
    edges = tuple((rng.randrange(n), rng.randrange(n)) for _ in range(edge_count))
    initial = tuple(sorted(rng.sample(range(n), rng.randint(1, min(2, n)))))
    return ExplicitGraph(n, edges, initial)


def graph_system(graph: ExplicitGraph, *, shuffle: random.Random | None = None):
    """Build a ``TransitionSystem`` whose state is the single variable ``s``.

    Each graph edge becomes a transition ``when s == a`` updating ``s`` to
    ``b``. Optionally the transition (and declaration) order is shuffled to show
    that declaration order never changes verdicts.
    """
    transitions = [
        {"id": f"e{k}", "when": f"s == {a}", "updates": {"s": str(b)}}
        for k, (a, b) in enumerate(graph.edges)
    ]
    if shuffle is not None:
        shuffle.shuffle(transitions)
    data = {
        "schema_version": "1.0",
        "model": {
            "id": "graph",
            "variables": {
                "s": {
                    "type": "integer",
                    "min": 0,
                    "max": max(graph.n - 1, 0),
                    "initial": list(graph.initial),
                }
            },
            "transitions": transitions,
        },
    }
    return parse_specification(data).system


def system_from_dict(data: Mapping) -> TransitionSystem:
    return parse_specification(data).system


def atom(members: set[int]) -> str:
    """A state expression true exactly in ``members`` (for variable ``s``)."""
    if not members:
        return "false"
    return "s in (" + ", ".join(str(m) for m in sorted(members)) + ",)"


# ------------------------------------------------------------ reference algorithms


def reachable(graph: ExplicitGraph) -> set[int]:
    succ = graph.succ()
    seen = set(graph.initial)
    frontier = list(graph.initial)
    while frontier:
        i = frontier.pop()
        for j in succ[i]:
            if j not in seen:
                seen.add(j)
                frontier.append(j)
    return seen


def shortest_distance(graph: ExplicitGraph, targets: set[int]) -> int | None:
    """Length of a shortest path from any initial state to any target state."""
    g = nx.DiGraph()
    g.add_nodes_from(range(graph.n))
    g.add_edges_from(graph.edges)
    best: int | None = None
    for i in graph.initial:
        lengths = nx.single_source_shortest_path_length(g, i)
        for t in targets:
            if t in lengths and (best is None or lengths[t] < best):
                best = lengths[t]
    return best


def deadlocks(graph: ExplicitGraph) -> set[int]:
    succ = graph.succ()
    return {i for i in reachable(graph) if not succ[i]}


def totalized(graph: ExplicitGraph) -> dict[int, set[int]]:
    """Successor sets where every deadlock state loops on itself."""
    succ = graph.succ()
    return {i: (set(succ[i]) if succ[i] else {i}) for i in range(graph.n)}


# Formulas are nested tuples:
#   ("atom", frozenset) ("not", f) ("and", f, g) ("or", f, g) ("imp", f, g)
#   ("EX", f) ("AX", f) ("EF", f) ("AF", f) ("EG", f) ("AG", f)
#   ("EU", f, g) ("AU", f, g)


def sat(formula: tuple, tot: dict[int, set[int]]) -> set[int]:
    """Naive CTL semantics on a totalized graph (every state has a successor)."""
    states = set(tot)
    op = formula[0]

    def ex(z: set[int]) -> set[int]:
        return {i for i in states if tot[i] & z}

    def ax(z: set[int]) -> set[int]:
        return {i for i in states if tot[i] <= z}

    if op == "atom":
        return set(formula[1]) & states
    if op == "not":
        return states - sat(formula[1], tot)
    if op == "and":
        return sat(formula[1], tot) & sat(formula[2], tot)
    if op == "or":
        return sat(formula[1], tot) | sat(formula[2], tot)
    if op == "imp":
        return (states - sat(formula[1], tot)) | sat(formula[2], tot)
    if op == "EX":
        return ex(sat(formula[1], tot))
    if op == "AX":
        return ax(sat(formula[1], tot))
    if op in ("EF", "AF", "EU", "AU"):
        if op in ("EF", "AF"):
            p, q = states, sat(formula[1], tot)
        else:
            p, q = sat(formula[1], tot), sat(formula[2], tot)
        step = ex if op in ("EF", "EU") else ax
        z: set[int] = set()  # least fixed point, iterate from the empty set
        while True:
            new = q | (p & step(z))
            if new == z:
                return z
            z = new
    if op in ("EG", "AG"):
        p = sat(formula[1], tot)
        step = ex if op == "EG" else ax
        z = set(states)  # greatest fixed point, iterate from everything
        while True:
            new = p & step(z)
            if new == z:
                return z
            z = new
    raise ValueError(f"unknown operator {op!r}")


def to_ctl_text(formula: tuple) -> str:
    """Render a reference formula in ProofTrail's concrete CTL syntax."""
    op = formula[0]
    if op == "atom":
        return "{" + atom(set(formula[1])) + "}"
    if op == "not":
        return f"!({to_ctl_text(formula[1])})"
    if op in ("and", "or", "imp"):
        sym = {"and": "&&", "or": "||", "imp": "->"}[op]
        return f"({to_ctl_text(formula[1])} {sym} {to_ctl_text(formula[2])})"
    if op in ("EU", "AU"):
        return f"{op[0]}[{to_ctl_text(formula[1])} U {to_ctl_text(formula[2])}]"
    return f"{op} ({to_ctl_text(formula[1])})"


def random_formula(rng: random.Random, n: int, depth: int) -> tuple:
    if depth == 0 or rng.random() < 0.25:
        members = frozenset(i for i in range(n) if rng.random() < 0.4)
        return ("atom", members)
    op = rng.choice(
        ["not", "and", "or", "imp", "EX", "AX", "EF", "AF", "EG", "AG", "EU", "AU"]
    )
    if op in ("not", "EX", "AX", "EF", "AF", "EG", "AG"):
        return (op, random_formula(rng, n, depth - 1))
    return (op, random_formula(rng, n, depth - 1), random_formula(rng, n, depth - 1))


# ---------------------------------------------------------- hand-coded Peterson


def peterson_initial() -> list[tuple]:
    # (pc0, pc1, flag0, flag1, turn); turn starts as 0 or 1
    return [("idle", "idle", False, False, t) for t in (0, 1)]


def peterson_successors(state: tuple) -> list[tuple[str, tuple]]:
    pc0, pc1, f0, f1, turn = state
    out: list[tuple[str, tuple]] = []
    if pc0 == "idle":
        out.append(("p0_flag", ("set", pc1, True, f1, turn)))
    if pc0 == "set":
        out.append(("p0_turn", ("wait", pc1, f0, f1, 1)))
    if pc0 == "wait" and (not f1 or turn == 0):
        out.append(("p0_enter", ("crit", pc1, f0, f1, turn)))
    if pc0 == "crit":
        out.append(("p0_exit", ("idle", pc1, False, f1, turn)))
    if pc1 == "idle":
        out.append(("p1_flag", (pc0, "set", f0, True, turn)))
    if pc1 == "set":
        out.append(("p1_turn", (pc0, "wait", f0, f1, 0)))
    if pc1 == "wait" and (not f0 or turn == 1):
        out.append(("p1_enter", (pc0, "crit", f0, f1, turn)))
    if pc1 == "crit":
        out.append(("p1_exit", (pc0, "idle", f0, False, turn)))
    return out


def closure(
    initial: list[tuple], successors: Callable[[tuple], list[tuple[str, tuple]]]
) -> tuple[set[tuple], int]:
    """Reachable states and number of transitions, by plain depth-first search."""
    seen = set(initial)
    stack = list(initial)
    edges = 0
    while stack:
        state = stack.pop()
        for _, nxt in successors(state):
            edges += 1
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return seen, edges
