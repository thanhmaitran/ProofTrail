"""Randomised differential tests: the engine versus independent references.

``hypothesis`` is listed in the ``dev`` extra, but these tests deliberately use a
seeded ``random.Random`` instead: every case is reproducible from its seed (it
is part of every assertion message), the suite has no extra dependency, and the
generators live in ``tests/support.py`` next to the reference algorithms.

References used (none of them import the engine's search or CTL code):

* reachability  - plain set closure
* shortest path - ``networkx.single_source_shortest_path_length``
* deadlocks     - out-degree 0 among reachable states
* CTL           - naive iteration to a fixed point on a totalized graph, with
                  ``AF`` computed as a *least* fixed point (the engine
                  computes it as the dual of ``EG``)
"""

from __future__ import annotations

import random

import pytest
from support import (
    atom,
    closure,
    deadlocks,
    graph_system,
    peterson_initial,
    peterson_successors,
    random_formula,
    random_graph,
    reachable,
    sat,
    shortest_distance,
    to_ctl_text,
    totalized,
)

from prooftrail.exploration.bfs import Limits, explore
from prooftrail.model.expressions import BOOL, compile_expression
from prooftrail.model.parser import load_specification
from prooftrail.properties.ctl import CTLProperty, parse_ctl
from prooftrail.properties.deadlock import DeadlockFreedom
from prooftrail.properties.invariant import InvariantProperty
from prooftrail.properties.reachability import ReachabilityProperty
from prooftrail.verification.counterexample import validate_trace
from prooftrail.verification.engine import verify
from prooftrail.verification.results import Status

SEEDS = range(250)
SCOPE = {"s": "int"}


def expr(source: str):
    return compile_expression(source, SCOPE, expected=BOOL)


def members_of(system_states, explorations) -> set[int]:
    return {explorations.state(i)["s"] for i in range(len(explorations.index))}


@pytest.mark.parametrize("seed", SEEDS)
def test_exploration_finds_exactly_the_reachable_states(seed: int) -> None:
    rng = random.Random(seed)
    graph = random_graph(rng)
    result = explore(graph_system(graph), store_edges=True)
    expected = reachable(graph)
    assert result.complete, f"seed={seed}"
    assert {result.state(i)["s"] for i in range(len(result.index))} == expected, (
        f"seed={seed}"
    )
    assert result.stats.states_explored == len(expected)
    out_edges = sum(1 for a, _ in graph.edges if a in expected)
    assert result.stats.transitions_examined == out_edges, f"seed={seed}"
    assert result.stats.deadlock_states == len(deadlocks(graph)), f"seed={seed}"


@pytest.mark.parametrize("seed", SEEDS)
def test_invariant_verdict_and_shortest_counterexample(seed: int) -> None:
    rng = random.Random(1000 + seed)
    graph = random_graph(rng)
    bad = {i for i in range(graph.n) if rng.random() < 0.25}
    system = graph_system(graph)
    prop = InvariantProperty("inv", expression=expr(f"not ({atom(bad)})"))
    result = verify(system, [prop]).results[0]

    distance = shortest_distance(graph, bad & reachable(graph))
    if distance is None:
        assert result.status is Status.HOLDS, f"seed={seed}"
        assert result.counterexample is None
        return
    assert result.status is Status.VIOLATED, f"seed={seed}"
    trace = result.counterexample
    assert trace is not None
    assert len(trace.steps) - 1 == distance, f"seed={seed}: not a shortest trace"
    assert trace.last.state["s"] in bad
    assert all(step.state["s"] not in bad for step in trace.steps[:-1])
    validate_trace(system, trace)


@pytest.mark.parametrize("seed", SEEDS)
def test_reachability_verdict_and_shortest_witness(seed: int) -> None:
    rng = random.Random(2000 + seed)
    graph = random_graph(rng)
    target = {i for i in range(graph.n) if rng.random() < 0.3}
    system = graph_system(graph)
    result = verify(
        system, [ReachabilityProperty("r", target=expr(atom(target)))]
    ).results[0]
    distance = shortest_distance(graph, target & reachable(graph))
    if distance is None:
        assert result.status is Status.VIOLATED and result.reason == "unreachable", (
            f"seed={seed}"
        )
        return
    assert result.status is Status.HOLDS, f"seed={seed}"
    assert result.witness is not None and len(result.witness.steps) - 1 == distance
    validate_trace(system, result.witness)


