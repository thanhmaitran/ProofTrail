"""The benchmark generators must match their closed-form descriptions."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "benchmarks"))

import generators as gen  # noqa: E402

from prooftrail import parse_specification, verify  # noqa: E402
from prooftrail.verification.results import Status  # noqa: E402


def _stats(spec: dict) -> dict:
    parsed = parse_specification(spec)
    return verify(parsed.system, parsed.properties).statistics


@pytest.mark.parametrize(("n", "k"), [(1, 2), (2, 3), (3, 4), (4, 3)])
def test_grid_matches_formula(n: int, k: int) -> None:
    expected = gen.grid_expected(n, k)
    stats = _stats(gen.grid(n, k))
    assert stats["states_explored"] == expected.states
    assert stats["transitions_examined"] == expected.transitions
    assert stats["max_depth_reached"] == expected.max_depth


@pytest.mark.parametrize("length", [1, 5, 40])
def test_chain_matches_formula(length: int) -> None:
    expected = gen.chain_expected(length)
    stats = _stats(gen.chain(length))
    assert stats["states_explored"] == expected.states
    assert stats["transitions_examined"] == expected.transitions


@pytest.mark.parametrize("length", [1, 7, 30])
def test_violated_chain_has_a_counterexample_of_exact_length(length: int) -> None:
    parsed = parse_specification(gen.chain(length, violated=True))
    result = verify(parsed.system, parsed.properties).results[0]
    assert result.status is Status.VIOLATED
    assert len(result.counterexample) - 1 == length


@pytest.mark.parametrize("n", [2, 3, 9])
def test_token_ring_matches_formula(n: int) -> None:
    expected = gen.token_ring_expected(n)
    stats = _stats(gen.token_ring(n))
    assert stats["states_explored"] == expected.states
    assert stats["transitions_examined"] == expected.transitions


def test_many_properties_are_all_checked_and_hold() -> None:
    parsed = parse_specification(gen.many_properties(gen.grid(2, 3), 6))
    run = verify(parsed.system, parsed.properties)
    assert len(run.results) == 6
    assert all(r.status is Status.HOLDS for r in run.results)


@pytest.mark.parametrize(
    "call", [lambda: gen.grid(0, 3), lambda: gen.grid(2, 1), lambda: gen.chain(0)]
)
def test_invalid_generator_arguments_are_rejected(call) -> None:
    with pytest.raises(ValueError):
        call()
