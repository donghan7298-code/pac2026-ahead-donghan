"""Reuse the team's runtime, with stricter physical result acceptance.

This node replaces runtime_node only for the physical acceptance demo. It
keeps the same stage-4 proxy / four-slot policy contract and existing stage-5
ranker factory; it does not alter PPO observations or train PPO.
"""

from dataclasses import replace
import json
import math

from .contract import ExecutionFault, guard_runtime_result


def explicit_capacities(cell, order):
    catalog = dict(cell.catalog)
    for sku, row in order['skus'].items():
        if 'max_top_load_n' in row:
            cap = row['max_top_load_n']
            if type(cap) not in (int, float) or not math.isfinite(cap) or cap < 0:
                raise ExecutionFault('Declared top load must be a nonnegative force in N')
            # Zero is a meaningful constraint, never replaced by McKee.
            catalog[sku] = replace(catalog[sku], top_load_capacity_n=float(cap))
    return replace(cell, catalog=catalog)


def main(args=None):  # requires ROS2 Humble
    from pathlib import Path
    import rclpy
    from rclpy.node import Node
    from std_msgs.msg import String
    from pac_candidates import load_candidate_config
    from pac_highlevel import load_highlevel_config, load_policy
    from pac_robot_check import RobotFeasibility, load_robot_check_config
    from pac_runtime import load_runtime_config
    from pac_runtime.core import RuntimeCore
    from pac_runtime.order import load_order
    from pac_runtime.ros_node import CoreBridge, make_runtime_ranker, report_from_dict

    class VerifiedRuntime(Node):
        def __init__(self):
            super().__init__('pac_verified_runtime')
            p = {n: self.declare_parameter(n, default).value for n, default in (
                ('order_file', ''), ('candidates_config', ''), ('highlevel_config', ''),
                ('runtime_config', ''), ('robot_config', ''), ('policy', 'rule'), ('policy_file', ''),
                ('ranker', 'dblf'), ('ranker_model_path', ''), ('ranker_config', ''), ('ranker_seed', 7))}
            cand, high = load_candidate_config(p['candidates_config']), load_highlevel_config(p['highlevel_config'])
            if high.features.value_provider != 'proxy' or high.buffer.slots != 4:
                raise ExecutionFault('The existing proxy + four-slot policy contract is required')
            order = json.loads(Path(p['order_file']).read_text())
            cell = explicit_capacities(load_order(p['order_file'], cand), order)
            self.bridge = CoreBridge(RuntimeCore(
                cell, cand, high, load_runtime_config(p['runtime_config']),
                RobotFeasibility(load_robot_check_config(p['robot_config'])),
                load_policy(p['policy'], p['policy_file'] or None, config=high),
                make_runtime_ranker(p['ranker'], p['ranker_model_path'], p['ranker_seed'], p['ranker_config'])))
            self.command_pub = self.create_publisher(String, '/pac/command', 10)
            self.status_pub = self.create_publisher(String, '/pac/status', 10)
            self.fault_pub = self.create_publisher(String, '/pac/runtime_fault', 10)
            self.create_subscription(String, '/pac/observation', self.observation, 10)
            self.create_subscription(String, '/pac/conveyor_idle', self.idle, 10)
            self.create_subscription(String, '/pac/execution_result', self.result, 10)
            self.create_subscription(String, '/pac/execution_fault', self.fault, 10)
            self.held = False
            self.create_timer(1., lambda: self.publish(None))

        def publish(self, command):
            status = self.bridge.status()
            status['physical_hold'] = self.held
            self.status_pub.publish(String(data=json.dumps(status)))
            if command is not None and not self.held:
                self.command_pub.publish(String(data=json.dumps(command)))

        def stop(self, reason):
            self.held = True
            core = self.bridge.core
            core.supervisor.hold(core.sm.t, reason)
            self.get_logger().error(reason)
            self.fault_pub.publish(String(data=json.dumps({'reason': reason, 'requires_recovery': True})))
            self.publish(None)

        def observation(self, message):
            if self.held:
                return
            try:
                if self.bridge.pending is not None:
                    raise ExecutionFault('New observation arrived before the previous command was committed')
                _, command = self.bridge.on_observation(message.data)
                self.publish(command)
            except Exception as error:
                self.stop(str(error))

        def idle(self, message):
            if not self.held and self.bridge.pending is None:
                try:
                    _, command = self.bridge.on_idle(message.data)
                    self.publish(command)
                except Exception as error:
                    self.stop(str(error))

        def result(self, message):
            if self.held:
                return
            try:
                command = self.bridge.pending
                if command is None:
                    raise ExecutionFault('Result arrived without an outstanding command')
                data = json.loads(message.data)
                guard_runtime_result(command, data)
                if command.candidate is not None:
                    report = report_from_dict(data)
                    box = self.bridge.core.sm.tracked[command.box_id]
                    level, _, _, issues = self.bridge.core.verify(box, command.candidate, report.measured_pose)
                    if level == 'L4':
                        raise ExecutionFault('Measured placement rejected before commit: '+','.join(issues))
                _, next_command = self.bridge.on_result(message.data)
                self.publish(next_command)
            except Exception as error:
                # Pending command stays outstanding. Never commit a target pose
                # to cover a failed or unmeasured physical placement.
                self.stop(str(error))

        def fault(self, message):
            self.stop('Executor stopped: '+message.data)

    rclpy.init(args=args)
    node = VerifiedRuntime()
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
