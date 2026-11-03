"""Property definitions: invariants, reachability, deadlock freedom, CTL."""

from __future__ import annotations

from prooftrail.properties.base import Property
from prooftrail.properties.ctl import CTLProperty, ResponseProperty
from prooftrail.properties.deadlock import DeadlockFreedom
from prooftrail.properties.invariant import InvariantProperty
from prooftrail.properties.reachability import ReachabilityProperty

__all__ = [
    "CTLProperty",
    "DeadlockFreedom",
    "InvariantProperty",
    "Property",
    "ReachabilityProperty",
    "ResponseProperty",
]
