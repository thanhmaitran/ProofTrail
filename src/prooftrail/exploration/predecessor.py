"""Predecessor links: reconstructing paths from the BFS parent tree."""

from __future__ import annotations

from prooftrail.exploration.bfs import Exploration


def path_to(exploration: Exploration, target: int) -> list[tuple[int, str | None]]:
    """Return ``[(state index, action that led to it), ...]`` from an initial
    state to ``target``. The first action is ``None``.

    Requires the exploration to have been run with ``track_parents=True``. The
    path is a shortest one because the parent of every state is the state from
    which it was first discovered in breadth-first order.
    """
    if exploration.parent is None or exploration.parent_action is None:
        raise ValueError("The exploration did not track predecessors.")
    path: list[tuple[int, str | None]] = []
    node = target
    while node != -1:
        path.append((node, exploration.parent_action[node]))
        node = exploration.parent[node]
    path.reverse()
    return path
