"""Generator data -> resumable runtime teacher -> model -> paired holdout.

This module runs in a Python process with the pinned team packages on its
path. The command-line entry point configures those paths, avoiding duplicate
pac_common imports from the simulator or generator repository.
"""

from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import replace
import gzip
import json
import multiprocessing
from pathlib import Path
import time

import numpy as np
from pac_common import plain

from .config import PlannerConfig
from .evaluation import packed_query_report, paired_comparison
from .model import DualHeadRanker, train_model
from .team_bridge import TeamRuntimeRanker
from .team_training import RuntimeTeacherRanker, digest, inventory_group, training_splits
from .training_data import PackedQueries, atomic_json, file_digest, pack_queries

_JOB = None


def source_fingerprint(directory):
    return {p.name: file_digest(p) for p in sorted(Path(directory).glob("*.py"))}


def dataset_fingerprint(dataset):
    # Ignore generation timestamps, but pin every planner/truth/split input.
    files = [dataset.root / "splits.json", dataset.root / "analysis/catalog.csv"]
    files += sorted((dataset.root / "test_data").glob("S*.json"))
    files += sorted((dataset.root / "ground_truth").glob("S*.json"))
    return digest({str(p.relative_to(dataset.root)): file_digest(p) for p in files})


def _init_worker(context):
    global _JOB
    _JOB = context


def _make_cell(spec, seed):
    from virtual_data.runtime_cell import runtime_cell

    ctx = _JOB
    return runtime_cell(spec, ctx["dataset"], ctx["cand"], ctx["virtual"], seed=seed,
                        family_index=ctx["family"][spec.scenario_id],
                        scenario_index=ctx["position"][spec.scenario_id],
                        spec_mismatch_probability=ctx["spec_mismatch"],
                        missing_probability=ctx["missing"])


def runtime_metrics(output, cell):
    """Final true layout audit is analytical, separate from simulated L0-L4."""
    from pac_candidates import CandidateBackend
    from pac_common import InventoryState, PalletState, PlacedBox, PlanningContext, Pose3D, Size3D, SystemState
    from pac_planning.geometry import bounds

    ctx = _JOB
    by_id = {fb.truth.box_id: fb for fb in cell.stream}
    overrides = {bid: fb.capacity_n for bid, fb in by_id.items() if fb.capacity_n is not None}
    audit_context = PlanningContext(catalog=cell.catalog, pallet_max_weight_kg=cell.pallet_max_weight_kg,
                                    capacity_overrides_n=overrides)
    audit, volume = Counter(), 0.
    limit = (cell.pallet_size.x, cell.pallet_size.y, cell.pallet_size.z)
    for pallet in output["pallet_list"]:
        placed = []
        for b in pallet["layout"]:
            truth = by_id[b["box_id"]].truth
            p = b["pose"]
            placed.append(PlacedBox(b["box_id"], truth.sku_id, Size3D(*b["size"]),
                                    truth.weight_kg, Pose3D("pallet", p[0], p[1], p[2], yaw=p[3])))
            volume += float(np.prod(b["size"]))
        state = SystemState(0, 0., PalletState(pallet["pallet_id"], cell.pallet_size, tuple(placed)),
                            InventoryState({}, {}))
        backend = CandidateBackend(audit_context, ctx["cand"])
        for codes in backend.model_for(state).snapshot_issues().values():
            audit.update(codes)
        prior = []
        tolerance = ctx["runtime"].verify.max_overlap_m
        for b in placed:
            lo, hi = bounds(b)
            if any(a < -tolerance or z > upper + tolerance for a, z, upper in zip(lo, hi, limit)):
                audit["PROTRUSION"] += 1
            for a, z in prior:
                if all(min(v, zz) - max(u, aa) > tolerance for u, v, aa, zz in zip(lo, hi, a, z)):
                    audit["OVERLAP"] += 1
            prior.append((lo, hi))
    calls = output["stage6"].get("calls", 0)
    prov = output.get("ranker_provenance", {})
    statuses = prov.get("model_statuses", {})
    return dict(placed=output["placed"], pallets=output["pallets"],
                fill_true_mean=output["fill_true_mean"], time_s=output["time_s"],
                placed_volume_m3=volume,
                pallet_equivalent=output["pallets"] - volume / np.prod(limit),
                L4=output["decisions"].get("L4", 0),
                final_audit_issues=sum(audit.values()), final_audit_codes=dict(audit),
                executability_rate=(calls - output["stage6"].get("no_executable", 0)) / max(1, calls),
                ems_verified=prov.get("ems_verified", False),
                model_fallback_calls=sum(n for s, n in statuses.items()
                                         if "FAILED" in s or "FALLBACK" in s),
                inspection=len(output["inspection"]), missing=sum(output["missing"].values()),
                stage6=output["stage6"], decisions=output["decisions"],
                ranker_provenance=prov)


