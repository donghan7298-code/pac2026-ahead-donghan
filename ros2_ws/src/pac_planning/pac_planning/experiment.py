"""Reproducible offline teacher, dataset aggregation, holdout and weight sweep.

All scene advancement here is a SIMULATION harness. Hidden arrival order stays
in this module; a planner receives only the current box and remaining counts.
"""

import argparse
from collections import Counter
from dataclasses import replace
import gzip
import json
import math
from pathlib import Path
import random
import time
import numpy as np
from pac_common import (
    BoxState,
    BoxStatus,
    InventoryState,
    PalletState,
    PlanningContext,
    Pose3D,
    Size3D,
    SkuSpec,
    SystemState,
    plain,
)
from .config import PlannerConfig
from .features import FEATURE_NAMES
from .geometry import simulate_placement, volume
from .model import DualHeadRanker, OUTPUT_NAMES, train_model
from .planner import PlacementPlanner
from .reference_backend import ReferenceBackend
from .scoring import priority


def make_episode(base_group, order_seed=0):
    rng = random.Random(base_group)
    scale = rng.choice((0.8, 1.0, 1.2))
    specs = [
        (0.4, 0.3, 0.2, 8),
        (0.3, 0.2, 0.15, 4),
        (0.6, 0.4, 0.25, 12),
        (0.2, 0.4, 0.2, 6),
    ]
    catalog = {}
    for i, (x, y, z, weight) in enumerate(specs):
        sku = chr(65 + i)
        # Separate inventory groups vary geometry as well as order.
        factor = rng.choice((0.9, 1.0, 1.1))
        catalog[sku] = SkuSpec(
            sku,
            Size3D(x * scale * factor, y * scale, z * scale),
            weight,
            (0.0, math.pi / 2),
            weight * 35,
        )
    arrivals = [sku for sku in catalog for _ in range(rng.randint(1, 3))]
    rr = random.Random(base_group * 1009 + order_seed)
    rr.shuffle(arrivals)
    kind = ("random", "heavy_late", "sku_run", "small_early")[order_seed % 4]
    if kind == "heavy_late":
        arrivals.sort(key=lambda sku: catalog[sku].weight_kg)
    elif kind == "sku_run":
        arrivals.sort()
    elif kind == "small_early":
        arrivals.sort(key=lambda sku: volume(catalog[sku].size))
    state = SystemState(
        0,
        0.0,
        PalletState(
            "P001", Size3D(1.0 * scale, 0.8 * scale, 0.65 * scale), ()
        ),
        InventoryState({}, dict(Counter(arrivals))),
    )
    context = PlanningContext(catalog, 150.0)
    return state, context, arrivals, kind


def observe_next(state, context, sku, index):
    spec = context.catalog[sku]
    box = BoxState(
        f"B{index:03d}",
        sku,
        spec.size,
        spec.weight_kg,
        Pose3D("conveyor", 0.0, 0.0, 0.0),
        spec.allowed_yaws_rad,
        BoxStatus.READY_FOR_PICK,
        1.0,
        float(index),
        "OFFLINE_SIMULATION",
    )
    tracked = dict(state.inventory.tracked_boxes)
    tracked[box.box_id] = box
    remaining = dict(state.inventory.remaining_by_sku)
    remaining[sku] -= 1
    # Only this simulation State Manager advances its virtual version.
    state = replace(
        state,
        state_version=state.state_version + 1,
        stamp_sec=float(index),
        inventory=InventoryState(tracked, remaining),
    )
    return box, state


def make_planner(context, config, model=None):
    backend = ReferenceBackend(context, config)
    return PlacementPlanner(
        context=context,
        config=config,
        model=model,
        generate_candidates=backend.generate_candidates,
        validate_constraints=backend.validate_constraints,
    )


def collect_groups(base_ids, config, model=None, iteration=0, max_steps=5):
    groups = []
    for base in base_ids:
        state, context, arrivals, order_kind = make_episode(base, iteration)
        planner = make_planner(context, config, model)
        for step, sku in enumerate(arrivals[:max_steps]):
            box, state = observe_next(state, context, sku, step)
            teacher = planner.plan(
                box, state, seed=base + step, mode="teacher"
            )
            if not teacher.ranked:
                break
            rows = []
            for e in teacher.evaluations:
                # Saturating safety remains lexicographically prior to utility.
                safety = e.features.metrics["safety"]
                utility = (
                    2.0 if safety >= 1 - 1e-8 else safety
                ) + e.candidate.score
                rows.append(
                    {
                        "candidate_id": e.candidate.candidate_id,
                        "features": list(e.features.values),
                        "static": dict(e.features.metrics),
                        "future": {
                            k: getattr(e.future, k) for k in OUTPUT_NAMES
                        },
                        "teacher_score": utility,
                    }
                )
            groups.append(
                {
                    "base_group": base,
                    "group_id": f"{base}-{iteration}-{step}",
                    "iteration": iteration,
                    "order_kind": order_kind,
                    "rows": rows,
                }
            )
            if model is None:
                chosen = planner.plan(
                    box, state, mode="greedy", use_time_budget=False
                ).ranked[0]
            else:
                # Student roll-in; expert relabels ALL candidates in visited states.
                chosen = planner.plan(
                    box, state, mode="ranking", use_time_budget=False
                ).ranked[0]
            state = simulate_placement(state, box, chosen).state
        print(
            f"teacher inventory {base}: {len(groups)} accumulated query groups",
            flush=True,
        )
    return groups


