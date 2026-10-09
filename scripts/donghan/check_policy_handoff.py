#!/usr/bin/env python3
"""Check a published stage-4 PPO -> stage-5 handoff; no ROS/robot execution."""
import argparse
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys


def expect_rejection(call, message):
    try:
        call()
    except ValueError as exc:
        if message not in str(exc):
            raise
        return str(exc)
    raise RuntimeError(f"Expected rejection containing {message!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-root", type=Path, required=True)
    parser.add_argument("--team-ref", default="UNVERIFIED_LOCAL")
    parser.add_argument("--policy-kind", choices=("numpy", "sb3"), default="numpy")
    parser.add_argument("--policy-file", type=Path)
    parser.add_argument("--model", type=Path)
    parser.add_argument("--planner-config", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    own = Path(__file__).resolve().parents[2]
    team = args.team_root.resolve()
    sys.path[:0] = [str(own / "ros2_ws/src/pac_common"), str(own / "ros2_ws/src/pac_planning"),
                   str(team / "ros2_ws/src/pac_candidates"), str(team / "ros2_ws/src/pac_highlevel")]
    from pac_common import BoxStatus, InventoryState, plain
    from pac_candidates import CandidateBackend, load_candidate_config
    from pac_highlevel import HighLevelDecider, load_highlevel_config
    from pac_highlevel.actions import to_index
    from pac_highlevel.runtime import load_policy
    from pac_planning.config import PlannerConfig, load_config
    from pac_planning.demo import scene_from_file
    from pac_planning.team_bridge import plan_high_level_decision
    import numpy as np

    high = load_highlevel_config(team / "config/taehyeon/highlevel.yaml")
    cand = load_candidate_config(team / "config/taehyeon/candidates.yaml")
    cfg = load_config(args.planner_config) if args.planner_config else PlannerConfig()
    model_name = "highlevel_ppo.json" if args.policy_kind == "numpy" else "highlevel_sb3.zip"
    policy_path = args.policy_file or team / "ros2_ws/src/pac_highlevel/models" / model_name
    metadata_path = (policy_path if args.policy_kind == "numpy"
                     else policy_path.with_suffix(".contract.json"))
    for path in (policy_path, metadata_path):
        if not path.is_file():
            raise FileNotFoundError(f"Missing published policy artifact: {path}")
    policy = load_policy(args.policy_kind, policy_path, config=high)
    payload = json.loads(metadata_path.read_text())
    report = {"scope": "PYTHON_SNAPSHOT_HANDOFF_ONLY", "team_ref": args.team_ref,
              "python_version": sys.version.split()[0], "numpy_version": np.__version__,
              "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
              "policy_kind": args.policy_kind,
              "policy_metadata_sha256": hashlib.sha256(metadata_path.read_bytes()).hexdigest(),
              "policy_contract": payload["contract"], "cases": [],
              "lowlevel_mode": "ranking", "planner_config": plain(cfg),
              "model_file": str(args.model) if args.model else None,
              "training_contract_equivalence_verified": False,
              "fixture": "scenario_001_basic; buffer capacity explicitly matched to policy",
              "robot_execution": "NOT_RUN", "physical_execution": "NOT_RUN", "ros2": "NOT_RUN"}
    _, box, original_state, context = scene_from_file(own / "test_data/scenario_001_basic.json")
    context = replace(context, buffer_capacity=high.buffer.slots)
    for buffered in (False, True):
        current = replace(box, status=BoxStatus.BUFFERED if buffered else box.status)
        state = replace(original_state, inventory=InventoryState({current.box_id: current}, {}))
        before = plain(state)
        backend = CandidateBackend(context, cand)
        decision = HighLevelDecider(context, cand_config=cand, config=high,
                                   policy=policy).decide(state)
        if not decision.requires_low_level:
            raise RuntimeError(f"Fixture did not reach stage 5: {decision.action}")
        assert decision.mask[to_index(decision.action)]
        if decision.probabilities is not None:
            assert all(p == 0 for p, enabled in zip(decision.probabilities, decision.mask) if not enabled)
        options = dict(config=cfg, model_path=args.model, mode="ranking", use_time_budget=False)
        result = plan_high_level_decision(decision, state, backend, **options)
        assert result.ranked and result.requires_robot_validation
        assert all(c.box_id == current.box_id and c.base_state_version == state.state_version
                   for c in result.ranked)
        assert all(e.features.geometry_source == "EMS_SUPPLIED" for e in result.evaluations)
        stale = expect_rejection(lambda: plan_high_level_decision(
            decision, replace(state, state_version=state.state_version + 1), backend), "STALE_PLAN")
        assert plain(state) == before
        report["cases"].append({"buffered": buffered, "action": decision.action.label(),
                                "box_id": current.box_id, "state_version": state.state_version,
                                "ranked_count": len(result.ranked), "ems_source": "EMS_SUPPLIED",
                                "model_status": result.diagnostics["model_status"],
                                "masked_probabilities": ("PASS" if decision.probabilities is not None
                                                         else "NOT_PROVIDED_BY_POLICY"),
                                "stale_snapshot_rejection": stale, "state_unchanged": True})
    wrong = replace(high, features=replace(high.features, value_provider="donghan"))
    report["value_provider_mismatch_rejection"] = expect_rejection(
        lambda: load_policy(args.policy_kind, policy_path, config=wrong), "value_provider")
    report["status"] = "PASS"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