def _run_runtime(spec, seed, ranker, variant="nominal"):
    from pac_highlevel import load_policy
    from pac_robot_check import RobotFeasibility
    from pac_runtime import RuntimeLoop

    ctx = _JOB
    cell = _make_cell(spec, seed)
    rt = replace(ctx["runtime"], seed=seed)
    if variant == "clean":
        rt = replace(rt, perception=replace(rt.perception, weight_noise_std_ratio=0., size_noise_std_m=0.,
                                            uncertain_probability=0., label_fail_probability=0.,
                                            base_view_size_noise_std_m=0., damage_detect_probability=1.),
                     execution=replace(rt.execution, place_xy_noise_std_m=0., grip_fail_probability=0.))
    elif variant == "noisy3mm":
        rt = replace(rt, execution=replace(rt.execution, place_xy_noise_std_m=.003))
    elif variant != "nominal":
        raise ValueError("Unknown runtime variant")
    output = RuntimeLoop(cell, ctx["cand"], ctx["high"], rt, RobotFeasibility(ctx["robot"]),
                         load_policy("rule", config=ctx["high"]), ranker=ranker, log_events=True).run()
    # This hash proves every method receives identical true boxes/anomalies.
    # Executor random draws may diverge after policies take different actions.
    stream_hash = digest(plain(cell))
    return output, cell, stream_hash


def _collect_task(task):
    split, sid, seed, behaviour = task
    ctx = _JOB
    spec = ctx["specs"][sid]
    key = f"{split}-{sid}-{seed}-{behaviour}"
    shard = ctx["output"] / "shards" / (key + ".jsonl.gz")
    status_path = ctx["output"] / "shards" / (key + ".status.json")
    if shard.is_file() and status_path.is_file():
        saved = json.loads(status_path.read_text())
        if (saved["collection_digest"] == ctx["digest"] and saved["shard_sha256"] == file_digest(shard)):
            return saved
        raise ValueError("Completed shard changed or its collection contract differs: " + key)
    started = time.perf_counter()
    teacher = RuntimeTeacherRanker(ctx["planner"], sid, inventory_group(spec),
                                  seed=seed, behaviour=behaviour)
    output, _, stream_hash = _run_runtime(spec, seed, teacher)
    if not teacher.groups or teacher.contract is None:
        raise ValueError("No complete EMS teacher queries: " + key)
    shard.parent.mkdir(parents=True, exist_ok=True)
    tmp = shard.with_name(shard.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as f:
        for group in teacher.groups:
            f.write(json.dumps(group, allow_nan=False, ensure_ascii=False) + "\n")
    tmp.replace(shard)
    saved = dict(task=key, split=split, scenario_id=sid, seed=seed, behaviour=behaviour,
                 shard=str(shard), shard_sha256=file_digest(shard), collection_digest=ctx["digest"],
                 inventory_group=inventory_group(spec), backend_contract=teacher.contract,
                 queries=len(teacher.groups), candidates=sum(len(g["rows"]) for g in teacher.groups),
                 placed=output["placed"], boxes=len(spec.arrivals),
                 stream_sha256=stream_hash, feature_rejects=dict(teacher.rejected),
                 wall_time_sec=time.perf_counter() - started)
    atomic_json(status_path, saved)
    return saved


def collect_labels(context, output, *, episode_seeds=(7,), workers=2, mixed_behaviour=True, log=print):
    output = Path(output).resolve()
    dataset = context["dataset"]
    splits, split_note = training_splits(dataset, seed=context["split_seed"])
    contract = {"dataset_sha256": dataset_fingerprint(dataset), "effective_splits": splits,
                "split_note": split_note, "planner": plain(context["planner"]),
                "candidate": plain(context["cand"]), "virtual": plain(context["virtual"]),
                "runtime": plain(context["runtime"]), "robot": plain(context["robot"]),
                "highlevel": plain(context["high"]), "spec_mismatch": context["spec_mismatch"],
                "missing": context["missing"], "episode_seeds": list(episode_seeds),
                "mixed_behaviour": mixed_behaviour,
                "planner_source_sha256": source_fingerprint(Path(__file__).parent),
                "runtime_source_sha256": context["runtime_source_sha256"],
                "source_refs": context["source_refs"],
                "scope": "PYTHON_RUNTIME_SIMULATION", "highlevel_policy": "rule",
                "highlevel_value_provider": "proxy", "ppo_trained": False}
    contract_digest = digest(contract)
    path = output / "collection.json"
    if path.is_file() and json.loads(path.read_text())["digest"] != contract_digest:
        if any((output / "shards").glob("*.status.json")):
            raise ValueError("Existing collection belongs to a different dataset/config/source; use a new output")
    atomic_json(path, {"digest": contract_digest, **contract})
    ctx = {**context, "output": output, "digest": contract_digest}
    tasks = [(split, sid, int(seed),
              "dblf" if mixed_behaviour and split == "train" and i % 2 else "teacher")
             for split, ids in splits.items() for sid in ids for i, seed in enumerate(episode_seeds)]
    rows = []
    # Offline Ubuntu jobs only: fork inherits FrozenDict safely. The immutable
    # shared snapshots intentionally deny pickle's dict reconstruction writes.
    # No ROS node/controller is started in these worker processes.
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork"),
                             initializer=_init_worker, initargs=(ctx,)) as pool:
        future = {pool.submit(_collect_task, task): task for task in tasks}
        for f in as_completed(future):
            row = f.result()  # an error never creates a completed pack
            rows.append(row)
            log(json.dumps({"collected": len(rows), "total": len(tasks), "task": row["task"],
                            "queries": row["queries"], "candidates": row["candidates"]}))
    rows.sort(key=lambda r: r["task"])
    backend = rows[0]["backend_contract"]
    if any(r["backend_contract"] != backend for r in rows):
        raise ValueError("Teacher backend contract changed between episodes")
    packs = {}
    for split in splits:
        directory = output / "packed" / split
        if (directory / "metadata.json").is_file():
            packs[split] = PackedQueries(directory)
        else:
            packs[split] = pack_queries([r["shard"] for r in rows if r["split"] == split], directory)
    if any(packs[a].base_groups & packs[b].base_groups for a, b in
           (("train", "val"), ("train", "test"), ("val", "test"))):
        raise ValueError("Packed inventory-group split leakage")
    summary = dict(backend_contract=backend, episodes=rows,
                   counts={s: dict(queries=len(p), candidates=len(p.features),
                                   inventory_groups=len(p.base_groups)) for s, p in packs.items()})
    atomic_json(output / "collection_summary.json", summary)
    return summary


