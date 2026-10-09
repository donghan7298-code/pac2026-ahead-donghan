"""Python execution-contract tests; these do not execute a robot or physics."""
from dataclasses import replace
import math
from pathlib import Path
from types import SimpleNamespace as NS
import xml.etree.ElementTree as ET

import pytest

from pac_execution.assets import assemble_urdf, assemble_srdf, world_obstacles
from pac_execution.contract import (ExecutionFault, PalletTransform, guard_runtime_result,
                                     measured_report, validate_command)
from pac_execution.node_io import PoseSample, WorldPoses, settled_sample

FIXTURES = Path(__file__).parent/'fixtures/execution'


def test_physical_corner_tcp_round_trip_at_rotated_pallet():
    pallet = PalletTransform((1.2, 1., 1.35), (.75, -1.5, .15), math.pi/2)
    size = (.4, .3, .2)
    corner = (.1, .2, .4, math.pi/2)
    center = pallet.to_world((.1+.3/2, .2+.4/2, .4+.2/2))
    measured = pallet.measured_corner(size, center, (0., 0., 1., 0.))
    assert measured == pytest.approx(corner)
    position, down = pallet.tcp(size, corner, .003)
    assert position == pytest.approx(pallet.to_world((.25, .4, .603)))
    assert down == pytest.approx((0., 1., 0., 0.))
    with pytest.raises(ExecutionFault, match='aligned'):
        pallet.measured_corner(size, center, (0., 0., math.sin(1.59), math.cos(1.59)))


def command_fixture():
    boxes = {'BOX1': {'size_m': [.4, .3, .2]}}
    command = dict(action='PLACE_CURRENT', box_id='BOX1', state_version=4, candidate_id='c4',
                   target_min_corner=[.1, .2, 0., 0.])
    return boxes, command, PalletTransform((1.2, 1., 1.35), (.75, -1.5, .15))


def test_measured_floor_projection_is_bounded_and_preserves_xy():
    _, _, pallet = command_fixture()
    # Actual contact solver penetration; no target candidate is supplied.
    measured = pallet.measured_corner((.4, .3, .2), (1.051, -1.153, .24999), (0., 0., 0., 1.))
    assert measured == pytest.approx((.101, .197, 0., 0.))
    with pytest.raises(ExecutionFault, match='penetrates'):
        pallet.measured_corner((.4, .3, .2), (1.051, -1.153, .249), (0., 0., 0., 1.))


def test_only_supported_fresh_physical_commands():
    boxes, command, pallet = command_fixture()
    assert validate_command(command, boxes, pallet, 4)[0] == 'BOX1'
    with pytest.raises(ExecutionFault, match='STALE'):
        validate_command(command, boxes, pallet, 5)
    with pytest.raises(ExecutionFault, match='ACTUATOR'):
        validate_command({**command, 'action': 'BUFFER_CURRENT'}, boxes, pallet, 4)
    with pytest.raises(ExecutionFault, match='outside'):
        validate_command({**command, 'target_min_corner': [1., .2, 0., 0.]}, boxes, pallet, 4)
    with pytest.raises(ExecutionFault, match='integer'):
        validate_command({**command, 'state_version': True}, boxes, pallet, 4)


def test_target_fallback_cannot_be_reported_as_measured_success():
    _, values, _ = command_fixture()
    command = NS(state_version=4, box_id='BOX1', candidate=NS(candidate_id='c4'))
    good = measured_report(values, (.1, .2, 0., 0.))
    guard_runtime_result(command, good)
    for changed in ({'measured_pose': None}, {'ok': False}, {'box_id': 'BOX2'},
                    {'state_version': 3}, {'candidate_id': 'c3'}, {'measurement_source': 'TARGET'},
                    {'measured_frame': 'world'}, {'issues': []}):
        with pytest.raises(ExecutionFault):
            guard_runtime_result(command, {**good, **changed})


def test_settled_pose_requires_fresh_position_and_orientation_over_time():
    samples = [PoseSample((1., 2., 3.), (0., 0., 0., (-1.)**i), i, i*.01) for i in range(35)]
    kwargs = dict(after=-1, now=.35, max_age=1., tolerance=.002,
                  window=.25, angle_tolerance=math.radians(1))
    assert settled_sample(samples, **kwargs) is samples[-1]
    assert settled_sample(samples, **{**kwargs, 'now': 3.}) is None
    assert settled_sample(samples, **{**kwargs, 'after': 32}) is None
    moving = [replace(s, position=(1.+i*.01, 2., 3.)) for i, s in enumerate(samples)]
    assert settled_sample(moving, **kwargs) is None
    rotating = [replace(s, orientation=(0., 0., math.sin(i*.01), math.cos(i*.01)))
                for i, s in enumerate(samples)]
    assert settled_sample(rotating, **kwargs) is None


