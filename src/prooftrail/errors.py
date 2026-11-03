"""Exception hierarchy.

Errors are never swallowed into an ordinary verification verdict: a malformed
specification or a modelling error surfaces as an exception (or as an explicit
``ERROR`` status in a result), never as ``HOLDS`` or ``VIOLATED``.
"""

from __future__ import annotations


class ProofTrailError(Exception):
    """Base class for every error raised deliberately by ProofTrail."""


class SpecError(ProofTrailError):
    """The specification is malformed, ill-typed or exceeds a safety limit."""


class ExpressionError(SpecError):
    """An expression is syntactically invalid, ill-typed or unsupported."""


class ModelError(ProofTrailError):
    """A modelling error discovered while exploring.

    Examples: an update assigns a value outside a variable's declared domain,
    an integer expression divides by zero, or an integer exceeds 64 bits.
    """


class TraceError(ProofTrailError):
    """A counterexample or witness trace failed independent validation."""
