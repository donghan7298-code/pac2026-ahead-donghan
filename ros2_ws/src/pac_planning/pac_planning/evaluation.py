"""Paired runtime metrics and inventory-clustered uncertainty estimates.

Repeated seeds of the same inventory are not treated as independent data.
Reports can reject deployment; they never change the ROS default automatically.
"""

from collections import defaultdict

import numpy as np
from pac_common import FeatureVector

from .features import FEATURE_NAMES
from .model import OUTPUT_NAMES
from .planner import ZERO_FUTURE
from .scoring import priority, score_terms


def packed_query_report(data, config, model=None):
    hits, regret, square, candidates = [], [], np.zeros(4), 0
    for gi in range(len(data.offsets) - 1):
        a, b = data.offsets[gi:gi + 2]
        vectors = []
        for values in data.features[a:b]:
            named = dict(zip(FEATURE_NAMES, map(float, values)))
            space = (.25 * named["compactness"] + .2 * named["largest_free_patch"]
                     + .25 * named["remaining_probe_fit"] + .15 * (1 - named["height_variation"])
                     + .15 * (1 - named["fragmentation"]))
            vectors.append(FeatureVector(FEATURE_NAMES, tuple(map(float, values)),
                                         dict(safety=named["safety_saturated"], space=space,
                                              time=named["time_cost"]), "EMS_SUPPLIED", "OFFLINE"))
        prediction = model.predict(vectors) if model else [
            (sum(score_terms(v, ZERO_FUTURE, config).values()), ZERO_FUTURE) for v in vectors]
        order = sorted(range(len(vectors)), key=lambda i: priority(vectors[i], prediction[i][0]), reverse=True)
        truth = data.relevance[a:b]
        best = float(np.max(truth))
        hits.append(any(truth[i] >= best - 1e-7 for i in order[:config.top_k]))
        regret.append(best - float(truth[order[0]]))
        y = np.asarray([[getattr(value, n) for n in OUTPUT_NAMES] for _, value in prediction])
        square += np.sum((y - data.future[a:b]) ** 2, axis=0)
        candidates += b - a
    return dict(queries=len(hits), candidates=int(candidates),
                teacher_best_top_k_recall=float(np.mean(hits)),
                teacher_top1_relevance_regret=float(np.mean(regret)),
                future_output_mse=dict(zip(OUTPUT_NAMES, map(float, square / candidates))))


def paired_comparison(rows, mode, baseline="dblf", *, bootstrap=2000, seed=7):
    """Compare each exact (scenario, seed, variant) pair, bootstrap inventories."""
    metrics = ("pallet_equivalent", "pallets", "fill_true_mean", "time_s", "placed",
               "placed_volume_m3", "L4", "final_audit_issues", "executability_rate")
    left = {(r["scenario_id"], r["seed"], r["variant"]): r for r in rows if r["mode"] == baseline}
    right = {(r["scenario_id"], r["seed"], r["variant"]): r for r in rows if r["mode"] == mode}
    if (len(left) != sum(r["mode"] == baseline for r in rows)
            or len(right) != sum(r["mode"] == mode for r in rows)):
        raise ValueError("Duplicate episode key in paired comparison")
    if not left or left.keys() != right.keys():
        raise ValueError("Every mode needs exactly the same episode keys")
    clusters = defaultdict(list)
    for key in sorted(left):
        if left[key]["stream_sha256"] != right[key]["stream_sha256"]:
            raise ValueError("Paired runtime streams differ")
        clusters[left[key]["inventory_group"]].append(key)
    rng = np.random.default_rng(seed)
    result = {"baseline": baseline, "mode": mode, "episodes": len(left),
              "independent_inventory_groups": len(clusters),
              "uncertainty": "95% percentile bootstrap of inventory-cluster mean deltas",
              "delta_direction": "mode minus baseline", "metrics": {}}
    for metric in metrics:
        deltas = np.asarray([np.mean([right[k][metric] - left[k][metric] for k in keys])
                             for keys in clusters.values()], dtype=float)
        samples = (deltas[rng.integers(0, len(deltas), size=(bootstrap, len(deltas)))].mean(1)
                   if len(deltas) > 1 else np.repeat(deltas, bootstrap))
        lo, hi = np.quantile(samples, [.025, .975])
        lower = metric in ("pallet_equivalent", "pallets", "time_s", "L4", "final_audit_issues")
        directional = deltas if lower else -deltas
        result["metrics"][metric] = dict(mean_delta=float(deltas.mean()), ci95=[float(lo), float(hi)],
                                         preferred_direction="lower" if lower else "higher",
                                         better=int(np.sum(directional < -1e-9)),
                                         equal=int(np.sum(np.abs(deltas) <= 1e-9)),
                                         worse=int(np.sum(directional > 1e-9)))
    failures = [r for r in rows if r.get("error")]
    model_rows = list(right.values())
    reasons = []
    if failures:
        reasons.append("EPISODE_ERROR")
    if len(clusters) < 10:
        reasons.append("TOO_FEW_INDEPENDENT_INVENTORIES_FOR_PROMOTION")
    if any(r["L4"] or r["final_audit_issues"] for r in model_rows):
        reasons.append("SIMULATED_SAFETY_ISSUES")
    if any(right[k]["placed"] < left[k]["placed"]
           or right[k]["placed_volume_m3"] + 1e-7 < left[k]["placed_volume_m3"] for k in left):
        reasons.append("LESS_COMPLETION_OR_VOLUME")
    if result["metrics"]["pallet_equivalent"]["ci95"][1] >= 0:
        reasons.append("PALLET_ADVANTAGE_NOT_ESTABLISHED")
    if any(not r.get("ems_verified", False) for r in model_rows):
        reasons.append("EMS_NOT_VERIFIED")
    if any(r.get("model_fallback_calls", 0) for r in model_rows):
        reasons.append("MODEL_FALLBACK_OCCURRED")
    result["deployment_gate"] = dict(
        status="KEEP_BASELINE" if reasons else "OFFLINE_CANDIDATE_ONLY",
        reasons=reasons, automatic_default_change=False,
        ros_robot_execution_verified=False)
    return result
