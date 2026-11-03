# Algorithms

## Explicit-state breadth-first search

`prooftrail.exploration.bfs.explore` discovers states level by level from the
initial states. Every discovered state receives a dense integer id from a
`StateIndex`; the queue holds ids.

```
for s in initial states:  add(s); enqueue
while queue not empty:
    i = dequeue; succ = system.successors(state(i))
    for (action, s') in succ:
        j = lookup(s')
        if j is new: (limits checked) add(s'); parent[j] = i via action; enqueue
```

Properties used elsewhere:

* Discovery order is non-decreasing in depth, so the first state satisfying a
  predicate is at minimum depth and the trace rebuilt from `parent` links is a
  **shortest path**.
* With O(1) average hash operations, time and memory are
  `O(|V_r| + |E_r|)` for `V_r` reachable states and `E_r` examined transitions
  (`O(|V_r|)` extra for predecessor links).
* Limits (`max_states`, `max_depth`, `timeout`) stop the search and mark the
  exploration incomplete; the caller then reports `UNKNOWN`.
* A `ModelError` raised while generating successors ends the exploration as
  incomplete with `error` set, so verdicts decided earlier are kept.

## State indexing

* `object`: dictionary keyed by `State` objects.
* `packed`: dictionary keyed by bare value tuples; `State` objects are rebuilt
  on demand. Stores less per state; same ids, same order, same verdicts.

## Safety checking

For invariants, reachability and deadlock freedom the engine registers
callbacks on one shared exploration. It stops as soon as every safety property
is decided. The first violating state discovered (minimum depth) gives the
counterexample via predecessor links. For deadlock freedom the check runs when a
state is **expanded**, because that is when "no successor" is known.

`strategy="per-property"` instead runs one exploration per property (a
reference mode, and a benchmark baseline).

## CTL fixed points

For temporal properties the engine builds a `TotalGraph` (explored graph plus
stutter self-loops and predecessor lists), then computes satisfaction sets
`Sat(φ)` bottom-up:

* `Sat(EX φ) = pre∃(Sat φ)`, `Sat(AX φ) = ¬ pre∃(¬Sat φ)`
* `Sat(E[φ U ψ])`: least fixed point of `Z = Sat ψ ∪ (Sat φ ∩ pre∃ Z)`,
  computed with a worklist over predecessor lists
* `Sat(EF φ) = E[true U φ]`, `Sat(AF φ) = ¬Sat(EG ¬φ)`, `Sat(AG φ) = ¬Sat(EF ¬φ)`
* `Sat(A[φ U ψ]) = ¬( E[¬ψ U (¬φ ∧ ¬ψ)] ∪ EG ¬ψ )`
* Satisfaction sets are memoised per sub-formula, so shared sub-formulas are
  computed once
* `Sat(EG φ)`: greatest fixed point, computed by repeatedly removing states of
  `Sat φ` that have no successor inside the set (counter-based, linear in the
  number of edges)

A formula holds iff every initial state is in its satisfaction set.

## Counterexamples for temporal properties

* Violated `AG g`: shortest path to a state outside `Sat(g)`; if `g` is
  `AF h` (a response), the path is extended by a lasso avoiding `h` forever.
* Violated `AF g`: a lasso inside `Sat(EG ¬g)` from an initial state.
* Holding `EF g`: a shortest witness path.

Each trace goes through `validate_trace` plus a semantic check appropriate to
the property before it is reported.

## Complexity summary

| Task | Time | Extra memory |
|---|---|---|
| Reachable-set exploration | `O(V + E)` | `O(V)` index, `O(E)` if edges are stored |
| Invariant / reachability | `O(V·P + E)` for `P` properties | `O(V)` |
| CTL formula of size `f` | `O(f·(V + E))` | `O(f·V)` for satisfaction sets |
| Counterexample reconstruction | `O(length)` | `O(length)` |

`V` itself can grow exponentially in the number of variables
(state-space explosion). See `benchmark-methodology.md` and
`benchmarks/results/RESULTS.md` for measurements.
