# Correctness argument and test strategy

Passing tests is evidence, not proof. This document states what ProofTrail
claims, why the implementation should satisfy it, and exactly how each claim is
checked. It deliberately does **not** claim formal verification of the tool
itself.

## Claims

| # | Claim | Argument | Checked by |
|---|---|---|---|
| C1 | With no limit hit, exploration discovers exactly the reachable states. | BFS with a visited index enqueues each new state once and expands every enqueued state. | Reference set-closure comparison on random graphs (`tests/properties`), closed-form counts in the benchmark generators. |
| C2 | A reported safety counterexample is a shortest violating trace. | BFS discovery order is non-decreasing in depth; the first violating state found has minimum depth; the trace follows predecessor links. | Comparison with `networkx` shortest-path distances; the corpus' `counterexample_length` expectations. |
| C3 | Every reported trace is a real execution. | Traces are validated step by step against the transition system before being reported. | `tests/unit/test_traces.py`, `verification.replay`, corruption tests that must be rejected. |
| C4 | `HOLDS` is never reported for an incomplete exploration. | Verdict rules in `verification.engine`: a limit hit without a verdict gives `UNKNOWN`. | Pinned guard tests (`tests/regression`), including the exact-limit boundary (`max_states` equal to and one below the state count). |
| C5 | CTL results equal the semantic definition. | Fixed-point computation over a totalized graph. | Differential tests against a deliberately naive reference (iterate the defining equation to a fixed point, `AF` as a *least* fixed point, whereas the engine uses the dual of `EG`), including random formulas over random graphs and the Peterson model. |
| C6 | Representation choices do not change results. | `object` and `packed` indexing give identical ids and order; successor caching memoises a pure function. | Tests comparing every option against the default; the benchmark runner also asserts engine counts against closed-form formulas for every case. |
| C7 | Variable declaration order does not change semantics. | States are stored as sorted name/value tuples. | `tests/unit/test_state.py`, parser tests. |
| C8 | A malformed or hostile specification is rejected, not interpreted. | Strict schema, allow-listed expressions, no `eval`, size and depth limits. | `examples/invalid/*` in the corpus, `tests/integration/test_security.py`. |

## Test layers

* **Unit** (`tests/unit`): states, expressions, parser, system, exploration,
  CTL, traces, reporting, visualization, CLI, corpus.
* **Integration** (`tests/integration`): specification file to verdict, report
  and replay; security behaviour of the whole pipeline.
* **Differential / randomized** (`tests/properties`): random graphs and
  formulas, every case reproducible from its seed. These deliberately use a
  seeded `random.Random` rather than Hypothesis so each failure message carries
  its seed and the suite needs no extra dependency.
* **Regression** (`tests/regression`): pinned guards for bugs and boundary
  conditions that were once wrong or easy to get wrong.
* **Corpus** (`examples/manifest.json`, `prooftrail corpus`): 33 models, each
  with a written `derivation` of the expected answer produced by hand rather
  than by running the tool, including 12 invalid specifications that must be
  rejected with a specific message.

## Independence of the references

A test that uses the engine's own algorithm to compute the expected value would
only detect nondeterminism. The references therefore avoid engine code:
set closure for reachability, `networkx` for shortest distances, out-degree for
deadlocks, and a naive fixed-point iteration for CTL.

## What is not claimed

* Coverage numbers are a quality-management signal. They are collected in CI
  (`pytest --cov`) and are not offered as evidence of correctness.
* No comparison against TLC or another external model checker is included.
  Doing it fairly requires translating models into another language, and the
  translation would itself need verifying; it is listed as future work in
  `limitations.md`.
* A verdict says nothing about whether the model faithfully describes a real
  system.
