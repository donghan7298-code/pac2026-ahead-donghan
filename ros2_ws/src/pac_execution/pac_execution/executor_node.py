"""MoveIt planning -> confirmed execution -> physical grasp -> measured commit.

All motion calls wait for action results. Cartesian descents must be complete.
A timeout/failure holds the cell; no guessed placement or automatic release is
sent. This implementation targets the separate eight-box acceptance demo, whose
only supported cell action is PLACE_CURRENT.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import threading
import time
import uuid

from .audit import check_placement
from .contract import (ExecutionFault, PalletTransform, measured_report, relative_pose,
                       upright_yaw, validate_command)
from .node_io import WorldPoses, wait_future


def main(args=None):
    from pathlib import Path
    import rclpy
    from action_msgs.msg import GoalStatus
    from controller_manager_msgs.srv import ListControllers
    from geometry_msgs.msg import Pose
    from moveit_msgs.action import ExecuteTrajectory, MoveGroup
    from moveit_msgs.msg import (AllowedCollisionEntry, AttachedCollisionObject, CollisionObject, Constraints,
                                 OrientationConstraint, PlanningScene, PlanningSceneComponents, PositionConstraint)
    from moveit_msgs.srv import ApplyPlanningScene, GetCartesianPath, GetPlanningScene
    from rclpy.action import ActionClient
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from sensor_msgs.msg import JointState
    from shape_msgs.msg import SolidPrimitive
    from std_msgs.msg import String
    from std_srvs.srv import SetBool
    from tf2_msgs.msg import TFMessage
    from tf2_ros import Buffer, TransformListener
    from pac_candidates import load_candidate_config
    from .assets import world_obstacles

    def pose_message(position, orientation):
        pose = Pose()
        pose.position.x, pose.position.y, pose.position.z = map(float, position)
        pose.orientation.x, pose.orientation.y, pose.orientation.z, pose.orientation.w = map(float, orientation)
        return pose

    class MoveItExecutor(Node):
        def __init__(self):
            super().__init__('pac_moveit_executor')
            if not self.has_parameter('use_sim_time') or not self.get_parameter('use_sim_time').value:
                raise ExecutionFault('This acceptance executor requires simulation time')
            p = {n: self.declare_parameter(n, d).value for n, d in (
                ('fixture_file', ''), ('world_sdf', ''), ('candidates_config', ''),
                ('execute_enabled', False), ('trace_file', ''), ('action_timeout_s', 120.),
                ('demo_session_id', ''))}
            self.session = p['demo_session_id'] or uuid.uuid4().hex
            self.fixture = json.loads(Path(p['fixture_file']).read_text())
            self.boxes = {b['box_id']: b for b in self.fixture['boxes']}
            row = self.fixture['pallet']
            self.pallet = PalletTransform(tuple(row['size_m']), tuple(row['origin_world_m']))
            self.candidate_config = load_candidate_config(p['candidates_config'])
            _, self.obstacles = world_obstacles(Path(p['world_sdf']).read_text())
            self.trace_file = Path(p['trace_file']) if p['trace_file'] else None
            self.execute_enabled, self.timeout = bool(p['execute_enabled']), float(p['action_timeout_s'])
            self.group_name, self.tip = 'hdr_manipulator', 'vacuum_contact'
            self.joints = [f'j{i}' for i in range(1, 7)]
            self.touch_links = ['vacuum_tool_base', 'vacuum_cup_plate', *[f'vacuum_cup_{n}' for n in range(1, 5)]]
            self.poses = WorldPoses(self.boxes)
            self.tf_buffer = Buffer()
            self.tf_listener = TransformListener(self.tf_buffer, self)
            self.callbacks = ReentrantCallbackGroup()
            self.plan_client = ActionClient(self, MoveGroup, '/move_action', callback_group=self.callbacks)
            self.execute_client = ActionClient(self, ExecuteTrajectory, '/execute_trajectory', callback_group=self.callbacks)
            self.scene_client = self.create_client(ApplyPlanningScene, '/apply_planning_scene', callback_group=self.callbacks)
            self.scene_get = self.create_client(GetPlanningScene, '/get_planning_scene', callback_group=self.callbacks)
            self.controller_client = self.create_client(ListControllers, '/controller_manager/list_controllers',
                                                       callback_group=self.callbacks)
            self.cartesian_client = self.create_client(GetCartesianPath, '/compute_cartesian_path', callback_group=self.callbacks)
            self.grippers = {bid: self.create_client(SetBool, f'/pac/grasp/{bid}/set', callback_group=self.callbacks)
                             for bid in self.boxes}
            self.result_pub = self.create_publisher(String, '/pac/execution_result', 10)
            self.fault_pub = self.create_publisher(String, '/pac/execution_fault', 10)
            self.ready_pub = self.create_publisher(String, '/pac/executor_ready', 10)
            self.create_subscription(TFMessage, '/pac/gazebo_world_poses', self.poses.feed, 10,
                                     callback_group=self.callbacks)
            self.create_subscription(String, '/pac/status', self.status, 10, callback_group=self.callbacks)
            self.create_subscription(String, '/pac/command', self.command, 10, callback_group=self.callbacks)
            self.create_subscription(JointState, '/joint_states', self.joint_state, 10, callback_group=self.callbacks)
            self.latest_version = None
            self.completed, self.placed = set(), set()
            self.committed_poses = {}
            self.lock, self.joint_condition = threading.Lock(), threading.Condition()
            self.last_joints, self.joint_sequence, self.joint_received = {}, 0, 0.
            self.pending, self.held_box, self.phase = None, None, 'STARTING'
            self.active_goal = None
            self.held = False
            self.worker = ThreadPoolExecutor(max_workers=1)
            self.worker.submit(self.prepare)

        def trace(self, event, **values):
            row = dict(event=event, phase=self.phase, held_box_id=self.held_box,
                       scope='ROS_GAZEBO_EXECUTION', run_id=self.session,
                       simulation_time_ns=self.get_clock().now().nanoseconds,
                       command_key=list(self.pending) if self.pending else None)
            row.update(values)
            self.get_logger().info(json.dumps(row))
            if self.trace_file:
                self.trace_file.parent.mkdir(parents=True, exist_ok=True)
                with self.trace_file.open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps(row, allow_nan=False)+'\n')

        def fault(self, error, command=None):
            self.held = True
            self.phase = 'HOLD'
            row = dict(reason=str(error), requires_recovery=True, held_box_id=self.held_box,
                       state_version=command.get('state_version') if command else None,
                       box_id=command.get('box_id') if command else None)
            self.trace('failure', **row)
            self.fault_pub.publish(String(data=json.dumps(row)))
            # No execution_result is emitted: the runtime cannot mark an
            # unobserved or partially completed physical action as finished.

        def status(self, message):
            data = json.loads(message.data)
            self.latest_version = data['state_version']
            if data.get('physical_hold'):
                self.held = True
                if self.active_goal is not None:
                    self.active_goal.cancel_goal_async()

        def joint_state(self, message):
            with self.joint_condition:
                self.last_joints = dict(zip(message.name, message.position))
                self.joint_sequence += 1
                self.joint_received = time.monotonic()
                self.joint_condition.notify_all()

        def service(self, client, request, label):
            if not client.wait_for_service(timeout_sec=10.):
                raise ExecutionFault(label+' service is absent')
            return wait_future(client.call_async(request), 20., label)

        def action(self, client, goal, label):
            if not client.wait_for_server(timeout_sec=10.):
                raise ExecutionFault(label+' action server is absent')
            future = client.send_goal_async(goal)
            try:
                handle = wait_future(future, 10., label+' goal acceptance')
            except ExecutionFault:
                # A delayed goal must not begin executing after our timeout.
                future.add_done_callback(lambda f: f.result().cancel_goal_async() if f.result() else None)
                raise
            if not handle.accepted:
                raise ExecutionFault(label+' goal was rejected')
            self.active_goal = handle
            try:
                result = wait_future(handle.get_result_async(), self.timeout, label+' completion')
            except ExecutionFault:
                handle.cancel_goal_async()
                raise
            finally:
                self.active_goal = None
            if result.status != GoalStatus.STATUS_SUCCEEDED or result.result.error_code.val != 1:
                raise ExecutionFault(label+f' failed: status={result.status}, MoveIt={result.result.error_code.val}')
            self.trace('action_confirmed', action=label, action_status=result.status,
                       moveit_error_code=result.result.error_code.val)
            return result.result

        def apply(self, scene):
            scene.is_diff = True
            scene.robot_state.is_diff = True
            request = ApplyPlanningScene.Request()
            request.scene = scene
            if not self.service(self.scene_client, request, 'Planning scene update').success:
                raise ExecutionFault('MoveIt did not accept the scene update')

        def collision(self, bid, size, position, orientation, frame='world'):
            obj = CollisionObject()
            obj.id, obj.header.frame_id, obj.operation = bid, frame, CollisionObject.ADD
            shape = SolidPrimitive()
            shape.type, shape.dimensions = SolidPrimitive.BOX, list(map(float, size))
            obj.primitives, obj.primitive_poses = [shape], [pose_message(position, orientation)]
            return obj

        def prepare(self):
            try:
                deadline = time.monotonic()+40.
                while time.monotonic() < deadline:
                    try:
                        transform = self.tf_buffer.lookup_transform('world', 'base_link', rclpy.time.Time())
                        p = transform.transform.translation
                        if math.dist((p.x, p.y, p.z), self.fixture['base_position_world_m']) > .001:
                            raise ExecutionFault('TF base pose differs from the Gazebo pedestal pose')
                        self.tf_buffer.lookup_transform('base_link', self.tip, rclpy.time.Time())
                        break
                    except ExecutionFault:
                        raise
                    except Exception:
                        threading.Event().wait(.1)
                else:
                    raise ExecutionFault('base_link / physical vacuum_contact TF did not appear')
                deadline = time.monotonic()+40.
                while time.monotonic() < deadline:
                    controllers = self.service(self.controller_client, ListControllers.Request(), 'Controller readiness')
                    active = {c.name for c in controllers.controller if c.state == 'active'}
                    if ({'joint_state_broadcaster', 'joint_trajectory_controller'} <= active
                            and self.joint_sequence > 0 and time.monotonic()-self.joint_received < .5
                            and all(j in self.last_joints for j in self.joints)
                            and self.get_clock().now().nanoseconds > 0):
                        break
                    threading.Event().wait(.2)
                else:
                    raise ExecutionFault('Active Gazebo controllers and fresh actual joint feedback are required')
                scene = PlanningScene()
                for row in self.obstacles:
                    obj = CollisionObject()
                    obj.id, obj.header.frame_id, obj.operation = row['id'], 'world', CollisionObject.ADD
                    primitive = SolidPrimitive()
                    primitive.type = SolidPrimitive.BOX if row['kind'] == 'box' else SolidPrimitive.CYLINDER
                    primitive.dimensions = list(map(float, row['dimensions']))
                    obj.primitives = [primitive]
                    obj.primitive_poses = [pose_message(row['position'], row['orientation'])]
                    scene.world.collision_objects.append(obj)
                # The pedestal is added separately to Gazebo by this launch.
                scene.world.collision_objects.append(self.collision(
                    'robot_pedestal', (.6, .6, .5), (1.35, .15, .25), (0., 0., 0., 1.)))
                # Permit only the actual mounting contact. Preserve the
                # complete SRDF self-collision matrix when adding this pair.
                request = GetPlanningScene.Request()
                request.components.components = PlanningSceneComponents.ALLOWED_COLLISION_MATRIX
                matrix = self.service(self.scene_get, request, 'Read collision matrix').scene.allowed_collision_matrix
                for name in ('base_link', 'robot_pedestal'):
                    if name not in matrix.entry_names:
                        for row in matrix.entry_values:
                            row.enabled.append(False)
                        matrix.entry_names.append(name)
                        matrix.entry_values.append(AllowedCollisionEntry(enabled=[False]*len(matrix.entry_names)))
                a, b = (matrix.entry_names.index(n) for n in ('base_link', 'robot_pedestal'))
                matrix.entry_values[a].enabled[b] = matrix.entry_values[b].enabled[a] = True
                scene.allowed_collision_matrix = matrix
                self.apply(scene)
                self.phase = 'READY'
                self.trace('ready', execute_enabled=self.execute_enabled)
                self.ready_timer = self.create_timer(1., lambda: self.ready_pub.publish(
                    String(data=json.dumps({'ready': not self.held, 'execute_enabled': self.execute_enabled}))))
            except Exception as error:
                self.fault(error)

        def command(self, message):
            command = None
            try:
                command = json.loads(message.data)
                with self.lock:
                    if self.held or self.phase != 'READY':
                        raise ExecutionFault('Executor is not ready')
                    if not self.execute_enabled:
                        raise ExecutionFault('execute_enabled is false')
                    bid, _ = validate_command(command, self.boxes, self.pallet, self.latest_version)
                    key = (command['state_version'], bid, command['candidate_id'])
                    if key in self.completed:
                        raise ExecutionFault('Duplicate completed command')
                    if self.pending is not None:
                        raise ExecutionFault('A command is already executing')
                    self.pending = key
                    self.phase = 'ACCEPTED'
                    self.trace('command_accepted', command=command)
                    self.worker.submit(self.run_command, command)
            except Exception as error:
                self.fault(error, command)

        def execute(self, trajectory, target=None):
            joints = trajectory.joint_trajectory
            if set(joints.joint_names) != set(self.joints) or not joints.points:
                raise ExecutionFault('Planned trajectory does not control the six real arm joints')
            # Explicit conservative retiming also covers older Humble Cartesian
            # services without velocity scaling request fields.
            previous = -1.
            for point in joints.points:
                duration = (point.time_from_start.sec+point.time_from_start.nanosec/1e9)/.25
                if duration <= previous:
                    raise ExecutionFault('Trajectory time is not strictly increasing')
                point.time_from_start.sec = int(duration)
                point.time_from_start.nanosec = int((duration-int(duration))*1e9)
                point.velocities = [v*.25 for v in point.velocities]
                point.accelerations = [a*.25*.25 for a in point.accelerations]
                previous = duration
            if previous > self.timeout-5:
                raise ExecutionFault('Trajectory exceeds the configured execution timeout')
            sequence = self.joint_sequence
            goal = ExecuteTrajectory.Goal()
            goal.trajectory = trajectory
            # Humble's moveit_msgs 2.2.1 goal contains only trajectory.
            # Newer schemas allow an explicit controller list; older versions
            # use the verified MoveIt controller mapping loaded by this launch.
            if hasattr(goal, 'controller_names'):
                goal.controller_names = ['joint_trajectory_controller']
            self.action(self.execute_client, goal, 'Trajectory execution')
            expected = dict(zip(joints.joint_names, joints.points[-1].positions))
            deadline = time.monotonic()+3.
            with self.joint_condition:
                while time.monotonic() < deadline:
                    if (self.joint_sequence > sequence and time.monotonic()-self.joint_received < .5
                            and all(j in self.last_joints and abs(self.last_joints[j]-q) < .02
                                    for j, q in expected.items())):
                        break
                    self.joint_condition.wait(.05)
                else:
                    raise ExecutionFault('Controller success lacks matching fresh joint feedback')
            if target is not None:
                tf = self.tf_buffer.lookup_transform('world', self.tip, rclpy.time.Time())
                p = tf.transform.translation
                if math.dist((p.x, p.y, p.z), target[0]) > .01:
                    raise ExecutionFault('Measured TCP differs from the completed motion target')

        def move(self, target, phase, cartesian=False):
            if self.held:
                raise ExecutionFault('Cell is held')
            self.phase = phase
            self.trace('motion_begin', position=list(target[0]), cartesian=cartesian)
            pose = pose_message(*target)
            if cartesian:
                request = GetCartesianPath.Request()
                request.header.frame_id = 'world'
                request.start_state.is_diff = True
                request.group_name, request.link_name = self.group_name, self.tip
                request.waypoints, request.max_step, request.jump_threshold = [pose], .005, 2.
                request.avoid_collisions = True
                response = self.service(self.cartesian_client, request, 'Cartesian planning')
                if response.error_code.val != 1 or response.fraction < 1.-1e-6:
                    raise ExecutionFault(f'Incomplete Cartesian descent/lift: fraction={response.fraction}')
                trajectory = response.solution
            else:
                goal = MoveGroup.Goal()
                request = goal.request
                request.group_name, request.num_planning_attempts, request.allowed_planning_time = self.group_name, 3, 8.
                request.max_velocity_scaling_factor, request.max_acceleration_scaling_factor = .5, .5
                request.start_state.is_diff = True
                constraints = Constraints()
                pc = PositionConstraint()
                pc.header.frame_id, pc.link_name, pc.weight = 'world', self.tip, 1.
                region = SolidPrimitive()
                region.type, region.dimensions = SolidPrimitive.SPHERE, [.002]
                pc.constraint_region.primitives = [region]
                pc.constraint_region.primitive_poses = [pose]
                oc = OrientationConstraint()
                oc.header.frame_id, oc.link_name, oc.orientation, oc.weight = 'world', self.tip, pose.orientation, 1.
                oc.absolute_x_axis_tolerance = oc.absolute_y_axis_tolerance = oc.absolute_z_axis_tolerance = .01
                constraints.position_constraints, constraints.orientation_constraints = [pc], [oc]
                request.goal_constraints = [constraints]
                goal.planning_options.plan_only = True
                result = self.action(self.plan_client, goal, 'MoveIt planning')
                trajectory = result.planned_trajectory
            self.execute(trajectory, target)
            self.trace('motion_confirmed')

        def grip(self, bid, attached):
            if self.held:
                raise ExecutionFault('Cell hold prevents automatic grasp or release')
            self.phase = 'GRASP' if attached else 'RELEASE'
            request = SetBool.Request()
            request.data = attached
            response = self.service(self.grippers[bid], request, 'Physical grasp feedback')
            if not response.success:
                raise ExecutionFault(response.message)
            self.held_box = bid if attached else None
            self.trace('physical_joint_confirmed', attached=attached)

        def attach_scene(self, bid):
            sample = self.poses.get(bid)
            tf = self.tf_buffer.lookup_transform('world', self.tip, rclpy.time.Time())
            p, q = tf.transform.translation, tf.transform.rotation
            position, orientation = relative_pose((p.x, p.y, p.z), (q.x, q.y, q.z, q.w),
                                                   sample.position, sample.orientation)
            attached = AttachedCollisionObject()
            attached.link_name, attached.touch_links = self.tip, self.touch_links
            attached.object = self.collision(bid, self.boxes[bid]['size_m'], position, orientation, self.tip)
            remove = CollisionObject()
            remove.id, remove.operation = bid, CollisionObject.REMOVE
            scene = PlanningScene()
            scene.world.collision_objects = [remove]
            scene.robot_state.attached_collision_objects = [attached]
            self.apply(scene)

        def detach_scene(self, bid, sample):
            attached = AttachedCollisionObject()
            attached.link_name, attached.object.id, attached.object.operation = self.tip, bid, CollisionObject.REMOVE
            scene = PlanningScene()
            scene.robot_state.attached_collision_objects = [attached]
            scene.world.collision_objects = [self.collision(bid, self.boxes[bid]['size_m'],
                                                           sample.position, sample.orientation)]
            self.apply(scene)

        def run_command(self, command):
            try:
                bid, corner = validate_command(command, self.boxes, self.pallet, self.latest_version)
                sample = self.poses.get(bid)
                box = self.boxes[bid]
                if box['weight_kg']+15. > 50.:
                    raise ExecutionFault('Box + assumed EOAT exceeds the 50 kg payload')
                yaw = upright_yaw(sample.orientation)
                # Match stage 6's selected symmetric grasp orientation while
                # preserving the requested carton orientation after placement.
                tool_offset = command.get('robot', {}).get('gripper_yaw_rad', corner[3])-corner[3]
                pick_yaw = yaw+tool_offset
                down = (math.cos(pick_yaw/2), math.sin(pick_yaw/2), 0., 0.)
                pick_position = (*sample.position[:2], sample.position[2]+box['size_m'][2]/2+.003)
                pick = pick_position, down
                pick_up = ((*pick_position[:2], pick_position[2]+.30), down)
                # Stop short of support contact; release and measure the actual
                # settled box under Gazebo physics (never teleport it into place).
                place = self.pallet.tcp(box['size_m'], corner, .013)
                place_yaw = corner[3]+self.pallet.yaw+tool_offset
                place = place[0], (math.cos(place_yaw/2), math.sin(place_yaw/2), 0., 0.)
                place_up = ((*place[0][:2], place[0][2]+.30), place[1])
                scene = PlanningScene()
                for old in self.placed | {bid}:
                    observed = self.poses.get(old)
                    scene.world.collision_objects.append(self.collision(old, self.boxes[old]['size_m'],
                                                                         observed.position, observed.orientation))
                self.apply(scene)
                self.move(pick_up, 'PRE_GRASP')
                self.move(pick, 'DESCEND_GRASP', cartesian=True)
                self.grip(bid, True)
                self.attach_scene(bid)
                before_lift = self.poses.sequence
                self.move(pick_up, 'LIFT', cartesian=True)
                lifted = self.poses.get(bid, after=before_lift)
                expected_lift = (*sample.position[:2], sample.position[2]+.30)
                if math.dist(lifted.position, expected_lift) > .012:
                    raise ExecutionFault('Arm moved but the physical payload did not follow the lift')
                self.trace('payload_lift_confirmed', box_id=bid,
                           measurement_source='GAZEBO_WORLD_POSE', measured_center_world=list(lifted.position))
                self.move(place_up, 'PRE_PLACE')
                self.move(place, 'DESCEND_PLACE', cartesian=True)
                sequence = self.poses.sequence
                self.grip(bid, False)
                observed = self.poses.stable(bid, after=sequence)
                measured = self.pallet.measured_corner(box['size_m'], observed.position, observed.orientation)
                previous = {old: self.pallet.measured_corner(self.boxes[old]['size_m'],
                                                             self.poses.get(old).position, self.poses.get(old).orientation)
                            for old in self.placed}
                if any(math.dist(previous[old][:3], self.committed_poses[old][:3]) > .001
                       for old in self.placed):
                    raise ExecutionFault('Previously committed boxes moved; a fresh pallet-state recovery is required')
                check_placement(command, self.fixture, self.pallet, measured, previous, self.candidate_config)
                self.detach_scene(bid, observed)
                self.move(place_up, 'RETREAT', cartesian=True)
                # Re-read after retreat: motion or slipping after release is
                # not hidden by the earlier sample.
                observed = self.poses.stable(bid, after=observed.sequence)
                measured = self.pallet.measured_corner(box['size_m'], observed.position, observed.orientation)
                previous = {old: self.pallet.measured_corner(self.boxes[old]['size_m'],
                                                             self.poses.get(old).position, self.poses.get(old).orientation)
                            for old in self.placed}
                if any(math.dist(previous[old][:3], self.committed_poses[old][:3]) > .001
                       for old in self.placed):
                    raise ExecutionFault('Retreat moved previously committed boxes; state recovery is required')
                check_placement(command, self.fixture, self.pallet, measured, previous, self.candidate_config)
                self.detach_scene(bid, observed)
                self.placed.add(bid)
                self.committed_poses[bid] = measured
                self.completed.add(self.pending)
                with self.lock:
                    self.pending = None
                    self.phase = 'READY'
                report = measured_report(command, measured)
                report['run_id'] = self.session
                report['raw_center_world_m'] = list(observed.position)
                report['raw_quaternion_world'] = list(observed.orientation)
                report['yaw_projection_tolerance_rad'] = .001
                report['floor_projection_tolerance_m'] = .0005
                # pending has already been cleared to let the runtime accept
                # the next box; retain this result's original identity in logs.
                self.trace('placement_measured', report=report,
                           command_key=[command['state_version'], bid, command['candidate_id']])
                self.result_pub.publish(String(data=json.dumps(report)))
            except Exception as error:
                self.fault(error, command)

    rclpy.init(args=args)
    node = MoveItExecutor()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        node.held = True
        node.worker.shutdown(wait=False, cancel_futures=True)
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
