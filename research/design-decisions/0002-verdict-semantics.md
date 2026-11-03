# 0002. Four verdicts and no silent success

**Status:** accepted

## Context
Returning a Boolean hides the difference between "proved", "refuted" and "ran
out of budget".

## Decision
Use `HOLDS`, `VIOLATED`, `UNKNOWN` and `ERROR`. `HOLDS` for safety needs an
exhaustive exploration; a limit hit gives `UNKNOWN`; parse and modelling errors
are never verdicts. Exit codes mirror the four outcomes.

## Consequences
Callers must handle `UNKNOWN`. Pinned regression tests guard the boundary
(`max_states` equal to, and one below, the state count).
