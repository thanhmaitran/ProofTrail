"""Explicit-state exploration of transition systems."""

from __future__ import annotations

from prooftrail.exploration.bfs import Exploration, ExplorationStats, Limits, explore
from prooftrail.exploration.indexing import ObjectIndex, PackedIndex, make_index

__all__ = [
    "Exploration",
    "ExplorationStats",
    "Limits",
    "ObjectIndex",
    "PackedIndex",
    "explore",
    "make_index",
]
