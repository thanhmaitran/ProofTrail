# Limitations

Being explicit about limits is part of the tool's contract.

## Modelling

* A verdict is about the **model**, not about the real system. Modelling errors
  — a missing transition, an overly strong guard — are invisible to the checker.
* Variables are `integer`, `boolean` and `enum` only. No arrays, records,
  strings with operations, or unbounded data structures.
* Integers must stay within 64 bits; an update that leaves a declared domain is
  a modelling error and gives `ERROR`.
* Models are asynchronous interleavings of guarded atomic updates. There are no
  built-in notions of processes, channels, time or probabilities.
* Unbounded integers without both bounds are accepted but exploration is only
  guaranteed to finish when guards bound them. `validate` prints a note.

## Logic

* Supported temporal logic: CTL (`EX AX EF AF EG AG`, `E[·U·]`, `A[·U·]`) and
  the `response` shorthand. There is **no LTL**, no CTL*, and no fairness
  constraints.
* Without fairness, `AF` and `response` properties fail whenever any path
  avoids the goal forever, including paths that starve an enabled action.
  Fairness must be encoded in the model.
* Temporal properties need the whole reachable graph in memory; they return
  `UNKNOWN` if a limit stops exploration.
* Traces for CTL are produced only for violated `AG`, violated `AF` and holding
  `EF`. Other violated formulas give the verdict without a trace.

## Scale

* Explicit-state exploration suffers **state-space explosion**: the number of
  states grows exponentially with the number of variables. The benchmarks show
  roughly 60 MiB of traced memory for 65 536 states of a 8-variable model.
* The engine is pure Python and is not tuned for speed. Millions of states are
  possible but slow; it is intended for small and medium models and for
  teaching and research use.
* No symmetry reduction, partial-order reduction, symbolic (BDD/SAT) methods or
  parallelism are implemented. The two indexing strategies and the successor
  cache are engineering options, not state-space reductions, and they never
  change a verdict.

## Verification of the tool itself

* The test suite and differential references give strong evidence but not
  proof of correctness.
* Cross-checking against an independent tool such as TLC is not done; it would
  require translating models and verifying the translation.

## Possible future work

* LTL via Büchi automata and nested DFS; fairness constraints.
* Symmetry and partial-order reduction with a stated preservation argument and
  differential tests against the unreduced search.
* An interactive web viewer on top of the JSON report.
* Cross-validation against TLC on models with identical semantics.
