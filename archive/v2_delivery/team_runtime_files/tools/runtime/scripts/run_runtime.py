#!/usr/bin/env python3
"""Run the 1 -> 8 runtime loop (pac_runtime) on generator scenarios.

    python tools/runtime/scripts/run_runtime.py --dataset tools/highlevel/output/dataset80 \
        --split test --report docs/taehyeon/reports/runtime_test.json

Variants (paired, same streams and seeds):
  full       stage 6 on (HDR50-22 check inside the placer)
  no_robot   stage 6 off (every hard-mask-valid candidate counts as executable)
  noisy3mm   stage 6 on, placement error 3 mm (outside the 5-2 budget)
  small_gripper  stage 6 on, 0.20 x 0.15 m vacuum pad instead of 0.34 x 0.26 m
"""

import argparse
from collections import Counter
from dataclasses import replace
import json
import multiprocessing as mp
from pathlib import Path
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "highlevel" / "scripts"))
from _common import REPO, add_common_args, load_all  # noqa: E402
import team_paths  # noqa: E402

from pac_common import ValidationResult  # noqa: E402
from pac_highlevel import load_policy  # noqa: E402
from pac_robot_check import RobotFeasibility, load_robot_check_config  # noqa: E402
from pac_runtime import RuntimeLoop, load_runtime_config  # noqa: E402
from virtual_data.highlevel import family_indices, split_ids  # noqa: E402
from virtual_data.runtime_cell import runtime_cell  # noqa: E402


class NoRobot:
    """Stage 6 switched off: accepts everything, fixed cycle time."""

    def __init__(self, cycle_s=8.0):
        self.cycle_s = cycle_s

    def validate_robot_motion(self, box, candidate, state, robot_state=None):
        return ValidationResult(True, (), {"cycle_time_s": self.cycle_s, "gripper_yaw_rad": candidate.target_pose.yaw})

    def first_executable(self, box, ranked, state, robot_state=None):
        return (ranked[0], self.validate_robot_motion(box, ranked[0], state), {}) if ranked else (None, None, {})


JOB = None


def _run(task):
    variant, k, seed = task
    spec, cell_kwargs = JOB["specs"][k]
    cand, hl, rt, rcfg, dataset, vcfg = JOB["cfg"]
    cell = runtime_cell(spec, dataset, cand, vcfg, seed=seed, **cell_kwargs)
    if variant == "small_gripper":
        rcfg = replace(rcfg, gripper=replace(rcfg.gripper, footprint_m=(0.20, 0.15)))
    robot = NoRobot() if variant == "no_robot" else RobotFeasibility(rcfg)
    if variant == "noisy3mm":
        rt = replace(rt, execution=replace(rt.execution, place_xy_noise_std_m=0.003))
    rt = replace(rt, seed=seed)
    ranker = None
    if JOB["ranker"] == "donghan":
        team_paths.add_optional("pac_planning")
        from pac_runtime import donghan_ranker

        ranker = donghan_ranker()
    out = RuntimeLoop(cell, cand, hl, rt, robot, load_policy("rule", config=hl), ranker=ranker,
                      log_events=False).run()
    if ranker is not None:
        out["ranker_provenance"] = ranker.provenance()
    out.pop("events", None)
    for p in out["pallet_list"]:
        p.pop("layout", None)
    return variant, spec.scenario_id, seed, out


