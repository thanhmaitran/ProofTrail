# Security policy

## Supported versions

Only the latest released version receives security fixes.

## Reporting a vulnerability

Please **do not** open a public issue for a security problem. Use GitHub's
private vulnerability reporting ("Security" tab, "Report a vulnerability") on
the repository. Include:

- a description of the problem and its impact,
- a minimal specification or report file that reproduces it,
- the ProofTrail and Python versions.

You can expect an acknowledgement within a few days. Fixes are developed
privately and released together with a regression test.

## Scope

ProofTrail treats specification and report files as untrusted input. The
following are considered vulnerabilities:

- executing code that originates from a specification or report,
- bypassing the expression allow-list, the size/depth limits or the
  alias/duplicate-key rejection,
- writing files outside the path the user asked for,
- a crash or unbounded resource use that the documented limits should prevent.

A model that is merely large (state-space explosion) is not a vulnerability;
use `--max-states`, `--max-depth` and `--timeout`.

Design details are in [docs/security.md](docs/security.md).