@pytest.mark.parametrize("seed", SEEDS)
def test_deadlock_verdict_and_shortest_counterexample(seed: int) -> None:
    rng = random.Random(3000 + seed)
    graph = random_graph(rng)
    system = graph_system(graph)
    stuck = deadlocks(graph)
    allowed = {i for i in stuck if rng.random() < 0.5}
    plain = verify(system, [DeadlockFreedom("d")]).results[0]
    lenient = verify(
        system, [DeadlockFreedom("d", unless=expr(atom(allowed)))]
    ).results[0]

    for result, relevant in ((plain, stuck), (lenient, stuck - allowed)):
        distance = shortest_distance(graph, relevant)
        if distance is None:
            assert result.status is Status.HOLDS, f"seed={seed}"
        else:
            assert result.status is Status.VIOLATED and result.reason == "deadlock", (
                f"seed={seed}"
            )
            assert result.counterexample is not None
            assert len(result.counterexample.steps) - 1 == distance, f"seed={seed}"
            validate_trace(system, result.counterexample)


@pytest.mark.parametrize("seed", SEEDS)
def test_ctl_matches_the_naive_fixed_point_reference(seed: int) -> None:
    rng = random.Random(4000 + seed)
    graph = random_graph(rng, max_states=8)
    formula = random_formula(rng, graph.n, depth=3)
    text = to_ctl_text(formula)
    system = graph_system(graph)
    prop = CTLProperty("f", formula=parse_ctl(text, SCOPE), source=text)
    result = verify(system, [prop]).results[0]

    satisfied = sat(formula, totalized(graph))
    expected_holds = set(graph.initial) <= satisfied
    assert (result.status is Status.HOLDS) == expected_holds, (
        f"seed={seed} formula={text}"
    )
    assert result.status in (Status.HOLDS, Status.VIOLATED)
    for trace in (result.counterexample, result.witness):
        if trace is not None:
            validate_trace(system, trace)


@pytest.mark.parametrize("seed", range(120))
def test_temporal_dualities_hold_on_random_graphs(seed: int) -> None:
    """AF p == not EG not p ; AG p == not EF not p ; AX p == not EX not p."""
    rng = random.Random(5000 + seed)
    graph = random_graph(rng, max_states=8)
    tot = totalized(graph)
    p = ("atom", frozenset(i for i in range(graph.n) if rng.random() < 0.5))
    states = set(tot)
    assert sat(("AF", p), tot) == states - sat(("EG", ("not", p)), tot)
    assert sat(("AG", p), tot) == states - sat(("EF", ("not", p)), tot)
    assert sat(("AX", p), tot) == states - sat(("EX", ("not", p)), tot)


@pytest.mark.parametrize("seed", range(100))
def test_declaration_order_never_changes_results(seed: int) -> None:
    rng = random.Random(6000 + seed)
    graph = random_graph(rng)
    target = {i for i in range(graph.n) if rng.random() < 0.3}
    props = [
        InvariantProperty("i", expression=expr(f"not ({atom(target)})")),
        ReachabilityProperty("r", target=expr(atom(target))),
        DeadlockFreedom("d"),
    ]
    base = verify(graph_system(graph), props)
    shuffled = verify(graph_system(graph, shuffle=random.Random(seed)), props)

    def summary(run):
        return [
            (
                r.property_id,
                r.status,
                len(r.counterexample.steps) if r.counterexample else None,
            )
            for r in run.results
        ]

    assert summary(base) == summary(shuffled), f"seed={seed}"
    # With early stopping, how many states were discovered before every
    # property was decided legitimately depends on successor order; only an
    # exhaustive exploration must agree on the count.
    if base.exhaustive and shuffled.exhaustive:
        assert (
            base.statistics["states_explored"] == shuffled.statistics["states_explored"]
        )


