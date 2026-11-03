"""Shared fixtures and helpers for the test suite."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"

# Make ``import tests_support`` style helpers importable without packaging.
sys.path.insert(0, str(Path(__file__).parent))


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture(scope="session")
def root_dir() -> Path:
    return ROOT


@pytest.fixture()
def spec_file(tmp_path: Path):
    """Write specification text to a temporary file and return its path."""

    def write(text: str, name: str = "model.yaml") -> Path:
        path = tmp_path / name
        path.write_text(text, encoding="utf-8")
        return path

    return write
