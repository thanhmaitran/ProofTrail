"""Breadth-first explicit-state exploration.

States are discovered level by level, so the discovery order is non-decreasing
in depth. Two consequences are relied on elsewhere:

* the first state found that satisfies a predicate is at minimum depth, so the
  trace reconstructed from predecessor links is a *shortest* path;
* the first deadlock state expanded is at minimum depth among deadlock states.

Time and memory are ``O(|V_r| + |E_r|)`` for ``V_r`` reachable states and
``E_r`` examined transitions (``O(|V_r|)`` extra for predecessors).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field

from prooftrail.errors import ProofTrailError
from prooftrail.exploration.indexing import StateIndex, make_index
from prooftrail.model.state import State
from prooftrail.model.system import TransitionSystem

StopFlag = bool
OnDiscover = Callable[[int, State], StopFlag]
OnExpand = Callable[[int, State, list[tuple[str, State]]], StopFlag]

STOP_MAX_STATES = "max_states"
STOP_MAX_DEPTH = "max_depth"
STOP_TIMEOUT = "timeout"
STOP_EARLY = "stopped_early"
STOP_ERROR = "error"

_TIMEOUT_CHECK_INTERVAL = 256


@dataclass(frozen=True)
class Limits:
    """Resource limits. ``None`` disables a limit.

    Hitting a limit never produces ``HOLDS``: unresolved properties become
    ``UNKNOWN``.
    """

    max_states: int | None = 1_000_000
    max_depth: int | None = None
    timeout_seconds: float | None = None

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "max_states": self.max_states,
            "max_depth": self.max_depth,
            "timeout_seconds": self.timeout_seconds,
        }


@dataclass
class ExplorationStats:
    states_explored: int = 0  # distinct states discovered and indexed
    states_expanded: int = 0  # states whose successors were generated
    transitions_examined: int = 0  # successor edges generated
    max_depth_reached: int = 0
    deadlock_states: int = 0  # expanded states with no enabled transition
    elapsed_seconds: float = 0.0

    def to_dict(self, *, include_timing: bool = True) -> dict[str, int | float]:
        data: dict[str, int | float] = {
            "states_explored": self.states_explored,
            "states_expanded": self.states_expanded,
            "transitions_examined": self.transitions_examined,
            "max_depth_reached": self.max_depth_reached,
            "deadlock_states": self.deadlock_states,
        }
        if include_timing:
            data["elapsed_seconds"] = round(self.elapsed_seconds, 6)
        return data


@dataclass
class Exploration:
    """Everything an exploration learned about the reachable state space."""

    index: StateIndex
    initial: list[int]
    depth: list[int]
    parent: list[int] | None
    parent_action: list[str | None] | None
    edges: list[list[tuple[str, int]]] | None
    complete: bool
    stop_reason: str | None
    stats: ExplorationStats = field(default_factory=ExplorationStats)
    error: ProofTrailError | None = None

    def state(self, index: int) -> State:
        return self.index.state(index)

    @property
    def exhaustive(self) -> bool:
        """True if every reachable state was discovered and expanded."""
        return self.complete


def explore(
    system: TransitionSystem,
    *,
    limits: Limits | None = None,
    indexing: str = "object",
    track_parents: bool = True,
    store_edges: bool = False,
    on_discover: OnDiscover | None = None,
    on_expand: OnExpand | None = None,
) -> Exploration:
    """Explore the reachable states of ``system`` breadth-first.

    ``on_discover(index, state)`` is called once when a state is first found
    (initial states included); ``on_expand(index, state, successors)`` once when
    a state's successors have been generated. Either may return ``True`` to stop
    the exploration immediately (``stop_reason == "stopped_early"``).

    A modelling error raised by the system (``ModelError``) does not propagate:
    the exploration is returned as incomplete with ``error`` set and
    ``stop_reason == "error"``, so verdicts already established (for example a
    counterexample found before the error) are not lost.
    """
    limits = limits or Limits()
    start = time.perf_counter()
    index = make_index(indexing)
    stats = ExplorationStats()

    depth: list[int] = []
    parent: list[int] | None = [] if track_parents else None
    parent_action: list[str | None] | None = [] if track_parents else None
    edges: list[list[tuple[str, int]]] | None = [] if store_edges else None
    initial: list[int] = []
    queue: deque[int] = deque()
    stop_reason: str | None = None
    truncated = False

    def finish(reason: str | None, complete: bool) -> Exploration:
        stats.states_explored = len(index)
        stats.elapsed_seconds = time.perf_counter() - start
        return Exploration(
            index=index,
            initial=initial,
            depth=depth,
            parent=parent,
            parent_action=parent_action,
            edges=edges,
            complete=complete,
            stop_reason=reason,
            stats=stats,
        )

    try:
        # -- initial states ---------------------------------------------------
        for state in system.initial_states():
            if limits.max_states is not None and len(index) >= limits.max_states:
                return finish(STOP_MAX_STATES, False)
            i, is_new = index.add(state)
            if not is_new:
                continue
            depth.append(0)
            if parent is not None and parent_action is not None:
                parent.append(-1)
                parent_action.append(None)
            if edges is not None:
                edges.append([])
            initial.append(i)
            queue.append(i)
            if on_discover is not None and on_discover(i, state):
                return finish(STOP_EARLY, False)

        # -- main loop --------------------------------------------------------
        max_states = limits.max_states
        max_depth = limits.max_depth
        timeout = limits.timeout_seconds
        expansions = 0

        while queue:
            if timeout is not None:
                expansions += 1
                if (
                    expansions % _TIMEOUT_CHECK_INTERVAL == 0
                    and time.perf_counter() - start > timeout
                ):
                    return finish(STOP_TIMEOUT, False)

            i = queue.popleft()
            state = index.state(i)
            d = depth[i]
            successors = system.successors(state)
            stats.states_expanded += 1
            stats.transitions_examined += len(successors)
            if d > stats.max_depth_reached:
                stats.max_depth_reached = d
            if not successors:
                stats.deadlock_states += 1
            if on_expand is not None and on_expand(i, state, successors):
                return finish(STOP_EARLY, False)

            for action, nxt in successors:
                j = index.lookup(nxt)
                if j is None:
                    if max_states is not None and len(index) >= max_states:
                        return finish(STOP_MAX_STATES, False)
                    if max_depth is not None and d >= max_depth:
                        truncated = True
                        continue
                    j, _ = index.add(nxt)
                    depth.append(d + 1)
                    if parent is not None and parent_action is not None:
                        parent.append(i)
                        parent_action.append(action)
                    if edges is not None:
                        edges.append([])
                    queue.append(j)
                    if edges is not None:
                        edges[i].append((action, j))
                    if on_discover is not None and on_discover(j, nxt):
                        return finish(STOP_EARLY, False)
                elif edges is not None:
                    edges[i].append((action, j))

        if truncated:
            stop_reason = STOP_MAX_DEPTH
        return finish(stop_reason, not truncated)
    except ProofTrailError as exc:
        result = finish(STOP_ERROR, False)
        result.error = exc
        return result
