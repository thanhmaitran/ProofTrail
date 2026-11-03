"""Result types: status, traces and per-property outcomes."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from prooftrail.errors import ProofTrailError
from prooftrail.model.state import State


class Status(StrEnum):
    """Outcome of checking one property.

    HOLDS     confirmed within the model and the stated semantics
    VIOLATED  a valid counterexample exists (or, for reachability, the target is
              provably unreachable after exhaustive exploration)
    UNKNOWN   inconclusive: a resource limit was hit or the feature is unsupported
    ERROR     the specification or the run failed; never a verdict on the model
    """

    HOLDS = "HOLDS"
    VIOLATED = "VIOLATED"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True)
class Step:
    """One position in a trace. ``action`` is ``None`` for the initial state."""

    index: int
    state: State
    action: str | None = None


@dataclass(frozen=True)
class Trace:
    """A finite path, or a lasso if ``loop_start`` is set.

    For a lasso the last state has a transition (``loop_action``) back to
    ``steps[loop_start]``, so the trace describes an infinite path.
    """

    steps: tuple[Step, ...]
    loop_start: int | None = None
    loop_action: str | None = None

    def __len__(self) -> int:
        return len(self.steps)

    @property
    def last(self) -> Step:
        return self.steps[-1]

    @property
    def is_lasso(self) -> bool:
        return self.loop_start is not None

    def to_dict(self) -> dict[str, Any]:
        steps: list[dict[str, Any]] = []
        for step in self.steps:
            item: dict[str, Any] = {"index": step.index}
            if step.action is not None:
                item["action"] = step.action
            item["state"] = step.state.as_dict()
            steps.append(item)
        data: dict[str, Any] = {"steps": steps}
        if self.loop_start is not None:
            data["loop"] = {"back_to": self.loop_start, "action": self.loop_action}
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Trace:
        try:
            steps = tuple(
                Step(
                    index=int(item["index"]),
                    state=State.from_dict(item["state"]),
                    action=item.get("action"),
                )
                for item in data["steps"]
            )
            loop = data.get("loop")
            loop_start = int(loop["back_to"]) if loop else None
            loop_action = loop.get("action") if loop else None
        except (KeyError, TypeError, ValueError) as exc:
            raise ProofTrailError(f"Malformed trace in report: {exc!r}") from None
        return cls(steps, loop_start, loop_action)


@dataclass
class PropertyResult:
    property_id: str
    kind: str
    status: Status
    description: str = ""
    reason: str | None = None
    message: str | None = None
    counterexample: Trace | None = None
    witness: Trace | None = None
    statistics: dict[str, Any] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        """The algorithm reached a conclusion (HOLDS or VIOLATED)."""
        return self.status in (Status.HOLDS, Status.VIOLATED)


@dataclass
class VerificationRun:
    """Results of verifying all requested properties of one model."""

    model_id: str
    results: list[PropertyResult]
    limits: dict[str, Any]
    indexing: str
    strategy: str
    statistics: dict[str, Any]
    exhaustive: bool
    stop_reason: str | None = None

    def counts(self) -> dict[str, int]:
        counts = {status.value.lower(): 0 for status in Status}
        for result in self.results:
            counts[result.status.value.lower()] += 1
        return counts

    def overall(self) -> Status:
        statuses = {r.status for r in self.results}
        for status in (Status.ERROR, Status.VIOLATED, Status.UNKNOWN):
            if status in statuses:
                return status
        return Status.HOLDS

    def exit_code(self) -> int:
        """0 all hold, 1 some violated, 2 error, 3 inconclusive only."""
        return {
            Status.HOLDS: 0,
            Status.VIOLATED: 1,
            Status.ERROR: 2,
            Status.UNKNOWN: 3,
        }[self.overall()]
