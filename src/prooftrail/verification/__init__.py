"""Verification engine, results and counterexample handling."""

from __future__ import annotations

from prooftrail.verification.engine import (
    verify,
    verify_invariant,
    verify_property,
    verify_reachability,
)
from prooftrail.verification.results import (
    PropertyResult,
    Status,
    Step,
    Trace,
    VerificationRun,
)

__all__ = [
    "PropertyResult",
    "Status",
    "Step",
    "Trace",
    "VerificationRun",
    "verify",
    "verify_invariant",
    "verify_property",
    "verify_reachability",
]
