"""SetBool services backed by actual Gazebo DetachableJoint feedback.

Publish a request and wait for 'attached'/'detached'; sending Empty alone is
never reported as a successful grasp. Initialization detaches all stock boxes
before the arm or conveyor source is allowed to move.
"""

import json
import threading
import time

from .contract import ExecutionFault, identity


def main(args=None):
    from pathlib import Path
    import rclpy
    from rclpy.callback_groups import ReentrantCallbackGroup
    from rclpy.executors import MultiThreadedExecutor
    from rclpy.node import Node
    from std_msgs.msg import Empty, String
    from std_srvs.srv import SetBool, Trigger

    class GazeboGrasp(Node):
        def __init__(self):
            super().__init__('pac_gazebo_grasp')
            fixture = self.declare_parameter('fixture_file', '').value
            self.ids = [identity(b['box_id']) for b in json.loads(Path(fixture).read_text())['boxes']]
            self.timeout = float(self.declare_parameter('feedback_timeout_s', 10.).value)
            self.condition = threading.Condition()
            self.operation = threading.Lock()
            self.states, self.pubs = {}, {}
            self.ready = False
            self.group = ReentrantCallbackGroup()
            for bid in self.ids:
                self.pubs[bid] = {v: self.create_publisher(Empty, f'/pac/grasp/{bid}/{v}', 10)
                                  for v in ('attach', 'detach')}
                self.create_subscription(String, f'/pac/grasp/{bid}/state',
                                         lambda msg, box=bid: self.feedback(box, msg), 10,
                                         callback_group=self.group)
                self.create_service(SetBool, f'/pac/grasp/{bid}/set',
                                    lambda req, res, box=bid: self.set(box, req, res),
                                    callback_group=self.group)
            self.create_service(Trigger, '/pac/grasp/initialize', self.initialize, callback_group=self.group)

        def feedback(self, bid, message):
            if message.data not in ('attached', 'detached'):
                self.get_logger().error('Unexpected physical joint state: '+message.data)
                return
            with self.condition:
                self.states[bid] = message.data
                self.condition.notify_all()

        def confirm(self, bid, wanted):
            deadline = time.monotonic()+self.timeout
            with self.condition:
                while self.states.get(bid) != wanted:
                    if time.monotonic() >= deadline:
                        raise ExecutionFault('No physical '+wanted+' feedback for '+bid)
                    verb = 'attach' if wanted == 'attached' else 'detach'
                    self.pubs[bid][verb].publish(Empty())
                    self.condition.wait(min(.2, deadline-time.monotonic()))

        def initialize(self, request, response):
            del request
            if not self.operation.acquire(blocking=False):
                response.success, response.message = False, 'Another grasp operation is running'
                return response
            try:
                if self.ready:
                    response.success, response.message = True, 'Already initialized'
                    return response
                for bid in self.ids:
                    self.confirm(bid, 'detached')
                self.ready = True
                response.success, response.message = True, 'All stock boxes physically detached'
            except Exception as error:
                response.success, response.message = False, str(error)
            finally:
                self.operation.release()
            return response

        def set(self, bid, request, response):
            if not self.operation.acquire(blocking=False):
                response.success, response.message = False, 'Another grasp operation is running'
                return response
            try:
                if not self.ready:
                    raise ExecutionFault('Initialize and confirm all stock boxes before moving')
                wanted = 'attached' if request.data else 'detached'
                if request.data and any(value == 'attached' for key, value in self.states.items() if key != bid):
                    raise ExecutionFault('Another box is already attached')
                self.confirm(bid, wanted)
                response.success, response.message = True, 'Physical joint confirmed '+wanted
            except Exception as error:
                response.success, response.message = False, str(error)
            finally:
                self.operation.release()
            return response

    rclpy.init(args=args)
    node = GazeboGrasp()
    executor = MultiThreadedExecutor(num_threads=4)
    executor.add_node(node)
    try:
        executor.spin()
    finally:
        executor.shutdown()
        node.destroy_node()
        rclpy.shutdown()