def test_native_world_feed_ignores_link_frames_and_rejects_other_coordinate_frames():
    poses = WorldPoses(['BOX1'])
    def tf(name, frame='world'):
        return NS(child_frame_id=name, header=NS(frame_id=frame),
                  transform=NS(translation=NS(x=1., y=2., z=3.), rotation=NS(x=0., y=0., z=0., w=1.)))
    poses.feed(NS(transforms=[tf('link'), tf('BOX1')]))
    assert poses.get('BOX1').position == (1., 2., 3.)
    with pytest.raises(ExecutionFault, match='Fresh'):
        poses.get('BOX1', after=poses.sequence)
    with pytest.raises(ExecutionFault, match='world frame'):
        poses.feed(NS(transforms=[tf('BOX1', 'base_link')]))


def test_planning_obstacles_match_actual_collision_not_visual_roller_top():
    name, obstacles = world_obstacles((FIXTURES/'ahead_workcell_v2_hdp160.sdf').read_text())
    assert name == 'ahead_workcell_v2'
    belt = next(o for o in obstacles if o['id'].startswith('conveyor_main/'))
    assert belt['position'][2]+belt['dimensions'][2]/2 == pytest.approx(.85)
    assert not any(o['id'].split('/')[-1].startswith('roller_') for o in obstacles)
    pallet = next(o for o in obstacles if o['id'].startswith('pallet_main/'))
    assert pallet['position'] == pytest.approx((1.35, -1., .075))
    assert pallet['position'][2]+pallet['dimensions'][2]/2 == pytest.approx(.15)


def test_reviewed_real_eoat_description_has_mass_contact_and_detached_startup_plugin(tmp_path):
    xacro = pytest.importorskip('xacro')  # development parser, not a ROS test
    wrapper = tmp_path/'eoat.xacro'
    wrapper.write_text('<robot xmlns:xacro="http://www.ros.org/wiki/xacro">'
                       f'<xacro:include filename="{FIXTURES / "vacuum_gripper_v1.urdf.xacro"}"/>'
                       '<xacro:vacuum_gripper_v1 parent="tool0"/></robot>')
    eoat = xacro.process_file(str(wrapper)).toxml()
    assembled = ET.fromstring(assemble_urdf((FIXTURES/'hdr50_22.urdf').read_text(), eoat,
                            [{'box_id': 'BOX1'}], (1.35, .15, .5), 'ahead_demo_robot'))
    assert assembled.find("joint[@name='world_joint']/origin").get('xyz') == '1.35 0.15 0.5'
    eoat_links = [l for l in assembled.findall('link') if l.get('name').startswith('vacuum_')]
    assert sum(float(l.find('inertial/mass').get('value')) for l in eoat_links
               if l.find('inertial') is not None) == 15.
    joint = assembled.find("joint[@name='vacuum_contact_joint']")
    assert joint.find('origin').get('xyz') == '0 0 0.0475'
    plugin = assembled.find("gazebo/plugin[@name='pac_gazebo_grasp::ConfirmedGrasp']")
    assert plugin.findtext('child_link') == 'link'
    assert plugin.get('filename') == 'libpac_confirmed_grasp.so'
    srdf = xacro.process_file(str(FIXTURES/'hdr50_22.srdf.xacro'),
                              mappings={'name': 'ahead_demo_robot'}).toxml()
    srdf = ET.fromstring(assemble_srdf(srdf, 'ahead_demo_robot'))
    assert srdf.find("group[@name='hdr_manipulator']/chain").get('tip_link') == 'vacuum_contact'
    assert not srdf.findall('virtual_joint')