def fit_models(output, planner, *, model_seeds=(7, 17, 29), epochs=150, hidden=64,
               batch_queries=8, patience=25, learning_rate=.002, resume=False,
               initial_model=None, log=print):
    output = Path(output)
    manifest = json.loads((output / "collection.json").read_text())
    summary = json.loads((output / "collection_summary.json").read_text())
    if plain(planner) != manifest["planner"]:
        raise ValueError("Training planner config must match the teacher")
    train, val = (PackedQueries(output / "packed" / s) for s in ("train", "val"))
    test_base = set(json.loads((output / "packed/test/metadata.json").read_text())["base_groups"])
    if train.base_groups & test_base or val.base_groups & test_base:
        raise ValueError("Test inventory leakage")
    initial = DualHeadRanker.load(initial_model) if initial_model is not None else None
    if initial is not None and initial.payload.get("backend_contract") != summary["backend_contract"]:
        raise ValueError("Warm-start candidate backend contract mismatch")
    rows = []
    for seed in model_seeds:
        directory = output / "models" / ("seed_" + str(seed))
        directory.mkdir(parents=True, exist_ok=True)
        checkpoint = directory / "checkpoint.json"
        if checkpoint.exists() and not resume:
            raise ValueError("Existing training checkpoint; use --resume or a new run")
        model, history = train_model(train, val, seed=int(seed), epochs=epochs, hidden=hidden,
                                     batch_queries=batch_queries, patience=patience,
                                     learning_rate=learning_rate, checkpoint_path=checkpoint,
                                     resume=resume and checkpoint.is_file(), initial_model=initial,
                                     on_epoch=lambda row: log(json.dumps({"model_seed": seed, **row})))
        prior = initial.payload.get("training_provenance", {}) if initial is not None else {}
        heldout = sorted(val.base_groups | test_base | set(prior.get("heldout_inventory_groups", [])))
        provenance = dict(collection_digest=manifest["digest"], dataset_sha256=manifest["dataset_sha256"],
                          effective_splits=manifest["effective_splits"],
                          source_refs=manifest["source_refs"], heldout_inventory_groups=heldout,
                          planner_config=plain(planner), scope="OFFLINE_SIMULATION",
                          high_level_policy="RULE", high_level_value_provider="proxy", ppo_trained=False,
                          parent_model_sha256=file_digest(initial_model) if initial_model else None)
        # Warm-start ancestry must also remain excluded from later holdout tests.
        model.payload["training"]["train_base_groups"] = sorted(train.base_groups | set(
            initial.payload.get("training", {}).get("train_base_groups", []) if initial else []))
        model.payload["training"]["validation_base_groups"] = sorted(val.base_groups | set(
            initial.payload.get("training", {}).get("validation_base_groups", []) if initial else []))
        model.payload.update(backend_contract=summary["backend_contract"], training_provenance=provenance,
                             rollout_contract={n: getattr(planner, n) for n in ("horizon", "scenario_count", "cvar_alpha")})
        path = directory / "model.json"
        model.save(path)
        atomic_json(directory / "history.json", history)
        metrics = packed_query_report(val, planner, model)
        # Selection uses validation only; test targets are not loaded here.
        objective = metrics["teacher_top1_relevance_regret"] + np.mean(list(metrics["future_output_mse"].values()))
        rows.append(dict(seed=int(seed), model=str(path.resolve()), selected_epoch=history["selected_epoch"],
                         validation_objective=float(objective), validation=metrics))
    selected = min(rows, key=lambda r: (r["validation_objective"], r["seed"]))
    DualHeadRanker.load(selected["model"]).save(output / "selected_model.json")
    result = dict(selection="VALIDATION_ONLY", selected=selected, seeds=rows,
                  default_replaced=False, ros_robot_execution="NOT_RUN")
    atomic_json(output / "model_selection.json", result)
    return result


