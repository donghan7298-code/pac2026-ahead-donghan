"""Generator -> team's measured scenes -> EMS teacher -> low-level model.

Ground-truth arrival order is consumed only by the team's simulated world.
The planner receives its public snapshot/context, never that order list.
This does not train or modify the stage-4 PPO.
"""

from collections import Counter, defaultdict
import hashlib
import json
import random
import time

import numpy as np
from pac_common import FeatureVector, plain

from .features import FEATURE_NAMES
from .model import OUTPUT_NAMES
from .planner import ZERO_FUTURE
from .scoring import priority, score_terms
from .team_bridge import TeamPlacer, backend_contract, plan_with_backend


def digest(value):
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode()).hexdigest()


def measurement_xy_assessment(candidate_config, virtual_config):
    """Check declared measurement error against the mask's XY size margin.

    The virtual observer rounds to 0.1 mm. With centre-based placement, an
    underestimated dimension moves each true edge by half that error. This
    is a bound diagnostic, not a physical safety certificate or auto tuning.
    """
    obs, unc = virtual_config.observation, candidate_config.uncertainty
    normal = (0.0 if obs.dimension_noise_std_m <= 0 else
              obs.dimension_noise_clip_m + 0.00005 if obs.dimension_noise_clip_m > 0 else None)
    uncertain = (3.0 * obs.uncertain_dimension_noise_std_m + 0.00005
                 if obs.uncertain_dimension_noise_std_m > 0 else 0.0)
    cases = []
    for name, bound, margin, enabled in (
        ("normal", normal, unc.size_tolerance_m, True),
        ("uncertain", uncertain, unc.size_tolerance_m * unc.uncertain_multiplier,
         obs.uncertain_probability > 0 and unc.uncertain_policy != "reject"),
    ):
        if enabled:
            cases.append({"box_class": name, "max_dimension_error_m": bound,
                          "required_edge_margin_m": bound / 2 if bound is not None else None,
                          "configured_edge_margin_m": margin,
                          "bound_covered": bound is not None and margin + 1e-12 >= bound / 2})
    return {"scope": "DECLARED_XY_ERROR_BOUND_ONLY", "cases": cases,
            "status": "COVERED" if all(c["bound_covered"] for c in cases) else "XY_MARGIN_NOT_COVERED",
            "physical_safety_verified": False}


def inventory_group(spec):
    """Group by SKU/geometry counts, ignoring order, weight draws and noise."""
    counts = Counter((b.sku_id, b.size.x, b.size.y, b.size.z) for b in spec.arrivals)
    return digest(sorted((*key, n) for key, n in counts.items()))


def training_splits(dataset, seed=7):
    """Preserve generator splits unless identical inventories cross splits.

    If they do, repartition whole inventory groups locally. The generator's
    files are never changed; the effective split is saved with the model.
    """
    grouped = defaultdict(list)
    specs = {s.scenario_id: s for s in dataset.scenarios}
    for spec in dataset.scenarios:
        grouped[inventory_group(spec)].append(spec.scenario_id)
    splits = {name: list(dataset.splits.get(name, ())) for name in ("train", "val", "test")}
    ids = [sid for values in splits.values() for sid in values]
    if len(ids) != len(set(ids)) or set(ids) != set(specs):
        raise ValueError("Generator splits must contain every scenario exactly once")
    if not all(splits.values()):
        raise ValueError("Need nonempty train/val/test scenario splits")
    owners = defaultdict(set)
    for name, values in splits.items():
        for sid in values:
            owners[inventory_group(specs[sid])].add(name)
    collisions = [key for key, values in owners.items() if len(values) > 1]
    if collisions:
        keys = sorted(grouped)
        if len(keys) < 3:
            raise ValueError("Need at least three distinct inventory groups; generate more scenarios")
        random.Random(seed).shuffle(keys)
        val_n = max(1, int(len(keys) * 0.15))
        test_n = max(1, int(len(keys) * 0.15))
        cuts = (len(keys) - val_n - test_n, len(keys) - test_n)
        assigned = (keys[:cuts[0]], keys[cuts[0]:cuts[1]], keys[cuts[1]:])
        splits = {name: sorted(sid for key in keys_ for sid in grouped[key])
                  for name, keys_ in zip(("train", "val", "test"), assigned)}
    return splits, {"policy": "generator_split_with_inventory_leakage_guard",
                    "repartitioned": bool(collisions), "colliding_inventory_groups": len(collisions)}


