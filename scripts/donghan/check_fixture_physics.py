#!/usr/bin/env python3
"""Real Bullet settlement/contact-force cross-check of a Python runtime layout.

Boxes are inserted just above planned positions, then integrated under gravity.
There is NO robot arm, pick, MoveIt or ROS in this test. This is not the robot
demo; it checks whether its original-size carton layout survives a rigid drop.
"""
import argparse
import json
import math
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--physics-package', type=Path, required=True,
                        help='shared physics pac_simulation package with ahead_sim (not the AHEAD Gazebo asset package)')
    parser.add_argument('--team-root', type=Path, required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('--runtime-report', type=Path, required=True)
    parser.add_argument('--deck', choices=('solid', 'slatted'), default='solid')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--image', type=Path)
    args = parser.parse_args()
    own, team = Path(__file__).resolve().parents[2], args.team_root.resolve()
    sys.path[:0] = [str(own/'ros2_ws/src'/p) for p in ('pac_common', 'pac_planning', 'pac_execution')]
    sys.path[:0] = [str(team/'ros2_ws/src/pac_candidates'), str(args.physics_package.resolve())]
    import pybullet as p
    from pac_candidates import load_candidate_config
    from pac_execution.audit import check_placement
    from pac_execution.contract import PalletTransform
    from pac_planning.training_data import atomic_json, file_digest
    from pac_simulation.ahead_sim.models import PalletConfig, SimulatorConfig
    from pac_simulation.ahead_sim.simulator import AheadLiveSimulator
    fixture, rt = json.loads(args.fixture.read_text()), json.loads(args.runtime_report.read_text())
    if rt.get('fixture_sha256') != file_digest(args.fixture):
        parser.error('Runtime report was produced using a different or untracked fixture')
    if rt['outcome']['pallets'] != 1:
        parser.error('This bounded settlement test requires one pallet')
    cand = load_candidate_config(team/'config/taehyeon/candidates.yaml')
    if rt.get('audit_candidate_config_sha256') != file_digest(team/'config/taehyeon/candidates.yaml'):
        parser.error('Candidate configuration differs from the runtime report')
    size = fixture['pallet']['size_m']
    config = SimulatorConfig(pallet=PalletConfig(length_m=size[0], width_m=size[1],
                             max_height_m=size[2]+.15, collision_model=args.deck))
    pallet = PalletTransform(tuple(size), (-size[0]/2, -size[1]/2, 0.))
    boxes = {b['box_id']: b for b in fixture['boxes']}
    sim = AheadLiveSimulator(config)
    samples, errors, target_centres = {}, [], {}
    try:
        for row in rt['outcome']['pallet_list'][0]['layout']:
            box, corner = boxes[row['box_id']], row['pose']
            dx, dy, dz = box['size_m']
            if round(corner[3]/(math.pi/2)) % 2:
                dx, dy = dy, dx
            center = [corner[0]+dx/2-size[0]/2, corner[1]+dy/2-size[1]/2, corner[2]+dz/2]
            target_centres[row['box_id']] = center
            sim.place_mapping(dict(id=box['box_id'], size_m=box['size_m'], mass_kg=box['weight_kg'],
                                   max_top_load_n=box['max_top_load_n'], target_position_m=center, yaw_rad=corner[3]))
            # Snapshots must be taken throughout the averaging interval so
            # compression uses real contact forces, not a single final sample.
            for _ in range(60):
                sim.step(8)
                sim.snapshot()
        for _ in range(60):
            sim.step(8)
            snapshot = sim.snapshot()
        previous = {}
        for i, bid in enumerate(target_centres):
            body = sim.world.boxes[bid].body_id
            position, rotation = p.getBasePositionAndOrientation(body, physicsClientId=sim.world.client_id)
            error_m = math.dist(position, target_centres[bid])
            samples[bid] = dict(center=list(position), quaternion=list(rotation), displacement_m=error_m,
                                strength=snapshot['strength']['per_box'][bid])
            if error_m > .01:
                errors.append(dict(box_id=bid, reason='DISPLACEMENT_EXCEEDS_10_MM', actual_m=error_m))
            try:
                measured = pallet.measured_corner(boxes[bid]['size_m'], position, rotation)
                check_placement(dict(state_version=i, box_id=bid, candidate_id='measured-'+str(i)),
                                fixture, pallet, measured, previous, cand)
                previous[bid] = measured
            except Exception as error:
                errors.append(dict(box_id=bid, reason=str(error)))
            if samples[bid]['strength']['status'] == 'OVERLOAD':
                errors.append(dict(box_id=bid, reason='MEASURED_CONTACT_FORCE_EXCEEDS_DECLARED_CAPACITY'))
        if args.image:
            import numpy as np
            from PIL import Image
            view = p.computeViewMatrixFromYawPitchRoll([0., 0., .3], 2.5, 42., -38., 0., 2)
            projection = p.computeProjectionMatrixFOV(52., 4/3, .05, 10.)
            camera = p.getCameraImage(960, 720, view, projection, renderer=p.ER_TINY_RENDERER,
                                     physicsClientId=sim.world.client_id)
            args.image.parent.mkdir(parents=True, exist_ok=True)
            Image.fromarray(np.asarray(camera[2], dtype=np.uint8).reshape((720, 960, 4))[:, :, :3]).save(args.image)
        report = dict(scope='BULLET_RIGID_SETTLEMENT_ONLY', deck=args.deck, boxes=len(samples),
                      status='PASS_SETTLEMENT' if not errors else 'REJECT_SETTLEMENT',
                      fixture_sha256=file_digest(args.fixture), runtime_report_sha256=file_digest(args.runtime_report),
                      simulator_source_sha256={str(f.relative_to(args.physics_package)): file_digest(f)
                          for f in sorted((args.physics_package/'pac_simulation').rglob('*.py'))},
                      simulation_time_s=sim.world.simulation_time_s, errors=errors, measured_boxes=samples,
                      capacity_source='TEAM_MCKEE_ASSUMPTION', robot_execution_verified=False,
                      gazebo_verified=False, physics_engine='pybullet',
                      limitation='Rigid drop and contact forces only; no carton crushing, vacuum or robot motion')
        atomic_json(args.output, report)
        print(json.dumps({k: report[k] for k in ('scope', 'deck', 'boxes', 'status', 'errors')}))
    finally:
        sim.close()
    return 0 if report['status'] == 'PASS_SETTLEMENT' else 2


if __name__ == '__main__':
    sys.exit(main())
