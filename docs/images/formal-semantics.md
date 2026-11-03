# Formal semantics

This document defines precisely what ProofTrail's verdicts mean. A verdict is a
statement about the **model as written**, under the semantics below. It is not a
statement about any real system the model is meant to describe.

## 1. Transition systems

A specification denotes a finite (or bounded) transition system
`M = (S, S0, A, T, L)`:

* `S` — states: total assignments of values to the declared variables. Variables
  are `integer` (optionally bounded by `min`/`max`), `boolean` or `enum`.
* `S0` — initial states: the Cartesian product of every variable's `initial`
  values (each variable may list several).
* `A` — actions: the transition ids.
* `T ⊆ S × A × S` — for each declared transition `t` and state `s` where the
  guard `when` holds, there is exactly one edge `(s, t, s')`. All `updates` are
  evaluated against the **pre-state** simultaneously; variables not mentioned
  keep their value. Distinct transitions give distinct edges even if they lead
  to the same state (multi-edges are kept). Non-determinism is expressed by
  several transitions enabled in the same state, or by several initial values.
* `L` — labels: named Boolean expressions declared in `model.labels`.

A transition whose update leaves a variable's declared domain, divides by zero
or overflows 64 bits is a **modelling error** (see §5), not a disabled
transition.

## 2. Expression language

Expressions are parsed with Python's `ast` module (parsing executes nothing),
checked against an allow-list and compiled to closures. Types are `int`, `bool`
and `str` (enum literals); **`bool` is never an `int`**, so `true + 1` is a type
error. Supported: literals, variables, `not and or` (short-circuit), `+ - * // %`
(floor semantics), comparisons (chains allowed), `in` / `not in` with constant
members and conditional expressions. Everything else is rejected.

## 3. Properties

| Type | Meaning |
|---|---|
| `invariant` | `AG φ` restricted to a state predicate: every reachable state satisfies `φ`. |
| `reachability` | Some reachable state satisfies the target. The evidence is a shortest **witness** path. |
| `deadlock_free` | No reachable state has zero enabled transitions, except states satisfying the optional `unless`. |
| `ctl` | A CTL formula, true iff it holds in **every initial state**. |
| `response` | `AG (trigger → AF response)`, with a lasso counterexample. |

### CTL

Atoms are state expressions in braces (`{counter == 3}`) or declared labels.
Operators: `! && || ->`, `EX AX EF AF EG AG`, `E[φ U ψ]`, `A[φ U ψ]`, `true`,
`false`. The checker computes satisfaction sets by backward fixed points over
predecessor sets: a least fixed point for `E[φ U ψ]` (and so `EF`), a greatest
fixed point for `EG`, and the dual operators (`AX`, `AF`, `AG`, `A[φ U ψ]`) by
negation.

### Deadlocks and totalization

CTL is defined over infinite paths. ProofTrail therefore **totalizes** the graph
for temporal properties: every state with no enabled transition receives an
implicit self-loop labelled `<stutter>`. Consequences:

* `AF p` fails if a deadlock state is reachable on a path where `p` never holds
  (the system "stays" in the deadlock forever).
* `EG p` can hold on a deadlock state satisfying `p`.
* Deadlock detection (`deadlock_free`) uses the **original** graph, so it is
  unaffected by totalization.

### No fairness

No fairness assumption is made. `AF p` fails if *some* path avoids `p` forever,
even when that path ignores an enabled action forever. If a result depends on a
fairness assumption, the model must encode it.

## 4. Verdicts

| Status | Meaning |
|---|---|
| `HOLDS` | Confirmed for the model under these semantics. |
| `VIOLATED` | A valid counterexample exists — or, for `reachability`, the target is provably unreachable after exhaustive exploration. |
| `UNKNOWN` | Inconclusive: a resource limit (`max_states`, `max_depth`, `timeout`) was hit before a verdict. |
| `ERROR` | The run failed (e.g. a modelling error). Never a verdict on the model. |

Rules enforced by the engine:

1. `HOLDS` for a safety property (invariant, deadlock freedom) requires an
   **exhaustive** exploration. Hitting a limit gives `UNKNOWN`, never `HOLDS`.
2. A counterexample found is conclusive even if a limit was also hit.
3. A reachability target that is found gives `HOLDS` with a witness; a target
   not found after exhaustive exploration gives `VIOLATED` (reason `unreachable`);
   not found under a limit gives `UNKNOWN`.
4. Temporal properties are evaluated only on a complete graph; otherwise
   `UNKNOWN`.
5. A parse or validation failure is an error, never a violated property.

Process exit codes: `0` all properties hold, `1` some violated, `2` error,
`3` only inconclusive results.

## 5. Counterexamples

* Safety counterexamples are finite traces from an initial state. Because
  exploration is breadth-first, they have the **minimum possible number of
  steps** among violating traces.
* Liveness-style counterexamples (`AF`, `response`) are **lasso** traces: a
  finite prefix followed by a cycle back to an earlier step.
* Every trace is validated against the transition system before it is
  reported, and `prooftrail explain --model` can replay a saved report.
* For CTL formulas a trace is offered only for the shapes where it is a faithful
  explanation (violated `AG`, violated `AF`, holding `EF`). Other shapes return
  the verdict without a trace instead of a misleading one.
