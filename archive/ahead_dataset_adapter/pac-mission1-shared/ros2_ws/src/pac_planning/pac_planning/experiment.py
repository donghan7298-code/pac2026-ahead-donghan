"""Reproducible offline teacher, dataset aggregation, holdout and weight sweep.

All scene advancement here is a SIMULATION harness. Hidden arrival order stays
in this module; a planner receives only the current box and remaining counts.
"""

import argparse
from collections import Counter
from dataclasses import replace
import gzip
from itertools import islice
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


def episode_input(base, order_seed, dataset=None, model=None):
    if dataset is not None:
        from .ahead_dataset import observe_ahead_box

        episode = dataset.episode(base, model=model)
        return (
            episode.state, episode.context, episode.observations(), episode.family,
            lambda state, context, box, step: observe_ahead_box(state, box),
            episode.seed, episode.box_count,
        )
    state, context, arrivals, kind = make_episode(base, order_seed)
    return state, context, iter(arrivals), kind, observe_next, base, len(arrivals)


def collect_groups(base_ids, config, model=None, iteration=0, max_steps=5, dataset=None):
    groups = []
    for base in base_ids:
        state, context, arrivals, order_kind, observe, seed, _ = episode_input(
            base, iteration, dataset, model
        )
        planner = make_planner(context, config, model)
        for step, item in enumerate(islice(arrivals, max_steps)):
            box, state = observe(state, context, item, step)
            teacher = planner.plan(
                box, state, seed=seed + step, mode="teacher"
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


def benchmark(base_ids, config, model, dataset=None, max_steps=None):
    results = []
    for base in base_ids:
        for mode in ("greedy", "current", "teacher", "ranking", "ahead"):
            state, context, arrivals, kind, observe, seed, total_boxes = episode_input(
                base, base % 4 if dataset is None else 0, dataset, model
            )
            requested = total_boxes if max_steps is None else min(total_boxes, max_steps)
            planner = make_planner(context, config, model)
            times = []
            placed = 0
            min_safety = 1.0
            total_proxy_time = 0.0
            for step, item in enumerate(islice(arrivals, requested)):
                box, state = observe(state, context, item, step)
                result = planner.plan(
                    box,
                    state,
                    seed=seed + step,
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
                    "requested_boxes": requested,
                    "total_episode_boxes": total_boxes,
                    "truncated_for_smoke": requested < total_boxes,
                    "volume_utilization": sum(
                        volume(b.size) for b in state.pallet.boxes
                    )
                    / volume(state.pallet.size),
                    "blocked": placed < requested,
                    "min_saturated_safety": min_safety,
                    "estimated_robot_time_proxy_sec": total_proxy_time,
                    "decision_times_sec": times,
                    "robot_validation": "NOT_CHECKED",
                }
            )
            print(
                f"benchmark {base} {mode}: {placed}/{requested}",
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
    parser.add_argument("--ahead-dataset", help="AHEAD generated output directory")
    parser.add_argument("--generator-config")
    parser.add_argument("--adapter-config")
    parser.add_argument("--max-steps", type=int, default=4)
    parser.add_argument("--benchmark-max-steps", type=int)
    args = parser.parse_args()
    if (min(args.train_groups, args.validation_groups, args.holdout_groups,
            args.epochs, args.max_steps) < 1 or args.aggregate_rounds < 0
            or (args.benchmark_max_steps is not None and args.benchmark_max_steps < 1)):
        parser.error("Group counts, epochs and steps must be positive")
    dataset = None
    if args.ahead_dataset:
        if not args.generator_config or not args.adapter_config:
            parser.error("AHEAD input requires --generator-config and --adapter-config")
        from .ahead_dataset import AheadDataset

        dataset = AheadDataset(args.ahead_dataset, args.generator_config, args.adapter_config)
    elif args.generator_config or args.adapter_config:
        parser.error("Specify --ahead-dataset with external configuration")
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    config = PlannerConfig(horizon=3, scenario_count=7, top_k=4)
    train_ids = list(range(100, 100 + args.train_groups))
    val_ids = list(range(1000, 1000 + args.validation_groups))
    holdout_ids = list(range(2000, 2000 + args.holdout_groups))
    if dataset is not None:
        train_ids = dataset.splits["train"][:args.train_groups]
        val_ids = dataset.splits["val"][:args.validation_groups]
        holdout_ids = dataset.splits["test"][:args.holdout_groups]
        if not train_ids or not val_ids or not holdout_ids:
            parser.error("AHEAD train/val/test splits must all be nonempty")
    validation = collect_groups(val_ids, config, max_steps=args.max_steps, dataset=dataset)
    training = collect_groups(train_ids, config, max_steps=args.max_steps, dataset=dataset)
    model, history = train_model(
        training, validation, seed=args.seed, epochs=args.epochs
    )
    if dataset is not None:
        model.payload["dataset_contract"] = dataset.contract
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
                max_steps=args.max_steps,
                dataset=dataset,
            )
        )
        model, history = train_model(
            training,
            validation,
            seed=args.seed + iteration,
            epochs=args.epochs,
        )
        if dataset is not None:
            model.payload["dataset_contract"] = dataset.contract
        rounds.append(
            {
                "iteration": iteration,
                "training_queries": len(training),
                "validation": ranking_report(model, validation),
            }
        )
    # Holdout is read only after model/epoch selection and is never used for fitting.
    holdout = collect_groups(holdout_ids, config, max_steps=args.max_steps, dataset=dataset)
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
        "dataset_contract": dataset.contract if dataset else None,
        "adapter_policy": dataset.policy if dataset else None,
        "source_manifest": dataset.manifest if dataset else None,
        "max_steps": args.max_steps,
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
        output / "benchmark.json", benchmark(
            holdout_ids, config, model, dataset=dataset,
            max_steps=args.benchmark_max_steps,
        )
    )
    print(
        json.dumps({"output": str(output), "holdout": report["holdout"]}),
        flush=True,
    )


if __name__ == "__main__":
    main()
