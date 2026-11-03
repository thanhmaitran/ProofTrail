"""Synthetic model generators for the ProofTrail benchmarks.

Every generator returns a plain specification ``dict`` (the same structure a
YAML/JSON file parses to) together with a closed-form description of the state
space, so a benchmark can check the engine's counts against an *independent*
formula instead of trusting its own output.

Families
--------
``grid(n, k)``
    ``n`` independent counters, each in ``0..k-1``. Reachable states: ``k**n``
    (exponential growth with the number of variables). Transitions examined:
    ``n * k**n`` minus the guarded-out ones, see :func:`grid_expected`.
``chain(length)``
    One counter walking ``0..length``. Reachable states: ``length + 1``; the
    counterexample for ``counter <= length - 1`` is exactly ``length`` steps
    long, which isolates the cost of long traces.
``token_ring(n)``
    ``n`` processes passing a single token. Reachable states: ``n``. Used for
    models with many actions but a tiny state space.
``many_properties(base, count)``
    Adds ``count`` always-true invariants to a model, to measure how the cost
    grows with the number of properties.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1.0"


@dataclass(frozen=True)
class Expected:
    """Closed-form facts about a generated model, derived without the engine."""

    states: int
    transitions: int
    max_depth: int


def grid(n: int, k: int, *, invariant: str | None = None) -> dict[str, Any]:
    """``n`` counters in ``0..k-1``; each can be incremented or decremented."""
    if n < 1 or k < 2:
        raise ValueError("grid needs n >= 1 and k >= 2")
    variables = {
        f"c{i}": {"type": "integer", "min": 0, "max": k - 1, "initial": 0}
        for i in range(n)
    }
    transitions: list[dict[str, Any]] = []
    for i in range(n):
        transitions.append(
            {
                "id": f"inc{i}",
                "when": f"c{i} < {k - 1}",
                "updates": {f"c{i}": f"c{i} + 1"},
            }
        )
        transitions.append(
            {
                "id": f"dec{i}",
                "when": f"c{i} > 0",
                "updates": {f"c{i}": f"c{i} - 1"},
            }
        )
    expression = invariant or " and ".join(f"c{i} >= 0" for i in range(n))
    return {
        "schema_version": SCHEMA_VERSION,
        "model": {
            "id": f"grid-{n}x{k}",
            "description": f"{n} independent counters in 0..{k - 1}.",
            "variables": variables,
            "transitions": transitions,
        },
        "properties": [{"id": "inv", "type": "invariant", "expression": expression}],
    }


def grid_expected(n: int, k: int) -> Expected:
    """States, edges and BFS depth of :func:`grid`, from the product structure.

    Each counter has ``k`` values and ``2 * (k - 1)`` enabled moves summed over
    all its values (``k - 1`` increments, ``k - 1`` decrements). Across the
    product, every move of one counter combines with the ``k**(n-1)`` values of
    the others.
    """
    states = k**n
    transitions = n * 2 * (k - 1) * k ** (n - 1)
    return Expected(states=states, transitions=transitions, max_depth=n * (k - 1))


def chain(length: int, *, violated: bool = False) -> dict[str, Any]:
    """A counter that walks ``0..length``.

    With ``violated=True`` the invariant ``counter < length`` fails exactly
    ``length`` steps from the initial state.
    """
    if length < 1:
        raise ValueError("chain needs length >= 1")
    bound = length if violated else length + 1
    return {
        "schema_version": SCHEMA_VERSION,
        "model": {
            "id": f"chain-{length}{'-bad' if violated else ''}",
            "description": f"A counter walking from 0 to {length}.",
            "variables": {
                "counter": {
                    "type": "integer",
                    "min": 0,
                    "max": length,
                    "initial": 0,
                }
            },
            "transitions": [
                {
                    "id": "step",
                    "when": f"counter < {length}",
                    "updates": {"counter": "counter + 1"},
                }
            ],
        },
        "properties": [
            {"id": "bound", "type": "invariant", "expression": f"counter < {bound}"}
        ],
    }


def chain_expected(length: int) -> Expected:
    return Expected(states=length + 1, transitions=length, max_depth=length)


def token_ring(n: int) -> dict[str, Any]:
    """``n`` processes in a ring; the token moves to the next process."""
    if n < 2:
        raise ValueError("token_ring needs n >= 2")
    transitions = [
        {
            "id": f"pass{i}",
            "when": f"holder == {i}",
            "updates": {"holder": f"{(i + 1) % n}"},
        }
        for i in range(n)
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "model": {
            "id": f"token-ring-{n}",
            "description": f"A token circulating among {n} processes.",
            "variables": {
                "holder": {"type": "integer", "min": 0, "max": n - 1, "initial": 0}
            },
            "transitions": transitions,
        },
        "properties": [
            {"id": "valid", "type": "invariant", "expression": f"holder < {n}"}
        ],
    }


def token_ring_expected(n: int) -> Expected:
    return Expected(states=n, transitions=n, max_depth=n - 1)


def many_properties(base: dict[str, Any], count: int) -> dict[str, Any]:
    """Copy ``base`` and add ``count`` always-true invariants.

    The invariants are all distinct expressions over the first variable so the
    verifier cannot merge them.
    """
    spec = copy.deepcopy(base)
    variable = next(iter(spec["model"]["variables"]))
    spec["properties"] = [
        {
            "id": f"extra{i}",
            "type": "invariant",
            "expression": f"{variable} >= -{i + 1}",
        }
        for i in range(count)
    ]
    return spec
