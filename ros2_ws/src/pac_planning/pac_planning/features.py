"""Stage 5-3: OPAL-inspired 15 fields plus normalized operational context.

EMS dimensions are only called EMS when supplied by the generator. Missing EMS
uses the footprint column with an explicit indicator. All safety evidence comes
from the hard validator, never from the learning model.
"""

from collections import deque
from dataclasses import replace
import math
import numpy as np
from pac_common import (
    BoxState,
    BoxStatus,
    ConstraintEvidence,
    FeatureVector,
    Pose3D,
)
from .geometry import EPS, bounds, cog, dimensions, simulate_placement, volume

OPAL_NAMES = (
    "x_m",
    "y_m",
    "z_m",
    "extent_x_m",
    "extent_y_m",
    "extent_z_m",
    "footprint_x_m",
    "footprint_y_m",
    "support_ratio",
    "support_centering",
    "top_height_ratio",
    "max_load_ratio",
    "fragility_feasible",
    "side_support",
    "placement_effort",
)
FEATURE_NAMES = OPAL_NAMES + (
    "x_norm",
    "y_norm",
    "z_norm",
    "extent_x_norm",
    "extent_y_norm",
    "extent_z_norm",
    "footprint_x_norm",
    "footprint_y_norm",
    "box_height_norm",
    "box_weight_norm",
    "remaining_probe_fit",
    "buffer_probe_fit",
    "buffer_occupancy",
    "dependency_norm",
    "access_clearance_proxy",
    "cog_shift_norm",
    "cog_height_shift_norm",
    "load_increment_norm",
    "compactness",
    "largest_free_patch",
    "fragmentation",
    "height_variation",
    "safety_saturated",
    "load_margin",
    "balance",
    "time_cost",
    "ems_known",
    "observed_preview_ratio",
    "remaining_volume_ratio",
    "sensor_uncertain",
)
FEATURE_SCHEMA = "ahead-features-v1-45"


def sku_box(spec, identity, stamp):
    return BoxState(
        identity,
        spec.sku_id,
        spec.size,
        spec.weight_kg,
        Pose3D("conveyor", 0.0, 0.0, 0.0),
        spec.allowed_yaws_rad,
        BoxStatus.MEASURED,
        1.0,
        stamp,
        "SIMULATED_CATALOG",
    )


def height_grid(state, side):
    """Conservative cell-intersection height grid; not a hard collision test."""
    p = state.pallet.size
    xlo = np.arange(side) * p.x / side
    ylo = np.arange(side) * p.y / side
    grid = np.zeros((side, side))
    for b in state.pallet.boxes:
        lo, hi = bounds(b)
        cover = (
            (xlo[None, :] < hi[0] - EPS)
            & (xlo[None, :] + p.x / side > lo[0] + EPS)
            & (ylo[:, None] < hi[1] - EPS)
            & (ylo[:, None] + p.y / side > lo[1] + EPS)
        )
        grid[cover] = np.maximum(grid[cover], hi[2])
    return grid


def patches(grid):
    free = grid <= EPS
    seen = set()
    sizes = []
    for y, x in zip(*np.where(free)):
        if (y, x) in seen:
            continue
        queue = deque([(y, x)])
        seen.add((y, x))
        n = 0
        while queue:
            yy, xx = queue.popleft()
            n += 1
            for ny, nx in (
                (yy - 1, xx),
                (yy + 1, xx),
                (yy, xx - 1),
                (yy, xx + 1),
            ):
                if (
                    0 <= ny < grid.shape[0]
                    and 0 <= nx < grid.shape[1]
                    and free[ny, nx]
                    and (ny, nx) not in seen
                ):
                    seen.add((ny, nx))
                    queue.append((ny, nx))
        sizes.append(n)
    total = sum(sizes)
    return (
        max(sizes, default=0) / max(1, total),
        (len(sizes) - 1) / max(1, total) if sizes else 0.0,
    )


