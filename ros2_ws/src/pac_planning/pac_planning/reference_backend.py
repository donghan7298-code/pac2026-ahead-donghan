"""Explicit offline scaffold for teammates' stages 5-1/5-2.

Single-support/full-footprint geometry inherited conceptually from the old
pacdata.packing baseline. This is NOT EMS search, LBCP, IK, or physical simulation.
"""

import math
from pac_common import (
    ConstraintEvidence,
    PlacementCandidate,
    Pose3D,
    RejectCode as R,
    ValidationResult,
)
from .config import PlannerConfig
from .geometry import EPS, G, bounds, cog, dimensions, overlap


class ReferenceBackend:
    def __init__(self, context, config=None):
        self.context = context
        self.config = config or PlannerConfig()

    def generate_candidates(self, box, state):
        p = state.pallet.size
        raw = set()
        for yaw in box.allowed_yaws_rad:
            dx, dy, _ = dimensions(box.size, yaw)
            anchors = [
                (0, 0, 0),
                (p.x - dx, 0, 0),
                (0, p.y - dy, 0),
                (p.x - dx, p.y - dy, 0),
                ((p.x - dx) / 2, (p.y - dy) / 2, 0),
            ]
            for old in state.pallet.boxes:
                lo, hi = bounds(old)
                anchors.extend(
                    [
                        (hi[0], lo[1], 0),
                        (lo[0], hi[1], 0),
                        (lo[0] - dx, lo[1], 0),
                        (lo[0], lo[1] - dy, 0),
                    ]
                )
                for x, y in (
                    (lo[0], lo[1]),
                    (hi[0] - dx, lo[1]),
                    (lo[0], hi[1] - dy),
                    (hi[0] - dx, hi[1] - dy),
                    ((lo[0] + hi[0] - dx) / 2, (lo[1] + hi[1] - dy) / 2),
                ):
                    anchors.append((x, y, hi[2]))
            for xyz in anchors:
                raw.add(tuple(round(v, 10) for v in xyz) + (yaw,))
        return [
            PlacementCandidate(
                f"S{state.state_version:04d}-{box.box_id}-C{i:04d}",
                box.box_id,
                Pose3D("pallet", x, y, z, yaw=yaw),
                state.state_version,
            )
            for i, (x, y, z, yaw) in enumerate(
                sorted(raw, key=lambda a: (a[2], a[1], a[0], a[3]))
            )
        ]

    def _capacity(self, box):
        if box.box_id in self.context.capacity_overrides_n:
            return self.context.capacity_overrides_n[box.box_id]
        return self.context.catalog[box.sku_id].top_load_capacity_n

    def _support_graph(self, boxes):
        parents = {}
        for box in boxes:
            lo, hi = bounds(box)
            if abs(lo[2]) <= EPS:
                parents[box.box_id] = None
                continue
            supporters = []
            for old in boxes:
                if old.box_id == box.box_id:
                    continue
                blo, bhi = bounds(old)
                if abs(bhi[2] - lo[2]) <= EPS and all(
                    blo[i] <= lo[i] + EPS and hi[i] <= bhi[i] + EPS
                    for i in (0, 1)
                ):
                    supporters.append(old.box_id)
            if len(supporters) != 1:
                raise ValueError(
                    "Reference requires exactly one full supporter"
                )
            parents[box.box_id] = supporters[0]
        return parents

    def validate_constraints(self, box, candidate, state):
        if candidate.base_state_version != state.state_version:
            return ValidationResult(False, (R.STALE_PLAN,))
        if candidate.box_id != box.box_id or any(
            b.box_id == box.box_id for b in state.pallet.boxes
        ):
            return ValidationResult(False, (R.INVALID_STATE,))
        if box.box_id in self.context.uncertain_box_ids or any(
            b.box_id in self.context.uncertain_box_ids
            for b in state.pallet.boxes
        ):
            return ValidationResult(
                False,
                (R.SENSOR_UNCERTAIN,),
                {"reason": "Needs robust external validator"},
            )
        pose = candidate.target_pose
        if not any(
            math.isclose(
                math.remainder(pose.yaw - yaw, 2 * math.pi), 0, abs_tol=EPS
            )
            for yaw in box.allowed_yaws_rad
        ):
            return ValidationResult(
                False,
                (R.INVALID_STATE,),
                {"reason": "Orientation not allowed"},
            )
        try:
            lo, hi = bounds(box, pose)
            bounded = [(b, *bounds(b)) for b in state.pallet.boxes]
        except ValueError as error:
            return ValidationResult(
                False, (R.INVALID_STATE,), {"reason": str(error)}
            )
        p = state.pallet.size
        codes = []
        if (
            lo[0] < -EPS
            or lo[1] < -EPS
            or hi[0] > p.x + EPS
            or hi[1] > p.y + EPS
        ):
            codes.append(R.OUT_OF_BOUND)
        if lo[2] < -EPS or hi[2] > p.z + EPS:
            codes.append(R.HEIGHT_LIMIT)
        if any(overlap(lo, hi, blo, bhi) for _, blo, bhi in bounded):
            codes.append(R.BOX_COLLISION)
        # Vertical approach COLUMN is checked conservatively for packing only.
        if any(
            overlap(lo, hi, blo, bhi, (0, 1)) and bhi[2] > lo[2] + EPS
            for _, blo, bhi in bounded
        ):
            codes.append(R.APPROACH_FAIL)
        mass = sum(b.weight_kg for b in state.pallet.boxes) + box.weight_kg
        if mass > self.context.pallet_max_weight_kg + EPS:
            codes.append(R.LOAD_VIOLATION)
        if codes:
            return ValidationResult(False, tuple(dict.fromkeys(codes)))
        from pac_common import PlacedBox

        proposed = PlacedBox(
            box.box_id, box.sku_id, box.size, box.weight_kg, pose
        )
        boxes = state.pallet.boxes + (proposed,)
        try:
            parents = self._support_graph(boxes)
            by_id = {b.box_id: b for b in boxes}
            descendants = {b.box_id: [] for b in boxes}
            for b in boxes:
                parent = parents[b.box_id]
                seen = set()
                if (
                    parent is not None
                    and b.weight_kg > by_id[parent].weight_kg + EPS
                ):
                    codes.append(R.LOAD_VIOLATION)
                while parent is not None:
                    if parent in seen:
                        raise ValueError("Support cycle")
                    seen.add(parent)
                    descendants[parent].append(b)
                    parent = parents[parent]
            load_margin = 1.0
            max_load = 0.0
            cog_margin = 1.0
            for b in boxes:
                capacity = self._capacity(b)
                load = sum(x.weight_kg for x in descendants[b.box_id]) * G
                if load > capacity + EPS:
                    codes.append(R.LOAD_VIOLATION)
                ratio = (
                    load / capacity
                    if capacity > 0
                    else (0 if load == 0 else 1e6)
                )
                max_load = max(max_load, ratio)
                load_margin = min(load_margin, max(0.0, 1 - ratio))
                # Resultant CoG of every subtree inside its FULL support rectangle.
                cx, cy, _ = cog((b, *descendants[b.box_id]), p)
                blo, bhi = bounds(b)
                margin = min(
                    (cx - blo[0]) / ((bhi[0] - blo[0]) / 2),
                    (bhi[0] - cx) / ((bhi[0] - blo[0]) / 2),
                    (cy - blo[1]) / ((bhi[1] - blo[1]) / 2),
                    (bhi[1] - cy) / ((bhi[1] - blo[1]) / 2),
                )
                cog_margin = min(cog_margin, max(0.0, margin))
            cx, cy, _ = cog(boxes, p)
            cog_margin = min(
                cog_margin,
                2 * cx / p.x,
                2 * (1 - cx / p.x),
                2 * cy / p.y,
                2 * (1 - cy / p.y),
            )
        except KeyError as error:
            return ValidationResult(
                False,
                (R.INVALID_STATE,),
                {"reason": "Missing SKU capacity: " + str(error)},
            )
        except ValueError as error:
            return ValidationResult(
                False, (R.LOW_SUPPORT,), {"reason": str(error)}
            )
        if cog_margin + EPS < self.config.min_cog_margin:
            codes.append(R.COG_VIOLATION)
        if codes:
            return ValidationResult(False, tuple(dict.fromkeys(codes)))
        dependency = 0
        parent = parents[box.box_id]
        while parent is not None:
            dependency += 1
            parent = parents[parent]
        evidence = ConstraintEvidence(
            1.0,
            min(1.0, max(0.0, cog_margin)),
            load_margin,
            max(0.0, 1 - mass / self.context.pallet_max_weight_kg),
            max_load,
            (
                0.5
                if abs(lo[2]) <= EPS
                else min(hi[0] - lo[0], hi[1] - lo[1])
                / (2 * max(hi[0] - lo[0], hi[1] - lo[1]))
            ),
            dependency,
            "REFERENCE_FULL_SINGLE_SUPPORT",
        )
        return ValidationResult(True, details={"evidence": evidence})