def ranking_report(model, groups, top_k=4):
    from pac_common import FeatureVector

    hits, regrets, mse = [], [], []
    for group in groups:
        vectors = [
            FeatureVector(
                FEATURE_NAMES, tuple(r["features"]), {}, "OFFLINE", "OFFLINE"
            )
            for r in group["rows"]
        ]
        pred = model.predict(vectors)
        order = sorted(range(len(pred)), key=lambda i: -pred[i][0])
        truth = [r["teacher_score"] for r in group["rows"]]
        best = max(truth)
        hits.append(any(truth[i] >= best - 1e-8 for i in order[:top_k]))
        regrets.append(best - truth[order[0]])
        for (_, value), row in zip(pred, group["rows"]):
            mse.append(
                [
                    (getattr(value, n) - row["future"][n]) ** 2
                    for n in OUTPUT_NAMES
                ]
            )
    return {
        "queries": len(groups),
        "candidates": sum(len(g["rows"]) for g in groups),
        "teacher_best_top_k_recall": float(np.mean(hits)),
        "teacher_top1_regret": float(np.mean(regrets)),
        "output_mse": dict(
            zip(OUTPUT_NAMES, map(float, np.mean(mse, axis=0)))
        ),
    }


def benchmark(base_ids, config, model):
    results = []
    for base in base_ids:
        for mode in ("greedy", "current", "teacher", "ranking", "ahead"):
            state, context, arrivals, kind = make_episode(base, base % 4)
            planner = make_planner(context, config, model)
            times = []
            placed = 0
            min_safety = 1.0
            total_proxy_time = 0.0
            for step, sku in enumerate(arrivals):
                box, state = observe_next(state, context, sku, step)
                result = planner.plan(
                    box,
                    state,
                    seed=base + step,
                    mode=mode,
                    use_time_budget=False,
                )
                times.append(result.diagnostics["planning_time_sec"])
                if not result.ranked:
                    break
                e = result.evaluations[0]
                min_safety = min(min_safety, e.features.metrics["safety"])
                total_proxy_time += e.features.metrics["estimated_time_sec"]
                state = simulate_placement(state, box, result.ranked[0]).state
                placed += 1
            results.append(
                {
                    "base_group": base,
                    "order_kind": kind,
                    "mode": mode,
                    "placed_boxes": placed,
                    "requested_boxes": len(arrivals),
                    "volume_utilization": sum(
                        volume(b.size) for b in state.pallet.boxes
                    )
                    / volume(state.pallet.size),
                    "blocked": placed < len(arrivals),
                    "min_saturated_safety": min_safety,
                    "estimated_robot_time_proxy_sec": total_proxy_time,
                    "decision_times_sec": times,
                    "robot_validation": "NOT_CHECKED",
                }
            )
            print(
                f"benchmark {base} {mode}: {placed}/{len(arrivals)}",
                flush=True,
            )
    summary = {}
    for mode in ("greedy", "current", "teacher", "ranking", "ahead"):
        rows = [r for r in results if r["mode"] == mode]
        times = [t for r in rows for t in r["decision_times_sec"]]
        summary[mode] = {
            "episodes": len(rows),
            "mean_utilization": float(
                np.mean([r["volume_utilization"] for r in rows])
            ),
            "mean_placed_boxes": float(
                np.mean([r["placed_boxes"] for r in rows])
            ),
            "blocked_episode_rate": float(
                np.mean([r["blocked"] for r in rows])
            ),
            "mean_decision_sec": float(np.mean(times)),
            "p95_decision_sec": float(np.percentile(times, 95)),
            "worst_utilization": min(r["volume_utilization"] for r in rows),
        }
    return {
        "scope": "small synthetic offline smoke benchmark; fixed work, no robot physics",
        "summary": summary,
        "episodes": results,
    }