def side_support(box, candidate, state):
    lo, hi = bounds(box, candidate.target_pose)
    limits = (state.pallet.size.x, state.pallet.size.y)
    supported = 0
    eligible = 0
    for axis in (0, 1):
        other = 1 - axis
        for positive in (False, True):
            plane = hi[axis] if positive else lo[axis]
            if abs(plane - (limits[axis] if positive else 0)) <= EPS:
                continue
            eligible += 1
            area = 0.0
            for b in state.pallet.boxes:
                blo, bhi = bounds(b)
                if abs(plane - (blo[axis] if positive else bhi[axis])) <= EPS:
                    area += max(
                        0.0,
                        min(hi[other], bhi[other])
                        - max(lo[other], blo[other]),
                    ) * max(0.0, min(hi[2], bhi[2]) - max(lo[2], blo[2]))
            supported += area + EPS >= 0.2 * (hi[other] - lo[other]) * (
                hi[2] - lo[2]
            )
    return supported / eligible if eligible else 1.0


def has_placement(box, state, generator, validator, limit):
    # The generator defines a finite search. Invalid early proposals must not
    # hide a later valid one. Keep limit for callers; existence needs only the
    # first valid proposal, so the rollout's valid-candidate limit is not a
    # raw-proposal cutoff here. False is NOT proof of geometric infeasibility.
    if type(limit) is not int or limit < 1:
        raise ValueError("Placement probe limit must be positive")
    for candidate in generator(box, state):
        if (
            candidate.box_id != box.box_id
            or candidate.base_state_version != state.state_version
        ):
            raise ValueError("Generator returned inconsistent candidate")
        if validator(box, candidate, state).success:
            return True
    return False


