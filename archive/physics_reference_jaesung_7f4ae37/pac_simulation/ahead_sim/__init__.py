"""Live rigid-body pallet simulator for PAC2026 AHEAD.

The physics outcome comes from PyBullet.  AHEAD / analytic physics can use this
module as an execution/validation world after it selects a placement.
"""

from .models import BoxSpec, PalletConfig, PhysicsConfig, SimulatorConfig
from .simulator import AheadLiveSimulator

__all__ = [
    "BoxSpec",
    "PalletConfig",
    "PhysicsConfig",
    "SimulatorConfig",
    "AheadLiveSimulator",
]
