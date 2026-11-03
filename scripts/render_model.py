"""Render the reachable state space of a model to a file.

Usage::

    python scripts/render_model.py MODEL.yaml out.mmd
    python scripts/render_model.py MODEL.yaml out.dot --format dot
    python scripts/render_model.py MODEL.yaml out.png --format png

This is a thin wrapper around ``prooftrail graph``.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from prooftrail.cli import main as cli_main  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("model", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--format", choices=("dot", "mermaid", "png"), default="mermaid"
    )
    parser.add_argument("--max-nodes", type=int, default=200)
    args = parser.parse_args()
    return cli_main(
        [
            "graph",
            str(args.model),
            "--format",
            args.format,
            "--output",
            str(args.output),
            "--max-nodes",
            str(args.max_nodes),
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
