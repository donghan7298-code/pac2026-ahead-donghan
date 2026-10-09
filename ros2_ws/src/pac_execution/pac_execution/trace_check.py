"""Offline inspection of recorded execution evidence; never commands a robot."""
from .contract import ExecutionFault, guard_runtime_result
from types import SimpleNamespace


def check_trace(rows, completion, fixture):
    session = completion.get('run_id')
    if not session or completion.get('scope') != 'ROS_GAZEBO_EXECUTION':
        raise ExecutionFault('A recorded ROS/Gazebo completion session is required')
    if completion.get('source_ground_truth_sha256') != fixture['source_ground_truth_sha256']:
        raise ExecutionFault('Completion and fixture source inventories differ')
    rows = [r for r in rows if r.get('run_id') == session]
    if not rows or any(r.get('scope') != 'ROS_GAZEBO_EXECUTION' for r in rows):
        raise ExecutionFault('No matching execution trace')
    if any(r['event'] == 'failure' for r in rows):
        raise ExecutionFault('Execution trace contains a failure/hold')
    commands = [r['command'] for r in rows if r['event'] == 'command_accepted']
    expected = [b['box_id'] for b in fixture['boxes']]
    if [c['box_id'] for c in commands] != expected:
        raise ExecutionFault('Fixture arrival order is missing, duplicated or changed')
    versions = [c['state_version'] for c in commands]
    if any(a >= b for a, b in zip(versions, versions[1:])):
        raise ExecutionFault('Command snapshot versions must increase')
    reports = [r['report'] for r in rows if r['event'] == 'placement_measured']
    if len(reports) != len(commands):
        raise ExecutionFault('Some physical commands lack measured final results')
    for command, report in zip(commands, reports):
        key = [command['state_version'], command['box_id'], command['candidate_id']]
        group = [r for r in rows if r.get('command_key') == key]
        shim = SimpleNamespace(state_version=key[0], box_id=key[1],
                               candidate=SimpleNamespace(candidate_id=key[2]))
        guard_runtime_result(shim, report)
        motion = [r['phase'] for r in group if r['event'] == 'motion_confirmed']
        if motion != ['PRE_GRASP', 'DESCEND_GRASP', 'LIFT', 'PRE_PLACE', 'DESCEND_PLACE', 'RETREAT']:
            raise ExecutionFault('Incomplete pick/place/retreat motion evidence')
        grasps = [r['attached'] for r in group if r['event'] == 'physical_joint_confirmed']
        if grasps != [True, False]:
            raise ExecutionFault('Physical attach/release acknowledgments are incomplete')
        if sum(r['event'] == 'payload_lift_confirmed' for r in group) != 1:
            raise ExecutionFault('Actual payload lift measurement is missing')
        actions = [r for r in group if r['event'] == 'action_confirmed' and r['action'] == 'Trajectory execution']
        if len(actions) != 6 or any(r['action_status'] != 4 or r['moveit_error_code'] != 1 for r in actions):
            raise ExecutionFault('Controller trajectory results are incomplete/unsuccessful')
        # Counts alone could accept a release before a grasp or an empty-arm
        # lift. Require the interleaved physical order recorded by the executor.
        critical = []
        for row in group:
            event = row['event']
            if event == 'action_confirmed' and row['action'] == 'Trajectory execution':
                critical.append('TRAJECTORY')
            elif event == 'motion_confirmed':
                critical.append(row['phase'])
            elif event == 'physical_joint_confirmed':
                critical.append('ATTACH' if row['attached'] else 'RELEASE')
            elif event in ('command_accepted', 'payload_lift_confirmed', 'placement_measured'):
                critical.append(event)
        expected_order = [
            'command_accepted', 'TRAJECTORY', 'PRE_GRASP', 'TRAJECTORY', 'DESCEND_GRASP',
            'ATTACH', 'TRAJECTORY', 'LIFT', 'payload_lift_confirmed',
            'TRAJECTORY', 'PRE_PLACE', 'TRAJECTORY', 'DESCEND_PLACE', 'RELEASE',
            'TRAJECTORY', 'RETREAT', 'placement_measured',
        ]
        if critical != expected_order:
            raise ExecutionFault('Physical execution events are missing or out of order')
    status = completion.get('final_runtime_status', {})
    if (completion.get('measured_and_committed') != len(expected) or status.get('placed') != len(expected)
            or status.get('state_version', -1) <= versions[-1] or status.get('physical_hold')):
        raise ExecutionFault('Runtime has not acknowledged every measured placement')
    return dict(scope='OFFLINE_EXECUTION_LOG_CHECK', status='RECORDED_ACCEPTANCE',
                run_id=session, boxes=len(expected),
                limitation='Reads recorded evidence; does not run ROS or independently validate physics')
