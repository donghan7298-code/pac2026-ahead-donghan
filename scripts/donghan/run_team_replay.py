#!/usr/bin/env python3
"""OFFLINE generator -> high-level Rule -> real candidate backend -> planner.

No ROS, robot, or physical execution is implied by a successful replay.
Ground truth arrival order stays inside the simulator's source loader.
"""
import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-root", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--scenario", default="S0001")
    parser.add_argument("--max-boxes", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("runs/team_replay"))
    args = parser.parse_args()
    if args.max_boxes < 1: parser.error("max-boxes must be positive")
    own = Path(__file__).resolve().parents[2]
    team = args.team_root.resolve()
    sys.path[:0] = [str(own/"ros2_ws/src/pac_common"), str(own/"ros2_ws/src/pac_planning"),
                   str(team/"ros2_ws/src/pac_candidates"), str(team/"ros2_ws/src/pac_highlevel"),
                   str(team/"tools/virtual_data")]
    from pac_common import plain
    from pac_candidates import load_candidate_config
    from pac_highlevel import RulePolicy, load_highlevel_config, run_policy
    from pac_planning.team_bridge import TeamPlacer
    from virtual_data import load_virtual_config
    from virtual_data.scenario_source import load_dataset
    from virtual_data.highlevel import world_from_spec

    dataset = load_dataset(args.dataset)
    spec = next((s for s in dataset.scenarios if s.scenario_id == args.scenario), None)
    if spec is None: parser.error("Unknown scenario")
    spec = replace(spec, arrivals=spec.arrivals[:args.max_boxes])
    cand = load_candidate_config(team/"config/taehyeon/candidates.yaml")
    virtual = load_virtual_config(team/"config/taehyeon/virtual_data.yaml")
    high = load_highlevel_config(team/"config/taehyeon/highlevel.yaml")
    if high.features.value_provider != "proxy":
        parser.error("This baseline preserves proxy; configure a trained value provider separately")
    args.output.mkdir(parents=True, exist_ok=True)
    with (args.output/"plans.jsonl").open("w", encoding="utf-8") as logs:
        def record(box, state, result):
            logs.write(json.dumps({"scope":"SIMULATED", "box_id":box.box_id,
                                   "state_version":state.state_version, "result":plain(result)},
                                  ensure_ascii=False, allow_nan=False)+"\n")
        placer = TeamPlacer(use_time_budget=False, on_plan=record)
        world = world_from_spec(spec,dataset,cand,virtual,high,placer=placer)
        summary = run_policy(world, RulePolicy(high))
    summary.update(scope="OFFLINE_SIMULATION", robot_execution="NOT_RUN",
                   scenario_id=spec.scenario_id, placer=placer.name, planner_calls=placer.calls,
                   policy="RULE", value_provider="proxy")
    (args.output/"summary.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False,allow_nan=False)+"\n")
    print(json.dumps(summary,ensure_ascii=False,allow_nan=False))


if __name__ == "__main__":
    main()