def test_explicit_zero_load_survives_and_measured_stack_is_rejected():
    pytest.importorskip('pac_candidates')
    from pac_candidates.config import CandidateConfig
    from pac_common import Size3D, SkuSpec
    from pac_execution.audit import check_placement
    from pac_execution.runtime_node import explicit_capacities
    from pac_runtime.loop import CellSpec
    config = CandidateConfig()
    cell = CellSpec((), {'K1': 2}, Size3D(1.2, 1., 1.35),
                    {'K1': SkuSpec('K1', Size3D(.4, .4, .2), 2., (0.,), 300.)})
    updated = explicit_capacities(cell, {'skus': {'K1': {'max_top_load_n': 0.}}})
    assert updated.catalog['K1'].top_load_capacity_n == 0.
    assert cell.catalog['K1'].top_load_capacity_n == 300.
    for invalid in (True, math.nan, math.inf, -1.):
        with pytest.raises(ExecutionFault, match='top load'):
            explicit_capacities(cell, {'skus': {'K1': {'max_top_load_n': invalid}}})
    fixture = {'pallet': {'max_load_kg': 1000.}, 'boxes': [
        {'box_id': b, 'sku': 'K1', 'size_m': [.4, .4, .2], 'weight_kg': 2., 'max_top_load_n': 0.}
        for b in ('BOX1', 'BOX2')]}
    pallet = PalletTransform((1.2, 1., 1.35), (.75, -1.5, .15))
    command = {'box_id': 'BOX1', 'state_version': 1, 'candidate_id': 'c1'}
    check_placement(command, fixture, pallet, (.1, .1, 0., 0.), {}, config)
    with pytest.raises(ExecutionFault, match='TOP_LOAD|LOAD'):
        check_placement({**command, 'box_id': 'BOX2'}, fixture, pallet, (.1, .1, .2, 0.),
                        {'BOX1': (.1, .1, 0., 0.)}, config)


def test_physical_stock_has_real_inertia_and_rejects_nonfinite_mass():
    from pac_execution.source_node import box_sdf
    box = dict(box_id='BOX1', size_m=[.4, .3, .2], weight_kg=5.)
    root = ET.fromstring(box_sdf(box))
    assert float(root.findtext('model/link/inertial/mass')) == 5.
    assert float(root.findtext('model/link/inertial/inertia/izz')) == pytest.approx(5*(.4**2+.3**2)/12)
    for mass in (True, math.nan, math.inf, 0., -1.):
        with pytest.raises(ExecutionFault):
            box_sdf({**box, 'weight_kg': mass})


def test_log_checker_cannot_accept_motion_without_payload_or_state_acknowledgment():
    # Explicitly synthetic records to test the checker, not physics evidence.
    from pac_execution.trace_check import check_trace
    _, command, _ = command_fixture()
    session = 'unit-test-only'
    fixture = dict(source_ground_truth_sha256='test-only', boxes=[{'box_id': 'BOX1'}])
    completion = dict(run_id=session, scope='ROS_GAZEBO_EXECUTION', source_ground_truth_sha256='test-only',
                      measured_and_committed=1, final_runtime_status=dict(placed=1, state_version=5))
    def row(event, **values):
        return dict(run_id=session, scope='ROS_GAZEBO_EXECUTION', event=event,
                    command_key=[4, 'BOX1', 'c4'], **values)
    rows = [row('command_accepted', command=command)]
    for phase in ('PRE_GRASP', 'DESCEND_GRASP', 'LIFT', 'PRE_PLACE', 'DESCEND_PLACE', 'RETREAT'):
        rows.extend([row('action_confirmed', action='Trajectory execution', action_status=4, moveit_error_code=1),
                     row('motion_confirmed', phase=phase)])
        if phase == 'DESCEND_GRASP':
            rows.append(row('physical_joint_confirmed', attached=True))
        elif phase == 'LIFT':
            rows.append(row('payload_lift_confirmed'))
        elif phase == 'DESCEND_PLACE':
            rows.append(row('physical_joint_confirmed', attached=False))
    rows.append(row('placement_measured', report=measured_report(command, (.1,.2,0.,0.))))
    assert check_trace(rows, completion, fixture)['status'] == 'RECORDED_ACCEPTANCE'
    with pytest.raises(ExecutionFault, match='lift measurement'):
        check_trace([r for r in rows if r['event'] != 'payload_lift_confirmed'], completion, fixture)
    with pytest.raises(ExecutionFault, match='acknowledged'):
        check_trace(rows, {**completion, 'final_runtime_status': dict(placed=0, state_version=4)}, fixture)
    attachment = next(r for r in rows if r['event'] == 'physical_joint_confirmed' and r['attached'])
    reordered = [r for r in rows if r is not attachment]
    late = next(i for i, r in enumerate(reordered) if r['event'] == 'payload_lift_confirmed')+1
    reordered.insert(late, attachment)
    with pytest.raises(ExecutionFault, match='out of order'):
        check_trace(reordered, completion, fixture)
    no_result_key = [{**r, 'command_key': None} if r['event'] == 'placement_measured' else r for r in rows]
    with pytest.raises(ExecutionFault, match='missing or out of order'):
        check_trace(no_result_key, completion, fixture)
