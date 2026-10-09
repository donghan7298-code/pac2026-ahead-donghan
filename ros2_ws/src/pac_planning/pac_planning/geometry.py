"""Axis-aligned geometry for features and the OFFLINE reference backend."""

from dataclasses import replace
import math
from pac_common import (
    BoxStatus,
    InventoryState,
    PlacedBox,
    Pose3D,
    SimulationSnapshot,
)

EPS = 1e-8
G = 9.80665


def dimensions(size, yaw):
    quarter = round(yaw / (math.pi / 2))
    if not math.isclose(yaw, quarter * math.pi / 2, abs_tol=1e-8):
        raise ValueError("Reference geometry supports only axis-aligned yaws")
    return (
        (size.y, size.x, size.z) if quarter % 2 else (size.x, size.y, size.z)
    )


def bounds(box, pose=None):
    p = pose or box.pose
    if abs(p.roll) > EPS or abs(p.pitch) > EPS:
        raise ValueError("Only upright boxes are supported")
    d = dimensions(box.size, p.yaw)
    lo = (p.x, p.y, p.z)
    return lo, tuple(a + b for a, b in zip(lo, d))


def volume(size):
    return size.x * size.y * size.z


def overlap(lo, hi, other_lo, other_hi, axes=(0, 1, 2)):
    return all(
        min(hi[i], other_hi[i]) - max(lo[i], other_lo[i]) > EPS for i in axes
    )


def center_pose(box, candidate):
    """Explicit lower-AABB-corner -> box-center adapter for the robot team."""
    p = candidate.target_pose
    d = dimensions(box.size, p.yaw)
    return replace(p, x=p.x + d[0] / 2, y=p.y + d[1] / 2, z=p.z + d[2] / 2)


def cog(boxes, pallet_size):
    mass = sum(b.weight_kg for b in boxes)
    if mass <= EPS:
        return (pallet_size.x / 2, pallet_size.y / 2, 0.0)
    result = [0.0] * 3
    for b in boxes:
        lo, hi = bounds(b)
        for axis in range(3):
            result[axis] += b.weight_kg * (lo[axis] + hi[axis]) / 2 / mass
    return tuple(result)


def simulate_placement(state, box, candidate, *, consume_unseen=False):
    """Internal look-ahead copy. Never a State Manager commit or execution event."""
    if (
        candidate.base_state_version != state.state_version
        or candidate.box_id != box.box_id
    ):
        raise ValueError("Stale or mismatched simulation candidate")
    if any(b.box_id == box.box_id for b in state.pallet.boxes):
        raise ValueError("Box already placed")
    new_box = PlacedBox(
        box.box_id, box.sku_id, box.size, box.weight_kg, candidate.target_pose
    )
    tracked = dict(state.inventory.tracked_boxes)
    tracked[box.box_id] = replace(
        box, pose=candidate.target_pose, status=BoxStatus.PLACED
    )
    remaining = dict(state.inventory.remaining_by_sku)
    if consume_unseen:
        if (
            box.box_id in state.inventory.tracked_boxes
            or remaining.get(box.sku_id, 0) <= 0
        ):
            raise ValueError("Unseen inventory double consumption")
        remaining[box.sku_id] -= 1
    simulated = replace(
        state,
        pallet=replace(state.pallet, boxes=state.pallet.boxes + (new_box,)),
        inventory=InventoryState(tracked, remaining),
    )
    # Keep actual version as lineage; synthetic decisions NEVER increment it.
    return SimulationSnapshot(simulated)
