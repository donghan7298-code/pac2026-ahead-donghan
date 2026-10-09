#!/usr/bin/env python3
"""Train stage 5-4 from jaesung's generator and the leader's real backend.

This is offline low-level retraining, not PPO or robot/ROS execution.
It writes a new model without replacing the shipped reference model.
"""

import argparse
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import sys


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-root", type=Path, required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--dataset", type=Path)
    source.add_argument("--generator-root", type=Path)
    parser.add_argument("--sample-per-family", type=int, default=3)
    parser.add_argument("--boxes-per-scenario", type=int, default=24)
    parser.add_argument("--team-ref", default="UNVERIFIED_LOCAL")
    parser.add_argument("--generator-ref", default="UNVERIFIED_LOCAL")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--epochs", type=int, default=80)
    parser.add_argument("--planner-config", type=Path)
    parser.add_argument("--horizon", type=int)
    parser.add_argument("--scenario-count", type=int)
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if min(args.epochs, args.sample_per_family, args.boxes_per_scenario) < 1:
        parser.error("epochs/sample-per-family/boxes-per-scenario must be positive")
    if args.output.exists() and any(args.output.iterdir()):
        parser.error("Use a new or empty output directory; existing models are not overwritten")
    own = Path(__file__).resolve().parents[2]
    team = args.team_root.resolve()
    sys.path[:0] = [str(own / "ros2_ws/src/pac_common"), str(own / "ros2_ws/src/pac_planning"),
                   str(team / "ros2_ws/src/pac_candidates"), str(team / "ros2_ws/src/pac_highlevel"),
                   str(team / "tools/virtual_data")]
    from pac_common import plain
    from pac_candidates import load_candidate_config
    from pac_highlevel import load_highlevel_config
    from pac_planning.config import PlannerConfig, load_config
    from pac_planning.model import train_model
    from pac_planning.team_training import (benchmark_holdout, collect_teacher, query_report,
                                            training_splits, measurement_xy_assessment)
    from virtual_data import load_virtual_config
    from virtual_data.scenario_source import load_dataset, run_generator

    args.output.mkdir(parents=True, exist_ok=True)
    dataset_path = args.dataset
    if args.generator_root:
        dataset_path = args.output / "source_dataset"
        print(run_generator(args.generator_root.resolve(), own / "ros2_ws/src/pac_common",
                            dataset_path, sample_per_family=args.sample_per_family, seed=args.seed,
                            boxes_per_scenario=args.boxes_per_scenario), flush=True)
    dataset = load_dataset(dataset_path.resolve())
    cand = load_candidate_config(team / "config/taehyeon/candidates.yaml")
    virtual = load_virtual_config(team / "config/taehyeon/virtual_data.yaml")
    high = load_highlevel_config(team / "config/taehyeon/highlevel.yaml")
    measurement_assessment = measurement_xy_assessment(cand, virtual)
    print(json.dumps({"measurement_geometry_assessment": measurement_assessment}), flush=True)
    config = load_config(args.planner_config) if args.planner_config else PlannerConfig()
    overrides = {name: getattr(args, name) for name in ("horizon", "scenario_count", "top_k")
                 if getattr(args, name) is not None}
    config = replace(config, **overrides)
    splits, split_note = training_splits(dataset, args.seed)
    write_json(args.output / "effective_splits.json", {"splits": splits, **split_note})
    write_json(args.output / "planner_config.json", plain(config))
    # Save runnable YAML: model rollout settings must match later inference.
    import yaml
    (args.output / "planner_config.yaml").write_text(
        yaml.safe_dump({"schema_version": 1, "planning": plain(config)}, sort_keys=False), encoding="utf-8")
    groups, summaries, contract = collect_teacher(dataset, splits, cand, virtual, high, config,
                                                  seed=args.seed, log=lambda msg: print(msg, flush=True))
    for split, rows in groups.items():
        with gzip.open(args.output / f"teacher_{split}.jsonl.gz", "wt", encoding="utf-8") as f:
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
    model, history = train_model(groups["train"], groups["val"], seed=args.seed, epochs=args.epochs)
    provenance = {
        "team_ref": args.team_ref, "generator_ref": args.generator_ref,
        "generator_manifest": dataset.manifest, "scope": "OFFLINE_SIMULATION",
        "high_level_policy": "RULE", "high_level_value_provider": "proxy", "ppo_trained": False,
        "source_dataset_sha256": {
            str(p.relative_to(dataset.root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for folder in ("test_data", "ground_truth", "analysis")
            for p in sorted((dataset.root / folder).glob("*")) if p.is_file()
        },
        "virtual_config": plain(virtual), "highlevel_config": plain(high),
        "effective_splits": splits, "split_note": split_note,
        "planner_config": plain(config), "initialization": "NEW_MODEL",
        "measurement_geometry_assessment": measurement_assessment,
    }
    model.payload.update(backend_contract=contract, training_provenance=provenance,
                         rollout_contract={name: getattr(config, name)
                                           for name in ("horizon", "scenario_count", "cvar_alpha")})
    model_path = args.output / "team_dual_head_ranker.json"
    model.save(model_path)
    write_json(args.output / "training_history.json", history)
    report = {
        "scope": "OFFLINE_TRAINING_AND_EVALUATION", "robot_execution": "NOT_RUN",
        "ros2_execution": "NOT_RUN", "provenance": provenance,
        "backend_contract": contract, "selected_epoch": history["selected_epoch"],
        "teacher_episodes": summaries,
        "query_evaluation": {
            split: {"model": query_report(rows, config, model),
                    "heuristic": query_report(rows, config)} for split, rows in groups.items()
        },
    }
    write_json(args.output / "training_report.json", report)
    report["holdout_benchmark"] = benchmark_holdout(
        dataset, splits["test"], cand, virtual, high, config, model_path, seed=args.seed,
        log=lambda msg: print(msg, flush=True))
    write_json(args.output / "training_report.json", report)
    print(json.dumps({"model": str(model_path), "report": str(args.output / "training_report.json"),
                      "train_queries": len(groups["train"]), "val_queries": len(groups["val"]),
                      "test_queries": len(groups["test"]), "scope": report["scope"]}), flush=True)


if __name__ == "__main__":
    main()
