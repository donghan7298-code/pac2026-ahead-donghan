"""Finite original-box fixture -> real Gazebo bodies -> measured observations.

Only incoming stock is positioned with set_pose. A picked/placed box is never
spawned or teleported to its placement target. The source advances after the
runtime acknowledges the measured successful placement in a newer state.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import re
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from .contract import ExecutionFault, identity, upright_yaw, vector
from .node_io import WorldPoses, wait_future


def box_sdf(box):
    name = identity(box['box_id'])
    x, y, z = vector(box['size_m'], 3, 'box dimensions')
    if type(box['weight_kg']) not in (int, float) or not math.isfinite(box['weight_kg']):
        raise ExecutionFault('Physical body mass must be a finite number in kg')
    mass = float(box['weight_kg'])
    if min(x, y, z, mass) <= 0:
        raise ExecutionFault('Physical body size and mass must be positive')
    root = ET.Element('sdf', version='1.8')
    model = ET.SubElement(root, 'model', name=name)
    link = ET.SubElement(model, 'link', name='link')
    inertia = ET.SubElement(link, 'inertial')
    ET.SubElement(inertia, 'mass').text = str(mass)
    moment = ET.SubElement(inertia, 'inertia')
    for key, val in dict(ixx=mass*(y*y+z*z)/12, iyy=mass*(x*x+z*z)/12,
                         izz=mass*(x*x+y*y)/12, ixy=0., ixz=0., iyz=0.).items():
        ET.SubElement(moment, key).text = str(val)
    for kind in ('collision', 'visual'):
        element = ET.SubElement(link, kind, name=kind)
        geometry = ET.SubElement(element, 'geometry')
        ET.SubElement(ET.SubElement(geometry, 'box'), 'size').text = f'{x} {y} {z}'
        if kind == 'collision':
            friction = ET.SubElement(ET.SubElement(element, 'surface'), 'friction')
            ode = ET.SubElement(friction, 'ode')
            ET.SubElement(ode, 'mu').text = '0.6'
            ET.SubElement(ode, 'mu2').text = '0.6'
    return ET.tostring(root, encoding='unicode')


def gazebo_service(world, endpoint, request_type, request):
    identity(world)
    result = subprocess.run(['ign', 'service', '-s', f'/world/{world}/{endpoint}',
                             '--reqtype', request_type, '--reptype', 'ignition.msgs.Boolean',
                             '--timeout', '5000', '--req', request],
                            capture_output=True, text=True, timeout=8.)
    if result.returncode != 0 or not re.search(r'\bdata:\s*true\b', result.stdout):
        raise ExecutionFault('Gazebo '+endpoint+' failed: '+(result.stdout+result.stderr)[-500:])


def main(args=None):
    from pathlib import Path
    import rclpy
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from std_msgs.msg import String
    from std_srvs.srv import Trigger
    from tf2_msgs.msg import TFMessage

    class BoxSource(Node):
        def __init__(self):
            super().__init__('pac_physical_box_source')
            fixture = self.declare_parameter('fixture_file', '').value
            self.session = self.declare_parameter('demo_session_id', '').value
            completion = self.declare_parameter('completion_file', '').value
            self.completion_file = Path(completion) if completion else None
            self.world = identity(self.declare_parameter('world', 'ahead_workcell_v2').value)
            self.fixture = json.loads(Path(fixture).read_text())
            self.boxes = self.fixture['boxes']
            self.poses = WorldPoses([b['box_id'] for b in self.boxes])
            self.group = ReentrantCallbackGroup()
            self.obs_pub = self.create_publisher(String, '/pac/observation', 10)
            self.done_pub = self.create_publisher(String, '/pac/demo_complete', 10)
            self.fault_pub = self.create_publisher(String, '/pac/execution_fault', 10)
            self.create_subscription(TFMessage, '/pac/gazebo_world_poses', self.poses.feed, 10,
                                     callback_group=self.group)
            self.create_subscription(String, '/pac/executor_ready', self.ready, 10, callback_group=self.group)
            self.create_subscription(String, '/pac/execution_result', self.result, 10, callback_group=self.group)
            self.create_subscription(String, '/pac/status', self.status, 10, callback_group=self.group)
            self.create_subscription(String, '/pac/runtime_fault', self.fault, 10, callback_group=self.group)
            self.create_subscription(String, '/pac/execution_fault', self.fault, 10, callback_group=self.group)
            self.init_client = self.create_client(Trigger, '/pac/grasp/initialize', callback_group=self.group)
            self.condition = threading.Condition()
            self.executor_ready, self.execute_enabled, self.held = False, False, False
            self.index, self.active, self.pending_result = 0, None, None
            self.latest_status = {}
            self.worker = ThreadPoolExecutor(max_workers=1)
            self.worker.submit(self.start)

        def ready(self, message):
            data = json.loads(message.data)
            with self.condition:
                self.executor_ready, self.execute_enabled = data['ready'], data['execute_enabled']
                self.condition.notify_all()

        def fault(self, message):
            self.held = True
            self.get_logger().error('Cell holds: '+message.data)
            with self.condition:
                self.condition.notify_all()

        def stop(self, error):
            self.held = True
            self.fault_pub.publish(String(data=json.dumps({'reason': str(error), 'requires_recovery': True})))

        def start(self):
            try:
                deadline = time.monotonic()+120.
                with self.condition:
                    while not self.executor_ready and not self.held and time.monotonic() < deadline:
                        self.condition.wait(.1)
                if self.held or not self.executor_ready:
                    raise ExecutionFault('Confirmed executor readiness is absent')
                for i, box in enumerate(self.boxes):
                    # Far stock, mutually separated and above the floor. Each
                    # Custom plugins start detached; initialization also
                    # confirms that state from actual post-update feedback.
                    stock = (-3.4+(i%2)*.65, -2.6+(i//2)*.60, .6+box['size_m'][2]/2)
                    request = (f'sdf: {json.dumps(box_sdf(box))} name: {json.dumps(box["box_id"])} '
                               f'pose {{ position {{ x: {stock[0]} y: {stock[1]} z: {stock[2]} }} }} '
                               'allow_renaming: false')
                    gazebo_service(self.world, 'create', 'ignition.msgs.EntityFactory', request)
                if not self.init_client.wait_for_service(timeout_sec=10.):
                    raise ExecutionFault('Physical grasp initialization service is absent')
                response = wait_future(self.init_client.call_async(Trigger.Request()), 120., 'Stock detach initialization')
                if not response.success:
                    raise ExecutionFault(response.message)
                if not self.execute_enabled:
                    self.get_logger().info('Preview prepared; restart with execute_enabled:=true to run the acceptance sequence')
                    return
                self.next_box()
            except Exception as error:
                self.stop(error)

        def next_box(self):
            try:
                if self.held:
                    return
                if self.index == len(self.boxes):
                    completion = {
                        'boxes': len(self.boxes), 'measured_and_committed': len(self.boxes),
                        'run_id': self.session, 'scope': 'ROS_GAZEBO_EXECUTION', 'vision_verified': False,
                        'source_ground_truth_sha256': self.fixture['source_ground_truth_sha256'],
                        'final_runtime_status': self.latest_status}
                    if self.completion_file:
                        from pac_planning.training_data import atomic_json
                        atomic_json(self.completion_file, completion)
                    self.done_pub.publish(String(data=json.dumps(completion)))
                    self.get_logger().info('All fixture boxes were measured and acknowledged by the runtime')
                    return
                box = self.boxes[self.index]
                self.active = box['box_id']
                x, y, floor = self.fixture['pick_surface_world_m']
                center = x, y, floor+box['size_m'][2]/2+.002
                sequence = self.poses.sequence
                request = (f'name: {json.dumps(self.active)} position {{ x: {center[0]} y: {center[1]} z: {center[2]} }} '
                           'orientation { w: 1 }')
                gazebo_service(self.world, 'set_pose', 'ignition.msgs.Pose', request)
                observed = self.poses.stable(self.active, after=sequence)
                if math_distance(observed.position[:2], center[:2]) > .02:
                    raise ExecutionFault('Incoming body did not reach the physical pick surface')
                upright_yaw(observed.orientation)
                if abs(observed.position[2]-box['size_m'][2]/2-floor) > .005:
                    raise ExecutionFault('Incoming body has not settled on the collidable conveyor surface')
                self.index += 1
                self.obs_pub.publish(String(data=json.dumps({
                    'box_id': self.active, 'label_sku': box['sku'], 'size_m': box['size_m'],
                    'weight_kg': box['weight_kg'], 'confidence': 1., 'visual_damage': False,
                    'stamp': self.get_clock().now().nanoseconds/1e9,
                    'observation_source': 'KNOWN_GAZEBO_RIGID_BODY'})))
            except Exception as error:
                self.stop(error)

        def result(self, message):
            data = json.loads(message.data)
            if (data.get('ok') is True and data.get('box_id') == self.active
                    and data.get('run_id') == self.session):
                self.pending_result = data
                self.try_advance()

        def status(self, message):
            self.latest_status = json.loads(message.data)
            if self.latest_status.get('physical_hold'):
                self.held = True
            self.try_advance()

        def try_advance(self):
            with self.condition:
                result, status = self.pending_result, self.latest_status
                if (not self.held and result is not None and status.get('state_version', -1) > result['state_version']
                        and status.get('placed') == self.index):
                    self.pending_result, self.active = None, None
                    self.worker.submit(self.next_box)

    def math_distance(a, b):
        return sum((x-y)**2 for x, y in zip(a, b))**.5

    rclpy.init(args=args)
    node = BoxSource()
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
