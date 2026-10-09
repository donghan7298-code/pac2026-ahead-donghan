#!/usr/bin/env python3
"""Measure real generator/runtime teacher work, optionally against v2 rollout.

This is a Python runtime benchmark. It does not start ROS or a physical robot.
"""

import argparse
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-root', type=Path, required=True)
    parser.add_argument('--dataset', type=Path, required=True)
    parser.add_argument('--config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--scenario', default='S0001')
    parser.add_argument('--boxes', type=int, default=8)
    parser.add_argument('--legacy-rollout', type=Path)
    args = parser.parse_args()
    own = Path(__file__).resolve().parents[2]
    team = args.team_root.resolve()
    sys.path[:0] = [str(own / 'ros2_ws/src/pac_common'), str(own / 'ros2_ws/src/pac_planning'),
                   *(str(team / 'ros2_ws/src' / pkg) for pkg in
                     ('pac_candidates', 'pac_highlevel', 'pac_robot_check', 'pac_runtime')),
                   str(team / 'tools/virtual_data')]
    import yaml
    from pac_candidates import CandidateBackend, load_candidate_config
    from pac_highlevel import load_highlevel_config, load_policy
    from pac_planning import PlannerConfig
    from pac_planning.team_bridge import plan_with_backend
    from pac_planning.team_training import RuntimeTeacherRanker, inventory_group, rows_from_teacher
    from pac_planning.training_data import atomic_json
    from pac_robot_check import RobotFeasibility, load_robot_check_config
    from pac_runtime import RuntimeLoop, load_runtime_config
    from virtual_data import load_virtual_config
    from virtual_data.highlevel import family_indices
    from virtual_data.runtime_cell import runtime_cell
    from virtual_data.scenario_source import load_dataset
    import pac_planning.planner as planner_module

    cfg = yaml.safe_load(args.config.read_text())
    planning = PlannerConfig(**cfg['planning'])
    dataset = load_dataset(args.dataset)
    original = next(s for s in dataset.scenarios if s.scenario_id == args.scenario)
    spec = replace(original, arrivals=original.arrivals[:args.boxes])
    cand = load_candidate_config(team / 'config/taehyeon/candidates.yaml')
    virtual = load_virtual_config(team / 'config/taehyeon/virtual_data.yaml')
    high = load_highlevel_config(team / 'config/taehyeon/highlevel.yaml')
    rt = replace(load_runtime_config(team / 'config/taehyeon/runtime.yaml'), seed=7)
    robot = RobotFeasibility(load_robot_check_config(team / 'config/taehyeon/robot_check_gazebo.yaml'))
    cell = runtime_cell(spec, dataset, cand, virtual, seed=7,
                        family_index=family_indices(dataset)[spec.scenario_id],
                        scenario_index=dataset.scenarios.index(original),
                        spec_mismatch_probability=0., missing_probability=0.)
    teacher = RuntimeTeacherRanker(planning, spec.scenario_id, inventory_group(spec), seed=7)
    current_eval = planner_module.evaluate_shared
    legacy_eval = None
    if args.legacy_rollout:
        definition = importlib.util.spec_from_file_location('pac_planning._legacy_rollout', args.legacy_rollout)
        module = importlib.util.module_from_spec(definition)
        sys.modules[definition.name] = module
        definition.loader.exec_module(module)
        legacy_eval = module.evaluate_shared
    timings = []

    def rank(valid, box, state, backend):
        before = time.perf_counter()
        ordered = teacher(valid, box, state, backend)
        duration = time.perf_counter() - before
        row = dict(box_id=box.box_id, state_version=state.state_version, candidates=len(valid),
                   teacher_sec=duration)
        if legacy_eval:
            legacy_backend = CandidateBackend(backend.context, backend.config)
            # Reproduce v2's report-allocating callback as well as its rollout.
            legacy_backend.generate_candidates = lambda b, s: list(legacy_backend.generate_with_report(b, s).candidates)
            before = time.perf_counter()
            planner_module.evaluate_shared = legacy_eval
            try:
                old = plan_with_backend(state.inventory.tracked_boxes[box.box_id], state, legacy_backend,
                                        candidates=valid, config=planning, mode='teacher', seed=7,
                                        use_time_budget=False)
            finally:
                planner_module.evaluate_shared = current_eval
            row['legacy_sec'] = time.perf_counter() - before
            if teacher.groups:
                assert teacher.groups[-1]['rows'] == rows_from_teacher(old), 'Teacher labels changed'
            assert [c.candidate_id for c in ordered] == [c.candidate_id for c in old.ranked], 'Ordering changed'
            row['parity'] = True
        timings.append(row)
        print(json.dumps(row), flush=True)
        return ordered

    started = time.perf_counter()
    outcome = RuntimeLoop(cell, cand, high, rt, robot, load_policy('rule', config=high), rank).run()
    report = dict(scope='PYTHON_RUNTIME_SIMULATION', source_scenario=original.scenario_id,
                  source_boxes=len(original.arrivals), executed_boxes=len(spec.arrivals),
                  placed=outcome['placed'], queries=len(teacher.groups),
                  candidate_labels=sum(len(g['rows']) for g in teacher.groups),
                  wall_time_sec=time.perf_counter() - started, timings=timings,
                  robot_execution_verified=False, physics_verified=False)
    atomic_json(args.output, report)
    print(json.dumps({k: v for k, v in report.items() if k != 'timings'}), flush=True)


if __name__ == '__main__':
    main()
