"""ROS boundary: JSON snapshots -> authoritative team backend -> PLANNED result.

The plain function is executable without ROS; the node requires Humble and the
generated pac_planning_interfaces service. It never issues robot commands.
"""

import json
from pac_common import plain
from pac_common.adapters import context_from_json, state_from_json
from .config import load_config
from .model import DualHeadRanker
from .team_bridge import plan_with_backend


def plan_request(state_json, context_json, box_id, expected_version,
                 candidate_config, planner_config, *, seed=7, use_time_budget=True,
                 model=None):
    from pac_candidates import CandidateBackend

    state = state_from_json(json.loads(state_json))
    context = context_from_json(json.loads(context_json))
    if state.state_version != expected_version:
        raise ValueError("STALE_PLAN: request version differs from snapshot")
    box = state.inventory.tracked_boxes[box_id]
    result = plan_with_backend(
        box, state, CandidateBackend(context, candidate_config),
        config=planner_config, model=model, seed=seed, use_time_budget=use_time_budget,
    )
    return json.dumps(plain(result), ensure_ascii=False, allow_nan=False)


def main(args=None):
    import rclpy
    from rclpy.node import Node
    from pac_planning_interfaces.srv import PlanPlacement
    from pac_candidates import load_candidate_config

    class PlacementNode(Node):
        def __init__(self):
            super().__init__("placement_planner")
            self.declare_parameter("candidate_config", "")
            self.declare_parameter("planner_config", "")
            self.declare_parameter("model_path", "")
            candidate_path = self.get_parameter("candidate_config").value
            planner_path = self.get_parameter("planner_config").value
            if not candidate_path or not planner_path:
                raise ValueError("Provide candidate_config and planner_config YAML paths")
            self.candidates = load_candidate_config(candidate_path)
            self.planner = load_config(planner_path)
            model_path = self.get_parameter("model_path").value
            self.model = DualHeadRanker.load(model_path) if model_path else None
            self.service = self.create_service(PlanPlacement, "/pac/plan_placement", self.plan)
            self.get_logger().info("Placement service ready; robot validation remains required")

        def plan(self, request, response):
            response.robot_validation_required = True
            try:
                response.result_json = plan_request(
                    request.state_json, request.context_json, request.box_id,
                    request.expected_state_version, self.candidates, self.planner,
                    seed=request.seed, model=self.model,
                )
                result = json.loads(response.result_json)
                response.success = bool(result["ranked"])
                response.error = "" if response.success else "NO_VALID_CANDIDATE"
            except (ValueError, KeyError, TypeError) as error:
                response.success = False
                response.error = str(error)
                response.result_json = ""
                self.get_logger().warning("Planning request rejected: " + str(error))
            return response

    rclpy.init(args=args)
    node = None
    try:
        node = PlacementNode()
        rclpy.spin(node)
    finally:
        if node is not None:
            node.destroy_node()
        rclpy.shutdown()
