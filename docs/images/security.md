# Security notes

Specification files are **untrusted input**. ProofTrail is designed so that
loading and checking a model cannot execute arbitrary code or exhaust memory
through the parser. The vulnerability reporting process is in `SECURITY.md` at
the repository root.

## Threat model

In scope: a malicious or malformed specification (YAML/JSON) or report file
handed to `prooftrail validate|check|explain|graph|corpus`.

Out of scope: a malicious *Python environment* (compromised dependencies), and
denial of service through a legitimately huge but valid state space beyond the
resource limits that the user chose.

## Controls

| Risk | Control |
|---|---|
| Code execution through expressions | No `eval`/`exec`. Expressions are parsed with `ast` (parsing executes nothing), every node type is checked against an allow-list, and the result is compiled to closures that can only read model variables. Calls, attribute access, subscripts, lambdas, comprehensions, `**`, `/` and bit operations are rejected. |
| Code execution through YAML | `SafeLoader` subclass only; no Python tags. |
| YAML alias expansion ("billion laughs") | Anchors and aliases are rejected outright. |
| Silent data loss | Duplicate mapping keys are errors in both YAML and JSON; `NaN`/`Infinity` are rejected in JSON; unknown keys are errors rather than ignored. |
| Oversized input | File size is capped (1 MB), as are nesting depth (24), total node count (50 000), variables (64), transitions (512), properties (256), labels (128), initial values per variable (1 000), enum values (64), formula length (2 000 characters) and formula depth (60). Report files for `explain` are size-capped as well. |
| Type confusion | `bool` is never accepted where an `int` is required (a classic Python pitfall because `True == 1`). Integer results are limited to 64 bits. |
| Unbounded exploration | `--max-states` (default 1 000 000), `--max-depth` and `--timeout`. A limit hit gives `UNKNOWN`, never `HOLDS`. Initial states are capped at 100 000. |
| Unsafe output paths | Output paths that are directories are refused; write failures surface as clear errors. Reports contain only model data and statistics, never environment variables or credentials. |
| Tampered reports | `explain --model` replays every stored trace against the model instead of trusting the report. |

## Continuous checks

* `tests/integration/test_security.py` and the `examples/invalid/` corpus cases
  (code injection, alias bombs, duplicate keys, bool-as-int, type errors).
* CI runs lint, tests and the corpus on every push and pull request with
  read-only repository permissions.

## Residual risks

* CPU time inside a single successor computation is not interruptible: the
  timeout is checked between state expansions.
* The tool trusts the Python standard library, PyYAML and the interpreter.
* A very large but valid report can use noticeable memory when explained.
