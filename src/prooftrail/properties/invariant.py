from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

from prooftrail.model.expressions import Expression
from prooftrail.properties.base import Property


@dataclass(frozen=True)
class InvariantProperty(Property):
    """``AG expression``: the expression holds in every reachable state.

    A violation is a finite path from an initial state to a state where the
    expression is false. Breadth-first search finds a shortest such path.
    """

    expression: Expression = None  # type: ignore[assignment]

    kind: ClassVar[str] = "invariant"

    def holds_in(self, state: object) -> bool:
        return bool(self.expression.evaluate(state))  # type: ignore[arg-type]
