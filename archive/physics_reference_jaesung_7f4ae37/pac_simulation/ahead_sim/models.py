from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

Vec3 = Tuple[float, float, float]


@dataclass(frozen=True)
class PalletConfig:
    length_m: float = 1.10
    width_m: float = 1.10
    deck_height_m: float = 0.15
    max_height_m: float = 1.60

    collision_model: str = "slatted"
    top_board_count: int = 5
    top_board_width_ratio: float = 0.13
    top_board_thickness_ratio: float = 0.22
    support_block_count_x: int = 3
    support_block_count_y: int = 3
    support_block_length_ratio: float = 0.14
    support_block_width_ratio: float = 0.15
    support_block_height_ratio: float = 0.50
    bottom_board_count: int = 3
    bottom_board_width_ratio: float = 0.14
    bottom_board_thickness_ratio: float = 0.16

    def as_dict(self) -> Dict[str, Any]:
        return {
            "length_m": self.length_m,
            "width_m": self.width_m,
            "deck_height_m": self.deck_height_m,
            "max_height_m": self.max_height_m,
            "collision_model": self.collision_model,
            "top_board_count": self.top_board_count,
            "top_board_width_ratio": self.top_board_width_ratio,
            "top_board_thickness_ratio": self.top_board_thickness_ratio,
        }


@dataclass(frozen=True)
class PhysicsConfig:
    gravity_m_s2: float = 9.80665
    physics_hz: int = 240
    server_loop_hz: int = 60
    viewer_hz: int = 30
    solver_iterations: int = 100
    rolling_friction: float = 0.001
    spinning_friction: float = 0.01
    restitution: float = 0.02
    spawn_clearance_m: float = 0.002

    # Desired combined contact coefficients.
    target_box_box_friction: float = 0.64
    target_box_pallet_friction: float = 0.72

    def bullet_body_frictions(self) -> Tuple[float, float]:
        """Return (box_body_friction, pallet_body_friction).

        Bullet's default material combiner multiplies body friction values.
        This conversion makes the config express the effective contact
        coefficient that we actually want to reason about.
        """
        bb = max(0.0, float(self.target_box_box_friction))
        bp = max(0.0, float(self.target_box_pallet_friction))

        box = bb ** 0.5
        if box <= 1e-12:
            # If box-box target is zero, use zero for both rather than divide.
            return 0.0, 0.0
        pallet = bp / box
        return box, pallet


@dataclass(frozen=True)
class SimulatorConfig:
    pallet: PalletConfig = field(default_factory=PalletConfig)
    physics: PhysicsConfig = field(default_factory=PhysicsConfig)
    load_grid_size: int = 20
    contact_display_min_force_n: float = 0.05
    contact_force_average_window_s: float = 0.50


@dataclass(frozen=True)
class BoxSpec:
    box_id: str
    size_m: Vec3
    mass_kg: float
    target_position_m: Vec3
    yaw_rad: float = 0.0
    color: str = "#c98b52"
    source: str = "ahead"

    # Optional carton strength constraint.
    # None means that this property is unknown/not evaluated.
    max_top_load_n: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.box_id,
            "size_m": list(self.size_m),
            "mass_kg": self.mass_kg,
            "target_position_m": list(self.target_position_m),
            "yaw_rad": self.yaw_rad,
            "color": self.color,
            "source": self.source,
            "max_top_load_n": self.max_top_load_n,
        }

    @staticmethod
    def from_mapping(data: Dict[str, Any]) -> "BoxSpec":
        box_id = str(data.get("id") or data.get("box_id"))
        if not box_id or box_id == "None":
            raise ValueError("box id is required")

        size = data.get("size_m") or data.get("size")
        target = data.get("target_position_m") or data.get("position")
        if size is None or len(size) != 3:
            raise ValueError("size_m must contain [x, y, z]")
        if target is None or len(target) != 3:
            raise ValueError("target_position_m must contain [x, y, z]")

        mass = float(data.get("mass_kg", 0.0))
        if mass <= 0:
            raise ValueError("mass_kg must be > 0")

        size_t = tuple(float(v) for v in size)
        if any(v <= 0 for v in size_t):
            raise ValueError("all size_m values must be > 0")

        max_top_load_n = data.get("max_top_load_n")
        if max_top_load_n is None and data.get("max_supported_load_kg") is not None:
            max_top_load_n = float(data["max_supported_load_kg"]) * 9.80665
        if max_top_load_n is not None:
            max_top_load_n = float(max_top_load_n)
            if not math.isfinite(max_top_load_n) or max_top_load_n < 0:
                raise ValueError("max_top_load_n must be finite and >= 0 when provided")

        return BoxSpec(
            box_id=box_id,
            size_m=size_t,  # type: ignore[arg-type]
            mass_kg=mass,
            target_position_m=tuple(float(v) for v in target),  # type: ignore[arg-type]
            yaw_rad=float(data.get("yaw_rad", data.get("yaw", 0.0))),
            color=str(data.get("color", "#c98b52")),
            source=str(data.get("source", "ahead")),
            max_top_load_n=max_top_load_n,
        )
