# 0001. Explicit-state search in pure Python first

**Status:** accepted

## Context
The project needs a verification engine whose algorithms are visible and
testable. Symbolic methods or a compiled core would add complexity before the
semantics were settled.

## Decision
Implement an explicit-state, breadth-first engine in pure Python. Add compiled
code only if a measured bottleneck justifies it.

## Consequences
Counterexamples are shortest by construction and every algorithm is readable.
Scale is limited to small and medium state spaces (see `docs/limitations.md`).