def weight_sweep(groups, config):
    """Validation-only sensitivity on cached teacher queries, not episode tuning."""
    results = []
    variants = [("baseline", dict(config.weights))]
    for key in config.weights:
        for factor in (0.8, 1.2):
            weights = dict(config.weights)
            weights[key] *= factor
            variants.append((f"{key}_x{factor}", weights))
    for name, weights in variants:
        total = sum(weights.values())
        w = {k: v / total for k, v in weights.items()}
        picks = []
        for group in groups:

            def utility(row):
                s, f = row["static"], row["future"]
                risk = (
                    f["mean"]
                    - f["cvar"]
                    + 0.25 * f["blocking_rate"]
                    + 0.25 * f["failure_rate"]
                )
                return (
                    w["safety"] * s["safety"]
                    + w["space"] * s["space"]
                    + w["future"] * f["mean"]
                    - w["risk"] * risk
                    - w["time"] * s["time"]
                )

            def key(row):
                safety = row["static"]["safety"]
                return (
                    safety >= 1 - 1e-8,
                    0 if safety >= 1 - 1e-8 else safety,
                    utility(row),
                )

            picks.append(max(group["rows"], key=key))
        results.append(
            {
                "name": name,
                "weights": w,
                "mean_future_value": float(
                    np.mean([r["future"]["mean"] for r in picks])
                ),
                "mean_blocking": float(
                    np.mean([r["future"]["blocking_rate"] for r in picks])
                ),
                "mean_time_proxy_sec": float(
                    np.mean([r["static"]["estimated_time_sec"] for r in picks])
                ),
            }
        )
    for row in results:
        row["pareto"] = not any(
            x["mean_future_value"] >= row["mean_future_value"]
            and x["mean_blocking"] <= row["mean_blocking"]
            and x["mean_time_proxy_sec"] <= row["mean_time_proxy_sec"]
            and (
                x["mean_future_value"] > row["mean_future_value"]
                or x["mean_blocking"] < row["mean_blocking"]
                or x["mean_time_proxy_sec"] < row["mean_time_proxy_sec"]
            )
            for x in results
        )
    return {
        "scope": "validation candidate-level sensitivity; weights remain provisional",
        "variants": results,
    }


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="runs/training")
    parser.add_argument("--train-groups", type=int, default=12)
    parser.add_argument("--validation-groups", type=int, default=4)
    parser.add_argument("--holdout-groups", type=int, default=4)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--aggregate-rounds", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20261007)
    args = parser.parse_args()
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    config = PlannerConfig(horizon=3, scenario_count=7, top_k=4)
    train_ids = list(range(100, 100 + args.train_groups))
    val_ids = list(range(1000, 1000 + args.validation_groups))
    holdout_ids = list(range(2000, 2000 + args.holdout_groups))
    validation = collect_groups(val_ids, config, max_steps=4)
    training = collect_groups(train_ids, config, max_steps=4)
    model, history = train_model(
        training, validation, seed=args.seed, epochs=args.epochs
    )
    rounds = [
        {
            "iteration": 0,
            "training_queries": len(training),
            "validation": ranking_report(model, validation),
        }
    ]
    for iteration in range(1, args.aggregate_rounds + 1):
        training.extend(
            collect_groups(
                train_ids,
                config,
                model=model,
                iteration=iteration,
                max_steps=4,
            )
        )
        model, history = train_model(
            training,
            validation,
            seed=args.seed + iteration,
            epochs=args.epochs,
        )
        rounds.append(
            {
                "iteration": iteration,
                "training_queries": len(training),
                "validation": ranking_report(model, validation),
            }
        )
    # Holdout is read only after model/epoch selection and is never used for fitting.
    holdout = collect_groups(holdout_ids, config, max_steps=4)
    report = {
        "seed": args.seed,
        "config": plain(config),
        "train_ids": train_ids,
        "validation_ids": val_ids,
        "holdout_ids": holdout_ids,
        "rounds": rounds,
        "train": ranking_report(model, training),
        "validation": ranking_report(model, validation),
        "holdout": ranking_report(model, holdout),
        "history": history,
        "algorithm": "NumPy dual-head MLP; LambdaRank; student roll-in dataset aggregation",
        "limits": "Synthetic single-support reference geometry; not hardware validation or production accuracy",
    }
    model.payload["rollout_contract"] = {
        "horizon": config.horizon,
        "scenario_count": config.scenario_count,
        "cvar_alpha": config.cvar_alpha,
        "backend": "REFERENCE_FULL_SINGLE_SUPPORT",
    }
    model.save(output / "dual_head_ranker.json")
    write_json(output / "training_report.json", report)
    write_json(
        output / "weight_sensitivity.json", weight_sweep(validation, config)
    )
    for name, groups in (
        ("train", training),
        ("validation", validation),
        ("holdout", holdout),
    ):
        with gzip.open(
            output / f"{name}.jsonl.gz", "wt", encoding="utf-8"
        ) as stream:
            for group in groups:
                stream.write(json.dumps(group, allow_nan=False) + "\n")
    write_json(
        output / "benchmark.json", benchmark(holdout_ids, config, model)
    )
    print(
        json.dumps({"output": str(output), "holdout": report["holdout"]}),
        flush=True,
    )


if __name__ == "__main__":
    main()
