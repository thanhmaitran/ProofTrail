"""Allow ``python -m prooftrail``."""

from __future__ import annotations

import sys

from prooftrail.cli import main

sys.exit(main())
