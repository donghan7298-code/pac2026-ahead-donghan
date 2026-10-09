from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List, Sequence, Tuple

from .models import PalletConfig


def _norm3(v: Sequence[float]) -> float:
    return math.sqrt(float(v[0]) ** 2 + float(v[1]) ** 2 + float(v[2]) ** 2)


def combined_com(boxes: Sequence[Dict[str, Any]]) -> Tuple[float, float, float]:
    total = sum(float(b["mass_kg"]) for b in boxes)
    if total <= 0:
        return (0.0, 0.0, 0.0)
    xyz = []
    for axis in range(3):
        xyz.append(
            sum(
                float(b["mass_kg"]) * float(b["position_m"][axis])
                for b in boxes
            )
            / total
        )
    return (xyz[0], xyz[1], xyz[2])


def is_center_over_pallet(box: Dict[str, Any], pallet: PalletConfig) -> bool:
    x, y, z = (float(v) for v in box["position_m"])
    return (
        abs(x) <= pallet.length_m / 2.0
        and abs(y) <= pallet.width_m / 2.0
        and float(box["aabb_max_m"][2]) > -0.01
    )


def build_load_grid(
    contacts: Sequence[Dict[str, Any]],
    pallet: PalletConfig,
    grid_size: int,
) -> Dict[str, Any]:
    n = max(2, int(grid_size))
    grid = [[0.0 for _ in range(n)] for _ in range(n)]
    total_force = 0.0
    weighted_x = 0.0
    weighted_y = 0.0

    for c in contacts:
        force = max(0.0, float(c["normal_force_n"]))
        if force <= 0.0:
            continue
        x, y, _ = (float(v) for v in c["position_on_pallet_m"])

        u = (x + pallet.length_m / 2.0) / pallet.length_m
        v = (y + pallet.width_m / 2.0) / pallet.width_m
        ix = max(0, min(n - 1, int(u * n)))
        iy = max(0, min(n - 1, int(v * n)))

        grid[iy][ix] += force
        total_force += force
        weighted_x += x * force
        weighted_y += y * force

    cop = None
    if total_force > 1e-9:
        cop = [weighted_x / total_force, weighted_y / total_force, 0.002]

    return {
        "grid_size": n,
        "force_n": grid,
        "max_cell_force_n": max((max(row) for row in grid), default=0.0),
        "total_normal_force_n": total_force,
        "equivalent_supported_mass_kg": total_force / 9.80665,
        "center_of_pressure_m": cop,
    }


def compute_metrics(
    boxes: Sequence[Dict[str, Any]],
    pallet: PalletConfig,
    pallet_contacts: Sequence[Dict[str, Any]],
    grid_size: int,
) -> Dict[str, Any]:
    total_mass = sum(float(b["mass_kg"]) for b in boxes)
    on_pallet = [b for b in boxes if is_center_over_pallet(b, pallet)]
    on_pallet_mass = sum(float(b["mass_kg"]) for b in on_pallet)

    com = combined_com(boxes)
    on_pallet_com = combined_com(on_pallet)

    if on_pallet:
        current_height = max(
            0.0, max(float(b["aabb_max_m"][2]) for b in on_pallet)
        )
    else:
        current_height = 0.0

    box_volume = sum(
        float(b["size_m"][0])
        * float(b["size_m"][1])
        * float(b["size_m"][2])
        for b in on_pallet
    )
    allowed_volume = (
        pallet.length_m * pallet.width_m * max(pallet.max_height_m, 1e-9)
    )
    current_envelope_volume = (
        pallet.length_m * pallet.width_m * max(current_height, 1e-9)
    )

    moving_ids: List[str] = []
    max_speed = 0.0
    max_tilt_deg = 0.0
    outside_ids: List[str] = []

    for b in boxes:
        speed = _norm3(b["linear_velocity_m_s"])
        angular = _norm3(b["angular_velocity_rad_s"])
        max_speed = max(max_speed, speed)
        if speed > 0.01 or angular > 0.03:
            moving_ids.append(str(b["id"]))

        roll, pitch, _ = (float(v) for v in b["euler_rad"])
        max_tilt_deg = max(
            max_tilt_deg,
            abs(math.degrees(roll)),
            abs(math.degrees(pitch)),
        )

        if not is_center_over_pallet(b, pallet):
            outside_ids.append(str(b["id"]))

    load_map = build_load_grid(
        pallet_contacts,
        pallet,
        grid_size,
    )

    return {
        "box_count": len(boxes),
        "on_pallet_box_count": len(on_pallet),
        "total_mass_kg": total_mass,
        "on_pallet_mass_kg": on_pallet_mass,
        "combined_com_m": list(com),
        "on_pallet_com_m": list(on_pallet_com),
        "com_xy_offset_m": math.hypot(on_pallet_com[0], on_pallet_com[1])
        if on_pallet
        else 0.0,
        "current_height_m": current_height,
        "height_limit_m": pallet.max_height_m,
        "remaining_height_m": pallet.max_height_m - current_height,
        "allowed_volume_utilization": box_volume / allowed_volume,
        "current_stack_utilization": (
            box_volume / current_envelope_volume if on_pallet else 0.0
        ),
        "moving_box_ids": moving_ids,
        "moving_box_count": len(moving_ids),
        "max_linear_speed_m_s": max_speed,
        "max_tilt_deg": max_tilt_deg,
        "outside_pallet_box_ids": outside_ids,
        "outside_pallet_box_count": len(outside_ids),
        "load_map": load_map,
    }