def rows_from_teacher(result):
    """Rank labels preserve the planner's safety-first ordering, including ties."""
    keys = sorted(set(priority(e.features, e.candidate.score) for e in result.evaluations))
    relevance = {key: i / max(1, len(keys) - 1) for i, key in enumerate(keys)}
    return [{
        "candidate_id": e.candidate.candidate_id,
        "features": list(e.features.values),
        "static": plain(e.features.metrics),
        "geometry_source": e.features.geometry_source,
        "future": {name: getattr(e.future, name) for name in OUTPUT_NAMES},
        "teacher_score": relevance[priority(e.features, e.candidate.score)],
        "placement_score": e.candidate.score,
    } for e in result.evaluations]


class TeacherPlacer:
    """Labels every valid candidate in states visited by the rule/teacher loop."""

    wants_context = True

    def __init__(self, config, scenario_id, base_group, *, seed=7):
        self.config = config
        self.scenario_id = scenario_id
        self.base_group = base_group
        self.seed = seed
        self.groups = []
        self.cache = {}
        self.contract = None
        self.rejected = Counter()
        self.empty_queries = 0

    def __call__(self, valid, box, state, backend):
        box = state.inventory.tracked_boxes[box.box_id]
        key = digest({"box": plain(box), "state": plain(state),
                      "context": plain(backend.context),
                      "candidates": plain(tuple(valid))})
        contract = backend_contract(backend)
        if self.contract is not None and self.contract != contract:
            raise ValueError("Candidate backend changed while collecting labels")
        self.contract = contract
        if key in self.cache:
            return self.cache[key]
        result = plan_with_backend(box, state, backend, candidates=valid, config=self.config,
                                   mode="teacher", seed=self.seed, use_time_budget=False)
        for verdict in result.rejected.values():
            self.rejected.update(str(code.value) for code in verdict.codes)
        if result.evaluations:
            rows = rows_from_teacher(result)
            if any(row["geometry_source"] != "EMS_SUPPLIED" for row in rows):
                raise ValueError("Training requires actual EMS evidence")
            if result.diagnostics["completed_scenarios"] != self.config.scenario_count:
                raise ValueError("Incomplete teacher labels")
            self.groups.append({"base_group": self.base_group, "group_id": key,
                                "scenario_id": self.scenario_id, "box_id": box.box_id,
                                "state_version": state.state_version, "rows": rows})
        else:
            self.empty_queries += 1
        chosen = result.ranked[0] if result.ranked else None
        self.cache[key] = chosen
        return chosen


def run_scenario(spec, dataset, cand, virtual, high, placer, *, episode_seed=0, pallet_xy=None):
    from pac_highlevel import RulePolicy, run_policy
    from virtual_data.highlevel import family_indices, world_from_spec

    if high.features.value_provider != "proxy":
        raise ValueError("This low-level experiment keeps the high-level proxy contract")
    position = next(i for i, s in enumerate(dataset.scenarios) if s.scenario_id == spec.scenario_id)
    started = time.perf_counter()
    world = world_from_spec(spec, dataset, cand, virtual, high, placer=placer,
                            family_index=family_indices(dataset)[spec.scenario_id],
                            scenario_index=position, episode_seed=episode_seed, pallet_xy=pallet_xy)
    summary = run_policy(world, RulePolicy(high))
    summary.update(scenario_id=spec.scenario_id, wall_time_sec=time.perf_counter() - started,
                   scope="OFFLINE_SIMULATION", robot_execution="NOT_RUN",
                   time_s_is_assumed_cost=True)
    return summary


class RuntimeTeacherRanker:
    """Collect EMS labels from states visited by the SAME measured RuntimeCore.

    Geometry/future labels stay in Donghan's scope. Stage 6 still filters the
    full ordered list. Training can alternate teacher and DBLF behaviour to
    cover more states; labels always come from the deterministic full teacher.
    """

    def __init__(self, config, scenario_id, base_group, *, seed=7, behaviour="teacher"):
        if behaviour not in ("teacher", "dblf"):
            raise ValueError("Unknown teacher collection behaviour")
        self.config, self.scenario_id, self.base_group = config, scenario_id, base_group
        self.seed, self.behaviour = seed, behaviour
        self.groups, self.cache = [], {}
        self.contract = None
        self.rejected, self.empty_queries = Counter(), 0

    def __call__(self, valid, box, state, backend):
        from pac_runtime.placer import dblf_order

        box = state.inventory.tracked_boxes[box.box_id]
        key = digest({"box": plain(box), "state": plain(state),
                      "context": plain(backend.context), "candidates": plain(tuple(valid))})
        contract = backend_contract(backend)
        if self.contract is not None and self.contract != contract:
            raise ValueError("Candidate backend changed while collecting labels")
        self.contract = contract
        if key in self.cache:
            return self.cache[key]
        result = plan_with_backend(box, state, backend, candidates=valid, config=self.config,
                                   mode="teacher", seed=self.seed, use_time_budget=False)
        for verdict in result.rejected.values():
            self.rejected.update(str(c.value) for c in verdict.codes)
        if result.evaluations:
            rows = rows_from_teacher(result)
            if (any(r["geometry_source"] != "EMS_SUPPLIED" for r in rows)
                    or result.diagnostics["completed_scenarios"] != self.config.scenario_count):
                raise ValueError("Runtime teacher requires complete rollout and real EMS evidence")
            self.groups.append(dict(base_group=self.base_group, group_id=key,
                                    scenario_id=self.scenario_id, box_id=box.box_id,
                                    state_version=state.state_version, rows=rows))
        else:
            self.empty_queries += 1
        if self.behaviour == "dblf":
            # Excluded feature-invalid candidates are never revived.
            ordered = dblf_order(list(result.ranked))
        else:
            ordered = list(result.ranked)
        self.cache[key] = ordered
        return ordered


