"""Explicit lower-corner -> box-centre boundaries; never a robot TCP command."""

from dataclasses import dataclass
import math

import yaml
from pac_common import Pose3D, Size3D
from pac_common.models import finite
from .geometry import center_pose, dimensions


@dataclass(frozen=True)
class PalletFrame:
    """Physical pallet dimensions and pose of its TOP CENTRE in the world."""

    physical_size: Size3D
    top_center: Pose3D

    def __post_init__(self):
        if abs(self.top_center.roll) > 1e-8 or abs(self.top_center.pitch) > 1e-8:
            raise ValueError("Only horizontal pallet surfaces are supported")

    @classmethod
    def from_workcell(cls, path):
        with open(path, encoding="utf-8") as stream:
            row = yaml.safe_load(stream)["layout"]["pallet"]
        size = Size3D(*row["size_m"])
        x, y, z = row["center_world_m"]
        # workcell.yaml's centre is the physical wood centre, not its top.
        return cls(size, Pose3D("world", x, y, z + size.z / 2))

    def check_size(self, pallet):
        if not (math.isclose(pallet.size.x, self.physical_size.x, abs_tol=1e-8)
                and math.isclose(pallet.size.y, self.physical_size.y, abs_tol=1e-8)):
            raise ValueError("Planner and simulator pallet footprints differ")

    def centre_in_world(self, box, candidate, state):
        self.check_size(state.pallet)
        check_candidate(box, candidate, state)
        p = center_pose(box, candidate)
        x = p.x - self.physical_size.x / 2
        y = p.y - self.physical_size.y / 2
        a = self.top_center.yaw
        return Pose3D(
            self.top_center.frame_id,
            self.top_center.x + math.cos(a) * x - math.sin(a) * y,
            self.top_center.y + math.sin(a) * x + math.cos(a) * y,
            self.top_center.z + p.z, yaw=a + p.yaw,
        )


def check_candidate(box, candidate, state):
    if candidate.box_id != box.box_id or candidate.base_state_version != state.state_version:
        raise ValueError("Stale or mismatched placement")
    if candidate.target_pose.frame_id != "pallet":
        raise ValueError("Expected pallet lower-corner frame")
    if state.inventory.tracked_boxes.get(box.box_id) != box:
        raise ValueError("Box differs from the authoritative snapshot")
    if any(b.box_id == box.box_id for b in state.pallet.boxes):
        raise ValueError("Box is already placed")
    pose = candidate.target_pose
    if abs(pose.roll) > 1e-8 or abs(pose.pitch) > 1e-8:
        raise ValueError("Only upright boxes are supported")
    dims = dimensions(box.size, pose.yaw)
    for start, size, limit in zip((pose.x, pose.y, pose.z), dims,
                                 (state.pallet.size.x, state.pallet.size.y, state.pallet.size.z)):
        if start < -1e-8 or start + size > limit + 1e-8:
            raise ValueError("Target exceeds planner cargo envelope")


def bullet_payload(box, candidate, state, context, *, simulator_size_xy):
    """Payload for the standalone Bullet simulator, not Gazebo nor MoveIt.

The caller must hard-validate the candidate and check settled physics later.
0 N is preserved. Upstream simulator needs patches/ahead_zero_top_load.patch
to accept that value; never disguise non-stackable as unknown (None).
"""
    x, y = simulator_size_xy
    finite(x, "simulator pallet x", 0)
    finite(y, "simulator pallet y", 0)
    if not (math.isclose(x, state.pallet.size.x, abs_tol=1e-8)
            and math.isclose(y, state.pallet.size.y, abs_tol=1e-8)):
        raise ValueError("Planner and simulator pallet footprints differ")
    check_candidate(box, candidate, state)
    p = center_pose(box, candidate)
    capacity = context.capacity_overrides_n.get(
        box.box_id, context.catalog[box.sku_id].top_load_capacity_n
    )
    finite(capacity, "top load", 0)
    return {
        "id": box.box_id, "size_m": [box.size.x, box.size.y, box.size.z],
        "mass_kg": box.weight_kg,
        "target_position_m": [p.x - x / 2, p.y - y / 2, p.z],
        "yaw_rad": p.yaw, "max_top_load_n": capacity,
        "source": "donghan_planner_physics_check",
    }
