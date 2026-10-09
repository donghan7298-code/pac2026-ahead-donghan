from __future__ import annotations

import math

from typing import Any, Dict, Mapping, Sequence


def evaluate_box_compression(
    boxes: Sequence[Dict[str, Any]],
    averaged_per_box: Mapping[str, Dict[str, float]],
) -> Dict[str, Any]:
    """Evaluate optional box top-load constraints.

    This does not deform the rigid body.  It is an analytic failure constraint
    layered on top of the actual Bullet contact forces.

    A box with no max_top_load_n is reported as UNKNOWN, not guessed.
    """

    per_box: Dict[str, Dict[str, Any]] = {}
    overloaded = []

    for box in boxes:
        box_id = str(box["id"])
        avg = averaged_per_box.get(box_id, {})
        load_n = float(avg.get("load_from_above_n_avg", 0.0))
        limit = box.get("max_top_load_n")

        if limit is None:
            per_box[box_id] = {
                "status": "UNKNOWN",
                "load_from_above_n_avg": load_n,
                "max_top_load_n": None,
                "utilization": None,
            }
            continue

        limit_n = float(limit)
        if not math.isfinite(limit_n) or limit_n < 0:
            raise ValueError("max_top_load_n must be finite and >= 0")
        # 0 N means non-stackable; no load is valid, positive load is not.
        # None avoids an Infinity value in JSON and in the browser renderer.
        utilization = load_n / limit_n if limit_n > 0 else None
        overloaded_now = load_n > (limit_n if limit_n > 0 else 1e-6)
        status = "OVERLOAD" if overloaded_now else "OK"

        if status == "OVERLOAD":
            overloaded.append(box_id)

        per_box[box_id] = {
            "status": status,
            "load_from_above_n_avg": load_n,
            "max_top_load_n": limit_n,
            "utilization": utilization,
        }

    return {
        "per_box": per_box,
        "overloaded_box_ids": overloaded,
        "overloaded_box_count": len(overloaded),
    }