def _evaluate_task(task):
    mode, sid, seed, variant = task
    ctx = _JOB
    spec = ctx["specs"][sid]
    started = time.perf_counter()
    ranker = None if mode == "dblf" else TeamRuntimeRanker(
        ctx["planner"], seed=seed, mode="ranking" if mode == "learned_ranking" else "ahead",
        model_path=ctx["model"] if mode.startswith("learned") else None,
        use_time_budget=False)
    latencies = []
    if ranker is not None:
        ranker.on_plan = lambda box, state, result: latencies.append(result.diagnostics["planning_time_sec"])
    output, cell, stream_hash = _run_runtime(spec, seed, ranker, variant)
    if ranker is not None:
        output["ranker_provenance"] = ranker.provenance()
    metrics = runtime_metrics(output, cell)
    # Hash the complete outcome without wall-clock values to enable replay checks.
    layouts = output["pallet_list"]
    outcome = digest({"layouts": layouts, "decisions": output["decisions"],
                      "missing": output["missing"], "inspection": output["inspection"]})
    row = dict(mode=mode, scenario_id=sid, seed=seed, variant=variant,
               inventory_group=inventory_group(spec), family=spec.family,
               stream_sha256=stream_hash, outcome_sha256=outcome, **metrics,
               wall_time_sec=time.perf_counter() - started,
               planner_mean_sec=float(np.mean(latencies)) if latencies else 0.,
               planner_p95_sec=float(np.quantile(latencies, .95)) if latencies else 0.,
               scope="PYTHON_RUNTIME_SIMULATION", robot_execution="NOT_RUN")
    row["evaluation_digest"] = ctx["evaluation_digest"]
    row["episode_sha256"] = digest(row)
    key = f"{mode}-{sid}-{seed}-{variant}"
    atomic_json(ctx["evaluation_dir"] / "episodes" / (key + ".json"), row)
    # Per-episode final layouts/events make rejects, L4 and replay debuggable.
    atomic_json(ctx["evaluation_dir"] / "layouts" / (key + ".json"),
                dict(events=output["events"], pallets=layouts, audit=metrics["final_audit_codes"]))
    return row