def compute_features(
    box, candidate, state, evidence, context, config, generator, validator
):
    if not isinstance(evidence, ConstraintEvidence):
        raise ValueError("Validator must provide typed ConstraintEvidence")
    post = simulate_placement(state, box, candidate).state
    p = state.pallet.size
    pose = candidate.target_pose
    dx, dy, dz = dimensions(box.size, pose.yaw)
    upper = context.ems_upper_by_candidate.get(candidate.candidate_id)
    known = upper is not None
    if upper is None:
        upper = Pose3D("pallet", pose.x + dx, pose.y + dy, p.z)
    extents = (upper.x - pose.x, upper.y - pose.y, upper.z - pose.z)
    if any(a + EPS < b for a, b in zip(extents, (dx, dy, dz))):
        raise ValueError("EMS upper bound does not contain candidate")
    fit = 0.0
    total = sum(state.inventory.remaining_by_sku.values())
    remaining_volume = 0.0
    for sku, n in sorted(state.inventory.remaining_by_sku.items()):
        spec = context.catalog[sku]
        remaining_volume += n * volume(spec.size)
        if n:
            probe = sku_box(spec, f"__probe__{sku}", state.stamp_sec)
            fit += n * has_placement(
                probe,
                post,
                generator,
                validator,
                config.rollout_candidate_limit,
            )
    remaining_fit = fit / total if total else 1.0
    buffered = [
        b
        for b in post.inventory.tracked_boxes.values()
        if b.status == BoxStatus.BUFFERED
    ]
    buffer_fit = (
        sum(
            has_placement(
                b, post, generator, validator, config.rollout_candidate_limit
            )
            for b in buffered
        )
        / len(buffered)
        if buffered
        else 1.0
    )
    grid = height_grid(post, config.grid_side)
    largest, fragmentation = patches(grid)
    variation = float(grid.std()) / p.z
    height = max(bounds(b)[1][2] for b in post.pallet.boxes)
    compactness = sum(volume(b.size) for b in post.pallet.boxes) / (
        p.x * p.y * height
    )
    before_cog = cog(state.pallet.boxes, p)
    after_cog = cog(post.pallet.boxes, p)
    shift = math.hypot(
        (after_cog[0] - before_cog[0]) / p.x,
        (after_cog[1] - before_cog[1]) / p.y,
    )
    balance = max(
        0.0,
        1
        - abs(2 * after_cog[0] / p.x - 1) / 2
        - abs(2 * after_cog[1] / p.y - 1) / 2,
    )
    safety = min(
        1.0,
        evidence.support_ratio / config.target_support,
        evidence.cog_margin_ratio / config.target_cog_margin,
        evidence.load_margin_ratio / config.target_load_margin,
        evidence.pallet_load_margin_ratio / config.target_load_margin,
    )
    seconds = context.robot_time_sec_by_candidate.get(candidate.candidate_id)
    time_source = (
        "EXTERNAL_ROBOT_ESTIMATE" if seconds is not None else "GEOMETRIC_PROXY"
    )
    if seconds is None:
        # Origin-to-center travel proxy: no uncalibrated conveyor/pallet transform.
        seconds = (
            config.base_handling_sec
            + math.sqrt(
                (pose.x + dx / 2) ** 2
                + (pose.y + dy / 2) ** 2
                + (pose.z + dz / 2) ** 2
            )
            / config.travel_speed_m_s
            + abs(math.remainder(pose.yaw, 2 * math.pi))
            * config.rotation_sec_per_rad
        )
    time_cost = min(1.0, seconds / config.time_reference_sec)
    top = (pose.z + dz) / p.z
    effort = (
        1
        + top
        + 0.25 * float(abs(math.remainder(pose.yaw, 2 * math.pi)) > EPS)
        + 0.5 * max(0.0, 0.75 - evidence.support_ratio)
    )
    space = (
        0.25 * compactness
        + 0.2 * largest
        + 0.25 * remaining_fit
        + 0.15 * (1 - variation)
        + 0.15 * (1 - fragmentation)
    )
    vals = (
        pose.x,
        pose.y,
        pose.z,
        *extents,
        dx,
        dy,
        evidence.support_ratio,
        evidence.support_centering,
        top,
        evidence.max_load_ratio,
        float(evidence.max_load_ratio <= 1),
        side_support(box, candidate, state),
        effort,
        pose.x / p.x,
        pose.y / p.y,
        pose.z / p.z,
        extents[0] / p.x,
        extents[1] / p.y,
        extents[2] / p.z,
        dx / p.x,
        dy / p.y,
        dz / p.z,
        box.weight_kg / context.pallet_max_weight_kg,
        remaining_fit,
        buffer_fit,
        len(buffered) / max(1, context.buffer_capacity),
        evidence.dependency_count / max(1, len(state.pallet.boxes)),
        1 - top,
        shift,
        (after_cog[2] - before_cog[2]) / p.z,
        box.weight_kg / context.pallet_max_weight_kg,
        compactness,
        largest,
        fragmentation,
        variation,
        safety,
        evidence.load_margin_ratio,
        balance,
        time_cost,
        float(known),
        len(context.observed_preview)
        / max(1, total + len(context.observed_preview)),
        remaining_volume / volume(p),
        float(box.box_id in context.uncertain_box_ids),
    )
    return FeatureVector(
        FEATURE_NAMES,
        tuple(float(v) for v in vals),
        {
            "safety": safety,
            "space": space,
            "time": time_cost,
            "estimated_time_sec": seconds,
            "balance": balance,
            "support_ratio": evidence.support_ratio,
            "load_margin": evidence.load_margin_ratio,
            "cog_margin": evidence.cog_margin_ratio,
            "remaining_probe_fit": remaining_fit,
            "buffer_probe_fit": buffer_fit,
            "compactness": compactness,
            "volume_utilization": sum(
                volume(b.size) for b in post.pallet.boxes
            )
            / volume(p),
        },
        "EMS_SUPPLIED" if known else "FOOTPRINT_COLUMN_PROXY",
        time_source,
    )
