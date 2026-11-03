from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar


@dataclass(frozen=True)
class Property:
    """Common base of all properties.

    ``kind`` is the value of the ``type`` field in a specification file.
    """

    id: str
    description: str = ""

    kind: ClassVar[str] = "property"

    @property
    def needs_graph(self) -> bool:
        """True if checking requires the complete explored graph (temporal)."""
        return False