def evaluate_models(context, output, *, model_path=None, episode_seeds=(101, 102, 103),
                    variants=("clean", "nominal", "noisy3mm"), workers=2,
                    external=False, report_name="runtime_test", log=print):
    output = Path(output).resolve()
    selected = Path(model_path or output / "selected_model.json").resolve()
    model = DualHeadRanker.load(selected)
    provenance = model.payload["training_provenance"]
    dataset = context["dataset"]
    if plain(context["planner"]) != provenance["planner_config"]:
        raise ValueError("Evaluation planner config differs from model training")
    if external:
        specs = list(dataset.scenarios)
    else:
        if dataset_fingerprint(dataset) != provenance["dataset_sha256"]:
            raise ValueError("Dataset changed; use --external-dataset for a fresh held-out dataset")
        ids = set(provenance["effective_splits"]["test"])
        specs = [s for s in dataset.scenarios if s.scenario_id in ids]
    used = set(model.payload["training"]["train_base_groups"]) | set(
        model.payload["training"]["validation_base_groups"])
    if not specs or any(inventory_group(s) in used for s in specs):
        raise ValueError("Evaluation inventory overlaps train/validation or test is empty")
    evaluation_dir = output / report_name
    contract = dict(model_sha256=file_digest(selected), dataset_sha256=dataset_fingerprint(dataset),
                    source_refs=context["source_refs"], episode_seeds=list(episode_seeds),
                    variants=list(variants), candidate=plain(context["cand"]), robot=plain(context["robot"]),
                    virtual=plain(context["virtual"]), runtime=plain(context["runtime"]),
                    highlevel=plain(context["high"]), planner=plain(context["planner"]),
                    spec_mismatch=context["spec_mismatch"], missing=context["missing"],
                    runtime_source_sha256=context["runtime_source_sha256"],
                    planner_source_sha256=source_fingerprint(Path(__file__).parent),
                    scenario_ids=[s.scenario_id for s in specs])
    manifest_path = evaluation_dir / "manifest.json"
    if manifest_path.is_file() and json.loads(manifest_path.read_text()) != contract:
        raise ValueError("Existing evaluation contract differs; choose a new --report-name")
    atomic_json(manifest_path, contract)
    ctx = {**context, "model": str(selected), "evaluation_dir": evaluation_dir,
           "evaluation_digest": digest(contract)}
    modes = ("dblf", "heuristic_rollout", "learned_ranking", "learned_rollout")
    tasks = [(m, s.scenario_id, int(seed), v) for m in modes for s in specs
             for seed in episode_seeds for v in variants]
    rows, pending = [], []
    for task in tasks:
        m, sid, seed, v = task
        path = evaluation_dir / "episodes" / f"{m}-{sid}-{seed}-{v}.json"
        if path.is_file():
            cached = json.loads(path.read_text())
            recorded = cached.pop("episode_sha256", None)
            if cached.get("evaluation_digest") != ctx["evaluation_digest"] or recorded != digest(cached):
                raise ValueError("Cached evaluation integrity/contract mismatch: " + str(path))
            cached["episode_sha256"] = recorded
            rows.append(cached)
        else:
            pending.append(task)
    with ProcessPoolExecutor(max_workers=workers, mp_context=multiprocessing.get_context("fork"),
                             initializer=_init_worker, initargs=(ctx,)) as pool:
        futures = {pool.submit(_evaluate_task, t): t for t in pending}
        for f in as_completed(futures):
            row = f.result()  # partial episode files remain resumable; no success report on error
            rows.append(row)
            log(json.dumps({"evaluated": len(rows), "total": len(tasks), "mode": row["mode"],
                            "scenario": row["scenario_id"], "seed": row["seed"],
                            "variant": row["variant"], "placed": row["placed"], "L4": row["L4"]}))
    rows.sort(key=lambda r: (r["variant"], r["scenario_id"], r["seed"], r["mode"]))
    comparisons = {}
    for variant in variants:
        group = [r for r in rows if r["variant"] == variant]
        comparisons[variant] = {m: paired_comparison(group, m) for m in modes[1:]}
    summary = {}
    for variant in variants:
        summary[variant] = {}
        for mode in modes:
            group = [r for r in rows if r["variant"] == variant and r["mode"] == mode]
            summary[variant][mode] = {metric: float(np.mean([r[metric] for r in group]))
                                     for metric in ("placed", "pallets", "pallet_equivalent", "placed_volume_m3",
                                                    "fill_true_mean", "time_s", "executability_rate",
                                                    "planner_p95_sec", "L4", "final_audit_issues")}
            summary[variant][mode]["episodes"] = len(group)
    result = dict(scope="PYTHON_RUNTIME_SIMULATION", robot_execution="NOT_RUN", ros2="NOT_RUN",
                  physics="NOT_RUN", highlevel_policy="rule", value_provider="proxy", ppo_changed=False,
                  model_sha256=file_digest(selected), contract=contract, episodes=rows,
                  summary=summary, paired=comparisons,
                  pairing_note="Identical source streams and configured seeds; execution RNG draws may diverge after action histories differ.",
                  deployment_note="A passed offline gate still requires ROS2/controller and physical validation.")
    atomic_json(evaluation_dir / "report.json", result)
    return result