def main():
    global JOB
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    parser.add_argument("--split", default="test")
    parser.add_argument("--passes", type=int, default=1)
    parser.add_argument("--variants", nargs="+", default=["full", "no_robot", "noisy3mm", "small_gripper"])
    parser.add_argument("--spec-mismatch", type=float, default=0.02)
    parser.add_argument("--missing", type=float, default=0.02)
    parser.add_argument("--runtime-config", type=Path, default=REPO / "config/taehyeon/runtime.yaml")
    parser.add_argument("--robot-config", type=Path, default=REPO / "config/taehyeon/robot_check.yaml")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--ranker", choices=("dblf", "donghan"), default="dblf",
                        help="5-3~5-6 ranking: DBLF or donghan's planner (slow)")
    parser.add_argument("--scenarios", type=int, default=0, help="first N scenarios (0 = all)")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    dataset, cand, vcfg, hl = load_all(args)
    rt = load_runtime_config(args.runtime_config)
    rcfg = load_robot_check_config(args.robot_config)
    specs = split_ids(dataset, args.split)
    if args.scenarios:
        specs = specs[: args.scenarios]
    fam = family_indices(dataset)
    pos = {s.scenario_id: i for i, s in enumerate(dataset.scenarios)}
    JOB = {"specs": [(s, {"family_index": fam[s.scenario_id], "scenario_index": pos[s.scenario_id],
                          "spec_mismatch_probability": args.spec_mismatch,
                          "missing_probability": args.missing}) for s in specs],
           "cfg": (cand, hl, rt, rcfg, dataset, vcfg), "ranker": args.ranker}
    tasks = [(v, k, p) for v in args.variants for k in range(len(specs)) for p in range(args.passes)]
    with mp.get_context("fork").Pool(args.workers) as pool:
        rows = pool.map(_run, tasks, chunksize=1)
    summary = {}
    for v in args.variants:
        outs = [o for vv, _, _, o in rows if vv == v]
        dec = [o["decisions"] for o in outs]
        summary[v] = {
            "episodes": len(outs),
            "placed_per_episode": statistics.fmean(o["placed"] for o in outs),
            "pallets_per_episode": statistics.fmean(o["pallets"] for o in outs),
            "fill_true_mean": statistics.fmean(o["fill_true_mean"] for o in outs),
            "time_s_per_episode": statistics.fmean(o["time_s"] for o in outs),
            "inspection_per_episode": statistics.fmean(len(o["inspection"]) for o in outs),
            "missing_per_episode": statistics.fmean(sum(o["missing"].values()) for o in outs),
            "levels": {L: sum(d.get(L, 0) for d in dec) for L in ("L0", "L1", "L2", "L3", "L4")},
            "repack": sum(d.get("PARTIAL_REPACK", 0) for d in dec),
            "repack_aborted_stage6": sum(d.get("repack_aborted_stage6", 0) for d in dec),
            "stage6": {k: sum(o["stage6"].get(k, 0) for o in outs)
                       for k in sorted({k for o in outs for k in o["stage6"]})},
            "anomalies": {k: sum(o["anomalies"].get(k, 0) for o in outs)
                          for k in sorted({k for o in outs for k in o["anomalies"]})},
            "inspection_reasons": {r: sum(1 for o in outs for i in o["inspection"] if i["reason"] == r)
                                   for r in sorted({i["reason"] for o in outs for i in o["inspection"]})},
        }
        provenance = [o["ranker_provenance"] for o in outs if "ranker_provenance" in o]
        if provenance:
            geometry = Counter()
            statuses = Counter()
            for item in provenance:
                geometry.update(item["geometry_sources"])
                statuses.update(item["model_statuses"])
            contracts = sorted({item["backend_contract_sha256"] for item in provenance})
            summary[v]["ranker_provenance"] = {
                "ranker": provenance[0]["ranker"],
                "calls": sum(item["calls"] for item in provenance),
                "candidate_evaluations": sum(item["candidate_evaluations"] for item in provenance),
                "geometry_sources": dict(sorted(geometry.items())),
                "ems_verified": all(item["ems_verified"] for item in provenance),
                "model_statuses": dict(sorted(statuses.items())),
                "robot_validation_required_calls": sum(
                    item["robot_validation_required_calls"] for item in provenance
                ),
                "backend_contract_sha256": contracts,
            }
    for v, s in summary.items():
        print(v, json.dumps(s))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps({"split": args.split, "variants": args.variants,
                                           "ranker": args.ranker, "summary": summary,
                                           "spec_mismatch_probability": args.spec_mismatch,
                                           "missing_probability": args.missing,
                                           "episodes": [{"variant": v, "scenario": s, "seed": p, **o}
                                                        for v, s, p, o in rows]}, indent=1, default=float),
                               encoding="utf-8")


if __name__ == "__main__":
    main()
