from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from prooftrail.model.expressions import Expression
from prooftrail.properties.base import Property


@dataclass(frozen=True)
class DeadlockFreedom(Property):
    """No reachable state is a deadlock (a state with no enabled transition).

    Intended terminal states can be excluded with ``unless``: a state with no
    enabled transition that satisfies ``unless`` is *not* counted as a deadlock.
    """

    unless: Expression | None = None

    kind: ClassVar[str] = "deadlock_free"

    def is_violation(self, state: object, has_successor: bool) -> bool:
        if has_successor:
            return False
        return self.unless is None or not self.unless.evaluate(state)  # type: ignore[arg-type]
