"""Optional real PyBullet cross-check; requires the documented simulator patch."""
from dataclasses import replace
import json
import math

import pytest
pytest.importorskip("pac_simulation")
pytest.importorskip("pybullet")
pytest.importorskip("pac_candidates")

from pac_candidates import CandidateBackend
from pac_common import BoxStatus, InventoryState, PalletState, Pose3D, Size3D, SystemState
from pac_planning import PlannerConfig
from pac_planning.team_bridge import plan_with_backend
from pac_planning.scene_bridge import bullet_payload
from pac_simulation.ahead_sim.models import BoxSpec, PalletConfig, SimulatorConfig
from pac_simulation.ahead_sim.simulator import AheadLiveSimulator
from pac_simulation.ahead_sim.strength import evaluate_box_compression


def test_zero_load_patch_does_not_turn_unknown_or_infinity():
    row = dict(id="no_stack", size_m=[.4,.3,.2], mass_kg=5.,
               target_position_m=[0.,0.,.1], max_top_load_n=0.)
    assert BoxSpec.from_mapping(row).max_top_load_n == 0.
    boxes = [{"id":"no_stack", "max_top_load_n":0.}]
    for load, expected in [(0., "OK"), (10., "OVERLOAD")]:
        result = evaluate_box_compression(boxes, {"no_stack":{"load_from_above_n_avg":load}})
        assert result["per_box"]["no_stack"]["status"] == expected
        json.dumps(result, allow_nan=False)
    for invalid in [-1., math.nan, math.inf]:
        with pytest.raises(ValueError):
            BoxSpec.from_mapping({**row, "max_top_load_n":invalid})


@pytest.mark.parametrize("deck", ["solid", "slatted"])
def test_real_planner_target_settles_in_teammate_bullet(scene, deck):
    _, old, _, context = scene
    box = replace(old, box_id="physics_box", size=Size3D(.4,.3,.2), weight_kg=5.,
                  status=BoxStatus.READY_FOR_PICK, pose=Pose3D("conveyor",0,0,0))
    state = SystemState(0, 0., PalletState("P", Size3D(1.1,1.1,1.35), ()),
                        InventoryState({box.box_id:box}, {}))
    context = replace(context, observed_preview=(), uncertain_box_ids=())
    backend = CandidateBackend(context)
    result = plan_with_backend(box,state,backend,config=PlannerConfig(horizon=1,scenario_count=1),
                               use_time_budget=False)
    assert result.ranked
    payload = bullet_payload(box,result.ranked[0],state,context,simulator_size_xy=(1.1,1.1))
    sim = AheadLiveSimulator(SimulatorConfig(pallet=PalletConfig(collision_model=deck)))
    try:
        sim.place_mapping(payload)
        for _ in range(60):
            sim.step(8)
            snapshot = sim.snapshot()
        actual = snapshot["boxes"][0]
        # Read real Bullet position, not requested target or a mocked response.
        import pybullet as p
        record = sim.world.boxes[box.box_id]
        pos, quat = p.getBasePositionAndOrientation(record.body_id, physicsClientId=sim.world.client_id)
        assert math.dist(pos, payload["target_position_m"]) < .01
        roll, pitch, _ = p.getEulerFromQuaternion(quat)
        assert max(abs(roll),abs(pitch)) < math.radians(2)
        assert len(snapshot["boxes"]) == 1
        assert result.requires_robot_validation  # physical drop != robot pick/place
        assert not state.pallet.boxes
    finally:
        sim.close()
