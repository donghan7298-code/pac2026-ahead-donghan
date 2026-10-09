from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Tuple

import yaml

from .models import PalletConfig, PhysicsConfig, SimulatorConfig


def load_config(path: Path) -> Tuple[SimulatorConfig, Dict[str, Any]]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    p = raw.get("pallet", {})
    ph = raw.get("physics", {})
    m = raw.get("metrics", {})

    pallet = PalletConfig(
        length_m=float(p.get("length_m", 1.10)),
        width_m=float(p.get("width_m", 1.10)),
        deck_height_m=float(p.get("deck_height_m", 0.15)),
        max_height_m=float(p.get("max_height_m", 1.60)),
        collision_model=str(p.get("collision_model", "slatted")),
        top_board_count=int(p.get("top_board_count", 5)),
        top_board_width_ratio=float(p.get("top_board_width_ratio", 0.13)),
        top_board_thickness_ratio=float(
            p.get("top_board_thickness_ratio", 0.22)
        ),
        support_block_count_x=int(p.get("support_block_count_x", 3)),
        support_block_count_y=int(p.get("support_block_count_y", 3)),
        support_block_length_ratio=float(
            p.get("support_block_length_ratio", 0.14)
        ),
        support_block_width_ratio=float(
            p.get("support_block_width_ratio", 0.15)
        ),
        support_block_height_ratio=float(
            p.get("support_block_height_ratio", 0.50)
        ),
        bottom_board_count=int(p.get("bottom_board_count", 3)),
        bottom_board_width_ratio=float(
            p.get("bottom_board_width_ratio", 0.14)
        ),
        bottom_board_thickness_ratio=float(
            p.get("bottom_board_thickness_ratio", 0.16)
        ),
    )

    # Backward compatibility:
    # if old raw body friction values exist, infer their combined targets.
    old_box = ph.get("box_lateral_friction")
    old_pallet = ph.get("pallet_lateral_friction")
    target_bb = ph.get("target_box_box_friction")
    target_bp = ph.get("target_box_pallet_friction")

    if target_bb is None and old_box is not None:
        target_bb = float(old_box) * float(old_box)
    if target_bp is None and old_box is not None and old_pallet is not None:
        target_bp = float(old_box) * float(old_pallet)

    physics = PhysicsConfig(
        gravity_m_s2=float(ph.get("gravity_m_s2", 9.80665)),
        physics_hz=int(ph.get("physics_hz", 240)),
        server_loop_hz=int(ph.get("server_loop_hz", 60)),
        viewer_hz=int(ph.get("viewer_hz", 30)),
        solver_iterations=int(ph.get("solver_iterations", 100)),
        rolling_friction=float(ph.get("rolling_friction", 0.001)),
        spinning_friction=float(ph.get("spinning_friction", 0.01)),
        restitution=float(ph.get("restitution", 0.02)),
        spawn_clearance_m=float(ph.get("spawn_clearance_m", 0.002)),
        target_box_box_friction=float(
            0.64 if target_bb is None else target_bb
        ),
        target_box_pallet_friction=float(
            0.72 if target_bp is None else target_bp
        ),
    )

    cfg = SimulatorConfig(
        pallet=pallet,
        physics=physics,
        load_grid_size=int(m.get("load_grid_size", 20)),
        contact_display_min_force_n=float(
            m.get("contact_display_min_force_n", 0.05)
        ),
        contact_force_average_window_s=float(
            m.get("contact_force_average_window_s", 0.50)
        ),
    )
    return cfg, raw
