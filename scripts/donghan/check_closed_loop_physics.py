#!/usr/bin/env python3
"""Original observation -> team planner -> Bullet drop -> measured state commit.

Each next placement is replanned from the acknowledged measured state. This
checks an algorithm/rigid-physics loop, never a robot, suction, ROS or Gazebo.
Only PLACE_CURRENT is supported, matching the first physical robot demo.
"""
import argparse
from dataclasses import replace
import json
import math
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-root', type=Path, required=True)
    parser.add_argument('--physics-package', type=Path, required=True)
    parser.add_argument('--demo-dir', type=Path, required=True)
    parser.add_argument('--deck', choices=('solid', 'slatted'), default='solid')
    parser.add_argument('--ranker', choices=('dblf', 'donghan'), default='dblf')
    parser.add_argument('--ranker-config', default='')
    parser.add_argument('--robot-checks', type=int, default=12)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    own, team, physics = Path(__file__).resolve().parents[2], args.team_root.resolve(), args.physics_package.resolve()
    if args.robot_checks < 1:
        parser.error('robot-checks must be positive')
    sys.path[:0] = [str(own/'ros2_ws/src'/p) for p in ('pac_common', 'pac_planning', 'pac_execution')]
    sys.path[:0] = [str(team/'ros2_ws/src'/p) for p in
                   ('pac_candidates', 'pac_highlevel', 'pac_runtime', 'pac_robot_check')]+[str(physics)]
    import pybullet as p
    from pac_candidates import load_candidate_config
    from pac_execution.audit import check_placement
    from pac_execution.contract import ExecutionFault, PalletTransform, validate_command
    from pac_execution.runtime_node import explicit_capacities
    from pac_highlevel import load_highlevel_config, load_policy
    from pac_planning.training_data import atomic_json, file_digest
    from pac_robot_check import RobotFeasibility, load_robot_check_config
    from pac_runtime import load_runtime_config
    from pac_runtime.core import RuntimeCore
    from pac_runtime.order import load_order
    from pac_runtime.ros_node import CoreBridge, make_runtime_ranker, report_from_dict
    from pac_simulation.ahead_sim.models import PalletConfig, SimulatorConfig
    from pac_simulation.ahead_sim.simulator import AheadLiveSimulator

    fixture = json.loads((args.demo_dir/'fixture.json').read_text())
    order = json.loads((args.demo_dir/'order.json').read_text())
    boxes = {b['box_id']: b for b in fixture['boxes']}
    cand = load_candidate_config(args.demo_dir/'candidates_demo.yaml')
    audit = load_candidate_config(team/'config/taehyeon/candidates.yaml')
    high = load_highlevel_config(team/'config/taehyeon/highlevel.yaml')
    if high.features.value_provider != 'proxy' or high.buffer.slots != 4:
        parser.error('The existing proxy + four-slot policy contract is required')
    runtime = replace(load_runtime_config(team/'config/taehyeon/runtime.yaml'),
                      robot_checks_per_option=args.robot_checks)
    cell = explicit_capacities(load_order(args.demo_dir/'order.json', cand), order)
    robot = RobotFeasibility(load_robot_check_config(args.demo_dir/'robot_check_demo.yaml'))
    core = RuntimeCore(cell, cand, high, runtime, robot, load_policy('rule', config=high),
                       make_runtime_ranker(args.ranker, config_path=args.ranker_config))
    bridge = CoreBridge(core)
    size = fixture['pallet']['size_m']
    simcfg = SimulatorConfig(pallet=PalletConfig(length_m=size[0], width_m=size[1],
                             max_height_m=size[2]+.15, collision_model=args.deck))
    # Same nominal 10 mm release gap as the planned robot sequence; no body
    # is moved after its initial insertion at the selected placement.
    simcfg = replace(simcfg, physics=replace(simcfg.physics, spawn_clearance_m=.010))
    pallet = PalletTransform(tuple(size), (-size[0]/2, -size[1]/2, 0.))
    sim = AheadLiveSimulator(simcfg)
    events, committed, errors, snapshot = [], {}, [], None
    try:
        for i, box in enumerate(fixture['boxes']):
            try:
                before = bridge.status()
                if bridge.pending is not None:
                    raise ExecutionFault('Previous physical result is still outstanding')
                _, command = bridge.on_observation(json.dumps(dict(
                    box_id=box['box_id'], label_sku=box['sku'], weight_kg=box['weight_kg'],
                    size_m=box['size_m'], confidence=1., visual_damage=False,
                    stamp=sim.world.simulation_time_s, observation_source='KNOWN_BULLET_BODY')))
                if command is None:
                    raise ExecutionFault('No placement command was produced')
                bid, corner = validate_command(command, boxes, pallet, bridge.status()['state_version'])
                dx, dy, dz = box['size_m']
                if round(corner[3]/(math.pi/2)) % 2:
                    dx, dy = dy, dx
                center = (corner[0]+dx/2-size[0]/2, corner[1]+dy/2-size[1]/2, corner[2]+dz/2)
                sim.place_mapping(dict(id=bid, size_m=box['size_m'], mass_kg=box['weight_kg'],
                    max_top_load_n=box['max_top_load_n'], target_position_m=center, yaw_rad=corner[3]))
                for _ in range(60):
                    sim.step(8)
                    snapshot = sim.snapshot()
                samples = {}
                for old in [*committed, bid]:
                    body = sim.world.boxes[old].body_id
                    position, rotation = p.getBasePositionAndOrientation(body, physicsClientId=sim.world.client_id)
                    linear, angular = p.getBaseVelocity(body, physicsClientId=sim.world.client_id)
                    if math.dist(linear, (0., 0., 0.)) > .005 or math.dist(angular, (0., 0., 0.)) > .01:
                        raise ExecutionFault('A physical carton has not settled: '+old)
                    samples[old] = dict(raw_center=list(position), raw_quaternion=list(rotation),
                        measured_corner=pallet.measured_corner(boxes[old]['size_m'], position, rotation),
                        strength=snapshot['strength']['per_box'][old])
                    if samples[old]['strength']['status'] == 'OVERLOAD':
                        raise ExecutionFault('Measured contact force exceeds the declared capacity: '+old)
                previous = {old: samples[old]['measured_corner'] for old in committed}
                if any(math.dist(previous[old][:3], committed[old][:3]) > .001 for old in committed):
                    raise ExecutionFault('A previously committed carton moved beyond the 1 mm recovery threshold')
                measured = samples[bid]['measured_corner']
                check_placement(command, fixture, pallet, measured, previous, audit)
                result = dict(state_version=command['state_version'], box_id=bid,
                    candidate_id=command['candidate_id'], ok=True, attempts=1, measured_pose=list(measured),
                    measured_frame='pallet', measurement_source='BULLET_WORLD_POSE')
                pending = bridge.pending
                if pending.state_version != command['state_version'] or pending.box_id != bid:
                    raise ExecutionFault('Measured result does not match the outstanding snapshot')
                level, _, _, issues = core.verify(core.sm.tracked[bid], pending.candidate,
                                                   report_from_dict(result).measured_pose)
                if level == 'L4':
                    raise ExecutionFault('Runtime rejected the measured placement: '+','.join(issues))
                level, next_command = bridge.on_result(json.dumps(result))
                after = bridge.status()
                if (next_command is not None or after['placed'] != i+1
                        or after['state_version'] <= command['state_version'] or after['buffer']):
                    raise ExecutionFault('The runtime did not acknowledge exactly one measured placement')
                committed[bid] = measured
                pb = next(b for b in core.sm.placed if b.box_id == bid)
                events.append(dict(box_id=bid, before_status=before, command=command,
                    measured_result=result, measured_body=samples[bid], runtime_level=level,
                    runtime_state_pose=[pb.pose.x, pb.pose.y, pb.pose.z, pb.pose.yaw], after_status=after))
            except Exception as error:
                errors.append(dict(box_id=box['box_id'], reason=str(error),
                                   outstanding_action=getattr(bridge.pending, 'action', None)))
                break  # Preserve the first real failure; never fake its commit.
        report = dict(scope='BULLET_MEASURED_RUNTIME_LOOP', status='PASS_MEASURED_LOOP' if not errors else 'BLOCKED',
            deck=args.deck, ranker=args.ranker, original_boxes=len(boxes), measured_and_committed=len(events),
            fixture_sha256=file_digest(args.demo_dir/'fixture.json'),
            source_ground_truth_sha256=fixture['source_ground_truth_sha256'],
            candidate_config_sha256=file_digest(args.demo_dir/'candidates_demo.yaml'),
            audit_candidate_config_sha256=file_digest(team/'config/taehyeon/candidates.yaml'),
            robot_config_sha256=file_digest(args.demo_dir/'robot_check_demo.yaml'),
            simulator_source_sha256={str(f.relative_to(physics)): file_digest(f)
                                    for f in sorted((physics/'pac_simulation').rglob('*.py'))},
            release_gap_m=.010, simulation_time_s=sim.world.simulation_time_s,
            final_runtime_status=bridge.status(), errors=errors, events=events,
            physics_engine='pybullet', robot_execution_verified=False, gazebo_verified=False,
            ppo_trained=False, capacity_source='TEAM_MCKEE_ASSUMPTION',
            limitation='Original rigid bodies are dropped at planned positions; no pick, robot or suction simulation')
        atomic_json(args.output, report)
        print(json.dumps({k: report[k] for k in
            ('scope', 'deck', 'ranker', 'original_boxes', 'measured_and_committed', 'status', 'errors')}))
    finally:
        sim.close()
    return 0 if report['status'] == 'PASS_MEASURED_LOOP' else 2


if __name__ == '__main__':
    sys.exit(main())
