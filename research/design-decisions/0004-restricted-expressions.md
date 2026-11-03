# 0004. Restricted expression language, never `eval`

**Status:** accepted

## Decision
Parse expressions with `ast`, check every node against an allow-list and compile
to closures. `bool` is not an `int`. Integers are limited to 64 bits.

## Consequences
A model file cannot run code. Some convenient Python features (calls, `/`,
`**`) are intentionally unavailable.
