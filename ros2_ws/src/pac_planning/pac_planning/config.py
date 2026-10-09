"""Checked YAML configuration, independent of ROS."""

from dataclasses import dataclass, field
from pathlib import Path
import math
import yaml
from pac_common import FrozenDict


@dataclass(frozen=True)
class PlannerConfig:
    top_k: int = 4
    scenario_count: int = 7
    horizon: int = 3
    rollout_candidate_limit: int = 16
    timeout_sec: float = 1.0
    cvar_alpha: float = 0.2
    grid_side: int = 12
    target_support: float = 0.95
    target_cog_margin: float = 0.2
    target_load_margin: float = 0.2
    min_cog_margin: float = 0.02
    time_reference_sec: float = 20.0
    base_handling_sec: float = 2.0
    travel_speed_m_s: float = 0.5
    rotation_sec_per_rad: float = 0.5
    weights: dict[str, float] = field(
        default_factory=lambda: {
            "safety": 0.15,
            "space": 0.3,
            "future": 0.4,
            "risk": 0.1,
            "time": 0.05,
        }
    )

    def __post_init__(self):
        for name in (
            "top_k",
            "scenario_count",
            "horizon",
            "rollout_candidate_limit",
            "grid_side",
        ):
            v = getattr(self, name)
            if type(v) is not int or v < 1:
                raise ValueError(f"{name} must be a positive integer")
        for name in ("timeout_sec", "time_reference_sec", "travel_speed_m_s"):
            v = getattr(self, name)
            if isinstance(v, bool) or not math.isfinite(v) or v <= 0:
                raise ValueError(f"Invalid {name}")
        for name in (
            "cvar_alpha",
            "target_support",
            "target_cog_margin",
            "target_load_margin",
            "min_cog_margin",
        ):
            v = getattr(self, name)
            if not math.isfinite(v) or not 0 < v <= 1:
                raise ValueError(f"Invalid {name}")
        for name in ("base_handling_sec", "rotation_sec_per_rad"):
            v = getattr(self, name)
            if not math.isfinite(v) or v < 0:
                raise ValueError(f"Invalid {name}")
        if self.min_cog_margin > self.target_cog_margin:
            raise ValueError("Safety target is below the hard minimum")
        if set(self.weights) != {"safety", "space", "future", "risk", "time"}:
            raise ValueError("Unexpected score weights")
        if any(not math.isfinite(v) or v < 0 for v in self.weights.values()):
            raise ValueError("Invalid score weight")
        total = sum(self.weights.values())
        if total <= 0:
            raise ValueError("Zero weight sum")
        object.__setattr__(
            self,
            "weights",
            FrozenDict({k: v / total for k, v in self.weights.items()}),
        )


def load_config(path):
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Unsupported config schema")
    return PlannerConfig(**data["planning"])
