#!/usr/bin/env python3
"""Check the bounded original-box fixture in the real team Python runtime.

This is an algorithm/analytical-reachability check. Execution uses ExecutorSim,
so success here is not a ROS, MoveIt, grasp or physics acceptance result.
"""

import argparse
from dataclasses import replace
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-root', type=Path, required=True)
    parser.add_argument('--demo-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ranker', choices=('dblf', 'donghan'), default='dblf')
    parser.add_argument('--ranker-config', default='')
    parser.add_argument('--robot-checks', type=int, default=12)
    args = parser.parse_args()
    own, team = Path(__file__).resolve().parents[2], args.team_root.resolve()
    sys.path[:0] = [str(own/'ros2_ws/src'/p) for p in ('pac_common', 'pac_planning', 'pac_execution')]
    sys.path[:0] = [str(team/'ros2_ws/src'/p) for p in
                   ('pac_candidates', 'pac_highlevel', 'pac_runtime', 'pac_robot_check')]
    from pac_common import BoxState, BoxStatus, Pose3D, Size3D
    from pac_candidates import load_candidate_config
    from pac_execution.audit import check_placement
    from pac_execution.contract import PalletTransform
    from pac_execution.runtime_node import explicit_capacities
    from pac_highlevel import load_highlevel_config, load_policy
    from pac_planning.training_data import atomic_json, file_digest
    from pac_robot_check import RobotFeasibility, load_robot_check_config
    from pac_runtime import RuntimeLoop, load_runtime_config
    from pac_runtime.order import load_order
    from pac_runtime.perception import FieldBox
    from pac_runtime.ros_node import make_runtime_ranker
    fixture = json.loads((args.demo_dir/'fixture.json').read_text())
    order = json.loads((args.demo_dir/'order.json').read_text())
    audit_path = team/'config/taehyeon/candidates.yaml'
    candidate_path = args.demo_dir/'candidates_demo.yaml'
    cand = load_candidate_config(candidate_path if candidate_path.is_file() else audit_path)
    audit_cand = load_candidate_config(audit_path)
    high = load_highlevel_config(team/'config/taehyeon/highlevel.yaml')
    rt = load_runtime_config(team/'config/taehyeon/runtime.yaml')
    if args.robot_checks < 1:
        parser.error('robot-checks must be positive')
    rt = replace(rt, robot_checks_per_option=args.robot_checks,
                 perception=replace(rt.perception, size_noise_std_m=0., weight_noise_std_ratio=0.,
                                       uncertain_probability=0., label_fail_probability=0.),
                 execution=replace(rt.execution, place_xy_noise_std_m=0., grip_fail_probability=0.))
    rows = fixture['boxes']
    stream = tuple(FieldBox(BoxState(b['box_id'], b['sku'], Size3D(*b['size_m']), b['weight_kg'],
                                     Pose3D('conveyor', 0., 0., 0.), (0., 1.5707963267948966),
                                     BoxStatus.MEASURED, 1., 0., 'KNOWN_FIXTURE'),
                            capacity_n=b['max_top_load_n']) for b in rows)
    cell = replace(explicit_capacities(load_order(args.demo_dir/'order.json', cand), order), stream=stream)
    robot = RobotFeasibility(load_robot_check_config(args.demo_dir/'robot_check_demo.yaml'))
    ranker = make_runtime_ranker(args.ranker, config_path=args.ranker_config)
    outcome = RuntimeLoop(cell, cand, high, rt, robot, load_policy('rule', config=high), ranker).run()
    p = fixture['pallet']
    pallet = PalletTransform(tuple(p['size_m']), tuple(p['origin_world_m']))
    rejected = []
    for layout in outcome['pallet_list']:
        previous = {}
        for i, placement in enumerate(layout['layout']):
            command = dict(state_version=i, box_id=placement['box_id'], candidate_id='audit-'+str(i))
            try:
                check_placement(command, fixture, pallet, placement['pose'], previous, audit_cand)
            except Exception as error:
                rejected.append(dict(box_id=placement['box_id'], reason=str(error)))
            previous[placement['box_id']] = placement['pose']
    supported = all(e.get('action', 'PLACE_CURRENT') == 'PLACE_CURRENT' for e in outcome['events']
                    if e['event'] == 'PLACE') and not any(
        e['event'] in ('BUFFER', 'CLOSE', 'REPACK_MOVE', 'L3_GRIP_FAIL') for e in outcome['events'])
    gate = (outcome['placed'] == len(rows) and outcome['pallets'] == 1 and supported
            and not rejected and not any(e.get('level') == 'L4' for e in outcome['events']))
    report = dict(scope='PYTHON_RUNTIME_SIMULATION', original_boxes=len(rows),
                  fixture_sha256=file_digest(args.demo_dir/'fixture.json'),
                  robot_config_sha256=file_digest(args.demo_dir/'robot_check_demo.yaml'),
                  candidate_config_sha256=file_digest(candidate_path if candidate_path.is_file() else audit_path),
                  audit_candidate_config_sha256=file_digest(audit_path),
                  ranker=args.ranker, robot_checks_per_option=args.robot_checks,
                  source_ground_truth_sha256=fixture['source_ground_truth_sha256'],
                  analytical_fixture_gate='PASS' if gate else 'BLOCKED',
                  unsupported_cell_actions=not supported, hard_mask_rejections=rejected,
                  robot_execution_verified=False, physics_verified=False, outcome=outcome)
    atomic_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'outcome'}))
    if not gate:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
