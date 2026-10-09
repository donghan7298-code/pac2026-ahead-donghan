#!/usr/bin/env python3
"""Make a bounded physical-demo fixture from original generated boxes.

No model trains here. Exact future order stays with the source fixture, while
the runtime order file contains only SKU counts/ranges and declared capacities.
"""

import argparse
from collections import Counter
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-root', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--scenario', default='S0001')
    parser.add_argument('--boxes', type=int, default=10)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if not 1 <= args.boxes <= 10:
        parser.error('The first physical acceptance fixture supports 1 to 10 boxes')
    own, team = Path(__file__).resolve().parents[2], args.team_root.resolve()
    sys.path[:0] = [str(own/'ros2_ws/src/pac_common'), str(own/'ros2_ws/src/pac_planning'),
                   *(str(team/'ros2_ws/src'/p) for p in ('pac_candidates', 'pac_highlevel', 'pac_runtime', 'pac_robot_check')),
                   str(team/'tools/virtual_data')]
    import yaml
    from pac_candidates import load_candidate_config
    from pac_planning.training_data import atomic_json, file_digest
    from virtual_data import load_virtual_config
    from virtual_data.scenario_source import build_catalog, load_dataset
    dataset = load_dataset(args.dataset.resolve())
    spec = next(s for s in dataset.scenarios if s.scenario_id == args.scenario)
    arrivals = spec.arrivals[:args.boxes]
    cand = load_candidate_config(team/'config/taehyeon/candidates.yaml')
    virtual = load_virtual_config(team/'config/taehyeon/virtual_data.yaml')
    catalog = build_catalog(dataset.sku_ranges, virtual, cand.constraints.load_model)
    # The generator has no measured carton strength field. Reuse and explicitly
    # mark the team's current McKee assumption; do not invent an actual rating.
    boxes = [dict(box_id=b.box_id, sku=b.sku_id, size_m=[b.size.x, b.size.y, b.size.z],
                  weight_kg=b.weight_kg, max_top_load_n=catalog[b.sku_id].top_load_capacity_n,
                  capacity_source='TEAM_MCKEE_ASSUMPTION') for b in arrivals]
    if any(b['weight_kg']+15. > 50. for b in boxes):
        parser.error('A source box plus assumed EOAT exceeds 50 kg')
    fixture = dict(schema_version=1, source_scenario=spec.scenario_id,
                   source_ground_truth_sha256=file_digest(dataset.root/'ground_truth'/f'{spec.scenario_id}.json'),
                   simulation_physics='RIGID_BOXES', observation_source='KNOWN_GAZEBO_RIGID_BODY',
                   eoat_mass_assumption_kg=15., vision_verified=False,
                   base_position_world_m=[1.35, .15, .5], pick_surface_world_m=[-.2, 1.2, .85],
                   pallet=dict(size_m=[1.2, 1.0, 1.35], origin_world_m=[.75, -1.5, .15], max_load_kg=1000.),
                   boxes=boxes)
    counts = Counter(b['sku'] for b in boxes)
    order = dict(pallet=dict(size_m=fixture['pallet']['size_m'], max_load_kg=1000., id_prefix='DEMO'), skus={})
    for sku, count in sorted(counts.items()):
        rows = [b for b in boxes if b['sku'] == sku]
        order['skus'][sku] = dict(size_m=rows[0]['size_m'],
                                 weight_kg=[min(b['weight_kg'] for b in rows), max(b['weight_kg'] for b in rows)],
                                 yaws_rad=[0., 1.5707963267948966], count=count,
                                 max_top_load_n=rows[0]['max_top_load_n'])
    args.output.mkdir(parents=True, exist_ok=True)
    for name, value in (('fixture.json', fixture), ('order.json', order)):
        target = args.output/name
        if target.exists() and json.loads(target.read_text()) != value:
            parser.error('Existing physical fixture differs; choose another output directory')
        atomic_json(target, value)
    robot = yaml.safe_load((team/'config/taehyeon/robot_check_gazebo.yaml').read_text())
    robot['robot_check']['gripper']['tcp_offset_m'] = .1525  # real cup faces in the reviewed EOAT xacro
    robot['robot_check']['cell']['pick_point_base_m'] = [-1.55, 1.05, .35]  # fully supported belt input at x=-.2
    robot_path = args.output/'robot_check_demo.yaml'
    if robot_path.exists() and yaml.safe_load(robot_path.read_text()) != robot:
        parser.error('Existing robot configuration differs; choose another output directory')
    robot_path.write_text(yaml.safe_dump(robot, sort_keys=False))
    plan_candidate = yaml.safe_load((team/'config/taehyeon/candidates.yaml').read_text())
    # Add 1 mm reserve at planning time; retain the original hard mask for
    # the measured audit. This does not modify training or team/PPO configs.
    plan_candidate['candidates']['uncertainty']['size_tolerance_m'] += .001
    candidate_path = args.output/'candidates_demo.yaml'
    if candidate_path.exists() and yaml.safe_load(candidate_path.read_text()) != plan_candidate:
        parser.error('Existing candidate configuration differs; choose another output directory')
    candidate_path.write_text(yaml.safe_dump(plan_candidate, sort_keys=False))
    print(json.dumps({'fixture': str(args.output/'fixture.json'), 'original_boxes': len(boxes),
                      'capacity_source': 'TEAM_MCKEE_ASSUMPTION', 'robot_execution_verified': False}))


if __name__ == '__main__':
    main()
