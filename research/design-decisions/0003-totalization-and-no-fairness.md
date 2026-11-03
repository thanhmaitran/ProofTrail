# 0003. Stutter totalization and no fairness

**Status:** accepted

## Context
CTL is defined over infinite paths, but finite models can have deadlock states.

## Decision
For temporal properties, add an implicit `<stutter>` self-loop to every
deadlock state. Deadlock freedom is checked on the original graph. No fairness
assumption is made; users encode fairness in the model.

## Consequences
`AF p` fails if a reachable deadlock avoids `p`. Results are predictable and
documented in `docs/formal-semantics.md`.