def collect_teacher(dataset, splits, cand, virtual, high, config, *, seed=7, log=print):
    groups = {}
    summaries = []
    contract = None
    specs = {s.scenario_id: s for s in dataset.scenarios}
    for split, ids in splits.items():
        groups[split] = []
        for sid in ids:
            spec = specs[sid]
            placer = TeacherPlacer(config, sid, inventory_group(spec), seed=seed)
            summary = run_scenario(spec, dataset, cand, virtual, high, placer)
            if contract is not None and placer.contract is not None and contract != placer.contract:
                raise ValueError("Candidate source/config differs between scenarios")
            contract = placer.contract or contract
            groups[split].extend(placer.groups)
            summary.update(split=split, queries=len(placer.groups),
                           empty_queries=placer.empty_queries, feature_rejects=dict(placer.rejected))
            summaries.append(summary)
            log(json.dumps({"split": split, "scenario": sid, "queries": len(placer.groups),
                            "placed": summary["placed"], "boxes": summary["boxes"]}))
        if not groups[split]:
            raise ValueError(f"No teacher queries for {split}; inspect rejects/data")
    return groups, summaries, contract


def query_report(groups, config, model=None):
    """Safety-aware Top-K recall and future MSE on frozen teacher queries."""
    hits, regrets, mse = [], [], []
    for group in groups:
        rows = group["rows"]
        vectors = [FeatureVector(FEATURE_NAMES, tuple(r["features"]), r["static"],
                                 "EMS_SUPPLIED", "OFFLINE") for r in rows]
        predictions = (model.predict(vectors) if model else
                       [(sum(score_terms(v, ZERO_FUTURE, config).values()), ZERO_FUTURE) for v in vectors])
        order = sorted(range(len(rows)), key=lambda i: (
            *(-x for x in priority(vectors[i], predictions[i][0])), rows[i]["candidate_id"]))
        truth = [r["teacher_score"] for r in rows]
        best = max(truth)
        hits.append(any(truth[i] >= best - 1e-8 for i in order[:config.top_k]))
        regrets.append(best - truth[order[0]])
        mse.extend([[float((getattr(value, name) - row["future"][name]) ** 2)
                     for name in OUTPUT_NAMES] for (_, value), row in zip(predictions, rows)])
    return {"queries": len(groups), "candidates": sum(len(g["rows"]) for g in groups),
            "teacher_best_top_k_recall": float(np.mean(hits)),
            "teacher_top1_relevance_regret": float(np.mean(regrets)),
            "future_output_mse": dict(zip(OUTPUT_NAMES, map(float, np.mean(mse, axis=0))))}


def benchmark_holdout(dataset, test_ids, cand, virtual, high, config, model_path, *, seed=7, log=print):
    results = []
    specs = {s.scenario_id: s for s in dataset.scenarios}
    for sid in test_ids:
        for label, mode, path in (("heuristic_rollout", "ahead", None),
                                  ("learned_ranking", "ranking", model_path),
                                  ("learned_rollout", "ahead", model_path)):
            latencies, statuses = [], Counter()
            def record(box, state, result):
                latencies.append(result.diagnostics["planning_time_sec"])
                statuses.update([result.diagnostics.get("model_status", "EMPTY")])
            placer = TeamPlacer(config, seed=seed, use_time_budget=False, mode=mode,
                                model_path=path, on_plan=record)
            summary = run_scenario(specs[sid], dataset, cand, virtual, high, placer)
            summary.update(mode=label, planner_calls=placer.calls, model_statuses=dict(statuses),
                           planner_mean_sec=float(np.mean(latencies)) if latencies else None,
                           planner_max_sec=max(latencies, default=None))
            results.append(summary)
            log(json.dumps({"test": sid, "mode": label, "placed": summary["placed"],
                            "ng": summary["ng"], "safety_issues": summary["safety_issues"]}))
    return results
