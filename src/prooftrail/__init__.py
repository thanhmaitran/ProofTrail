"""ProofTrail: an explicit-state model checking and formal verification workbench."""

from __future__ import annotations

__version__ = "0.1.0"

from prooftrail.errors import ProofTrailError, SpecError  # noqa: E402
from prooftrail.model.parser import (  # noqa: E402
    Specification,
    load_specification,
    parse_specification,
)
from prooftrail.verification.engine import verify  # noqa: E402
from prooftrail.verification.results import Status  # noqa: E402

__all__ = [
    "ProofTrailError",
    "SpecError",
    "Specification",
    "Status",
    "__version__",
    "load_specification",
    "parse_specification",
    "verify",
]
