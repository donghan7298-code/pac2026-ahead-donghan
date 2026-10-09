"""AHEAD low-level placement planner (stages 5-3 through 5-6)."""

from .config import PlannerConfig, load_config
from .planner import PlacementPlanner
from .team_bridge import TeamRuntimeRanker

__version__ = "0.1.0"
