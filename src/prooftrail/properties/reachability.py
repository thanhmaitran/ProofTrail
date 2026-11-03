from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from prooftrail.model.expressions import Expression
from prooftrail.properties.base import Property


@dataclass(frozen=True)
class ReachabilityProperty(Property):
    """``EF target``: some reachable state satisfies ``target``.

    ``HOLDS`` comes with a shortest *witness* path. ``VIOLATED`` means the whole
    reachable state space was explored and no state satisfies the target, so
    there is no trace to show - the evidence is the exhaustive exploration.
    """

    target: Expression = None  # type: ignore[assignment]

    kind: ClassVar[str] = "reachability"

    def reached_by(self, state: object) -> bool:
        return bool(self.target.evaluate(state))  # type: ignore[arg-type]
