from __future__ import annotations

from dataclasses import dataclass

from prooftrail.model.expressions import Env, Expression
from prooftrail.model.state import Scalar


@dataclass(frozen=True)
class Transition:
    """A guarded, simultaneous-update action.

    The guard is a Boolean expression over the current state. When it is true
    the action is *enabled*; taking it evaluates every right-hand side in the
    **current** state and assigns all results at once. Variables that are not
    updated keep their value.
    """

    id: str
    guard: Expression
    updates: tuple[tuple[str, Expression], ...] = ()
    description: str = ""

    def enabled(self, env: Env) -> bool:
        return bool(self.guard.evaluate(env))

    def evaluate_updates(self, env: Env) -> dict[str, Scalar]:
        """Evaluate all right-hand sides against ``env`` (the old state)."""
        return {name: expr.evaluate(env) for name, expr in self.updates}
