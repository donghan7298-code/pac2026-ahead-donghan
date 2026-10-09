#!/usr/bin/env python3
"""Scalable, resumable stage-5 training/evaluation using the leader's runtime.

Commands: generate, collect, train, evaluate, all. Config presets keep every
setting explicit. Existing team files and shipped models are never overwritten.
"""

import argparse
from pathlib import Path
import subprocess
import sys


def git_ref(path):
    done = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    return done.stdout.strip() if done.returncode == 0 else "UNVERIFIED_LOCAL"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("generate", "collect", "train", "evaluate", "all"))
    parser.add_argument("--team-root", type=Path, required=True)
    parser.add_argument("--generator-root", type=Path)
    parser.add_argument("--dataset", type=Path)
    parser.add_argument("--external-dataset", type=Path,
                        help="fresh independent evaluation dataset (train/val inventory overlap is rejected)")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--initial-model", type=Path, help="explicit compatible warm start on new data")
    parser.add_argument("--model", type=Path, help="evaluate an explicitly selected model")
    parser.add_argument("--team-ref")
    parser.add_argument("--generator-ref")
    parser.add_argument("--candidate-config", type=Path,
                        help="explicit candidate contract for a NEW compatible deployment-training run")
    parser.add_argument("--robot-config", type=Path,
                        help="explicit calibrated robot contract for a NEW run; does not change live training")
    parser.add_argument("--report-name", default="runtime_test")
    parser.add_argument("--workers", type=int)
    parser.add_argument("--epochs", type=int)
    args = parser.parse_args()
    own = Path(__file__).resolve().parents[2]
    team = args.team_root.resolve()
    sys.path[:0] = [str(own / "ros2_ws/src/pac_common"), str(own / "ros2_ws/src/pac_planning"),
                   *(str(team / "ros2_ws/src" / pkg) for pkg in
                     ("pac_candidates", "pac_highlevel", "pac_robot_check", "pac_runtime")),
                   str(team / "tools/virtual_data")]
    import yaml
    from pac_common import plain
    from pac_candidates import load_candidate_config
    from pac_highlevel import load_highlevel_config
    from pac_planning.config import PlannerConfig
    from pac_planning.model_pipeline import (collect_labels, evaluate_models, fit_models,
                                             source_fingerprint)
    from pac_planning.team_training import measurement_xy_assessment
    from pac_planning.training_data import atomic_json, file_digest
    from pac_robot_check import load_robot_check_config
    from pac_runtime import load_runtime_config
    from virtual_data import load_virtual_config
    from virtual_data.highlevel import family_indices
    from virtual_data.scenario_source import load_dataset, run_generator

    config = yaml.safe_load(args.config.read_text(encoding="utf-8"))
    if config.get("schema_version") != 1:
        parser.error("Unknown pipeline schema")
    if set(config) - {"schema_version", "data", "collection", "training", "evaluation", "planning"}:
        parser.error("Unknown pipeline config keys")
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    dataset_path = (args.dataset or output / "source_dataset").resolve()
    workers = args.workers if args.workers is not None else int(config["collection"]["workers"])
    if workers < 1:
        parser.error("workers must be positive")
    if args.epochs is not None and args.epochs < 1:
        parser.error("epochs must be positive")
    generator_ref = args.generator_ref or (git_ref(args.generator_root) if args.generator_root else "DATASET_SUPPLIED")
    source_refs = dict(team_ref=args.team_ref or git_ref(team), generator_ref=generator_ref)
    if args.command in ("generate", "all"):
        if args.generator_root is None:
            if args.dataset is None:
                parser.error("--generator-root or --dataset required")
        else:
            target = output / "source_dataset"
            generation_note = output / "generation.json"
            requested = dict(generator_ref=source_refs["generator_ref"],
                             default_config_sha256=file_digest(args.generator_root / "config/default.yaml"),
                             **config["data"])
            if target.exists() and any(target.iterdir()):
                import json
                if not generation_note.is_file() or json.loads(generation_note.read_text()) != requested:
                    parser.error("Existing generated data differs; use a new output directory")
            else:
                print(run_generator(args.generator_root.resolve(), own / "ros2_ws/src/pac_common", target,
                                    mode="sample", sample_per_family=int(config["data"]["sample_per_family"]),
                                    boxes_per_scenario=int(config["data"]["boxes_per_scenario"]),
                                    seed=int(config["data"]["seed"])), flush=True)
                atomic_json(generation_note, requested)
            dataset_path = target
        if args.command == "generate":
            return
    if args.external_dataset:
        if args.command != "evaluate":
            parser.error("--external-dataset is for evaluation only")
        dataset_path = args.external_dataset.resolve()
    dataset = load_dataset(dataset_path)
    candidate_path = (args.candidate_config or team / "config/taehyeon/candidates.yaml").resolve()
    cand = load_candidate_config(candidate_path)
    virtual = load_virtual_config(team / "config/taehyeon/virtual_data.yaml")
    high = load_highlevel_config(team / "config/taehyeon/highlevel.yaml")
    runtime = load_runtime_config(team / "config/taehyeon/runtime.yaml")
    robot_name = config["evaluation"].get("robot_config", "robot_check_gazebo.yaml")
    robot_path = (args.robot_config or team / "config/taehyeon" / robot_name).resolve()
    robot = load_robot_check_config(robot_path)
    if high.features.value_provider != "proxy" or high.buffer.slots != 4:
        parser.error("Expected unchanged team stage-4 proxy + 4-slot contract")
    planner = PlannerConfig(**config["planning"])
    saved_planner = output / "planner_config.yaml"
    contents = yaml.safe_dump({"schema_version": 1, "planning": plain(planner)}, sort_keys=False)
    if saved_planner.exists() and saved_planner.read_text() != contents:
        parser.error("Saved planner config differs; use a new run directory")
    saved_planner.write_text(contents, encoding="utf-8")
    context = dict(dataset=dataset, cand=cand, virtual=virtual, high=high, runtime=runtime,
                   robot=robot, planner=planner, split_seed=int(config["data"]["seed"]),
                   specs={s.scenario_id: s for s in dataset.scenarios},
                   family=family_indices(dataset),
                   position={s.scenario_id: i for i, s in enumerate(dataset.scenarios)},
                   spec_mismatch=float(config["collection"]["spec_mismatch_probability"]),
                   missing=float(config["collection"]["missing_probability"]), source_refs=source_refs,
                   runtime_source_sha256=source_fingerprint(team / "ros2_ws/src/pac_runtime/pac_runtime"))
    if any(not 0 <= context[k] <= 1 for k in ("spec_mismatch", "missing")):
        parser.error("Anomaly probabilities must lie in [0,1]")
    import json
    print(json.dumps({"measurement_assessment": measurement_xy_assessment(cand, virtual),
                      "candidate_config": str(candidate_path), "candidate_config_sha256": file_digest(candidate_path),
                      "robot_config": str(robot_path), "robot_config_sha256": file_digest(robot_path),
                      "team_ref": source_refs["team_ref"]}), flush=True)
    # Each stage owns its own immutable contract; collection/evaluation may
    # resume completed episode shards even if the training epoch target grows.
    if args.command in ("collect", "all"):
        collect_labels(context, output, workers=workers,
                       episode_seeds=tuple(config["collection"]["episode_seeds"]),
                       mixed_behaviour=bool(config["collection"].get("mixed_behaviour", True)),
                       log=lambda s: print(s, flush=True))
    if args.command in ("train", "all"):
        t = config["training"]
        fit_models(output, planner, model_seeds=tuple(t["model_seeds"]),
                   epochs=args.epochs if args.epochs is not None else int(t["epochs"]), hidden=int(t["hidden"]),
                   batch_queries=int(t["batch_queries"]), patience=int(t["patience"]),
                   learning_rate=float(t["learning_rate"]), resume=args.resume,
                   initial_model=args.initial_model, log=lambda s: print(s, flush=True))
    if args.command in ("evaluate", "all"):
        e = config["evaluation"]
        report = evaluate_models(context, output, model_path=args.model,
                                 episode_seeds=tuple(e["episode_seeds"]), variants=tuple(e["variants"]),
                                 workers=workers, external=bool(args.external_dataset),
                                 report_name=args.report_name, log=lambda s: print(s, flush=True))
        print(json.dumps({"report": str(output / args.report_name / "report.json"),
                          "summary": report["summary"], "scope": report["scope"]}), flush=True)


if __name__ == "__main__":
    main()