@pytest.mark.parametrize("seed", range(100))
def test_indexing_strategy_and_cache_never_change_results(seed: int) -> None:
    rng = random.Random(7000 + seed)
    graph = random_graph(rng, max_states=8)
    target = {i for i in range(graph.n) if rng.random() < 0.3}
    text = to_ctl_text(random_formula(rng, graph.n, depth=2))
    props = [
        InvariantProperty("i", expression=expr(f"not ({atom(target)})")),
        ReachabilityProperty("r", target=expr(atom(target))),
        DeadlockFreedom("d"),
        CTLProperty("c", formula=parse_ctl(text, SCOPE), source=text),
    ]
    system = graph_system(graph)

    def summary(**options):
        run = verify(system, props, **options)
        return [
            (
                r.property_id,
                r.status,
                len(r.counterexample.steps) if r.counterexample else None,
                len(r.witness.steps) if r.witness else None,
            )
            for r in run.results
        ]

    reference = summary()
    for indexing in ("object", "packed"):
        for strategy in ("shared", "per-property"):
            for cache in (False, True):
                got = summary(
                    indexing=indexing, strategy=strategy, successor_cache=cache
                )
                assert got == reference, (
                    f"seed={seed} {indexing} {strategy} cache={cache}"
                )


@pytest.mark.parametrize("seed", range(150))
def test_a_resource_limit_never_produces_holds_for_an_incomplete_search(
    seed: int,
) -> None:
    rng = random.Random(8000 + seed)
    graph = random_graph(rng, max_states=12)
    while len(reachable(graph)) < 2:  # redraw: a limit needs something to cut
        graph = random_graph(rng, max_states=12)
    reach = reachable(graph)
    limit = rng.randint(1, len(reach) - 1)
    # An invariant that is true everywhere: a complete search says HOLDS, an
    # incomplete one must say UNKNOWN (never HOLDS).
    prop = InvariantProperty("inv", expression=expr("s >= 0"))
    result = verify(
        graph_system(graph), [prop], limits=Limits(max_states=limit)
    ).results[0]
    assert result.status is Status.UNKNOWN, f"seed={seed} limit={limit}"
    full = verify(
        graph_system(graph), [prop], limits=Limits(max_states=len(reach))
    ).results[0]
    assert full.status is Status.HOLDS, f"seed={seed}"


@pytest.mark.parametrize("seed", range(60))
def test_every_truncated_prefix_is_a_subset_of_the_true_state_space(seed: int) -> None:
    rng = random.Random(9000 + seed)
    graph = random_graph(rng, max_states=12)
    reach = reachable(graph)
    limit = rng.randint(1, len(reach))
    result = explore(graph_system(graph), limits=Limits(max_states=limit))
    found = {result.state(i)["s"] for i in range(len(result.index))}
    assert found <= reach and len(found) == min(limit, len(reach)), f"seed={seed}"


# --------------------------------------------------------- Peterson reference


def test_peterson_matches_a_hand_coded_independent_implementation(examples_dir) -> None:
    spec = load_specification(examples_dir / "peterson" / "model.yaml")
    result = explore(spec.system, store_edges=True)
    states, edges = closure(peterson_initial(), peterson_successors)

    engine_states = {
        (
            s["pc0"],
            s["pc1"],
            s["flag0"],
            s["flag1"],
            s["turn"],
        )
        for s in (result.state(i) for i in range(len(result.index)))
    }
    assert engine_states == states
    assert result.stats.transitions_examined == edges
    assert result.complete


def test_peterson_safety_agrees_with_the_hand_coded_model(examples_dir) -> None:
    spec = load_specification(examples_dir / "peterson" / "model.yaml")
    states, _ = closure(peterson_initial(), peterson_successors)
    assert not any(s[0] == "crit" and s[1] == "crit" for s in states)
    run = verify(spec.system, spec.properties)
    by_id = {r.property_id: r.status for r in run.results}
    assert by_id["mutual-exclusion"] is Status.HOLDS
    assert by_id["deadlock-free"] is Status.HOLDS
    assert by_id["p0-can-enter"] is Status.HOLDS
