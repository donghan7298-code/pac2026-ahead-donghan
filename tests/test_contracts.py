from dataclasses import replace
import math
from types import SimpleNamespace as NS
import pytest
from pac_common import (
    BoxStatus,
    InventoryState,
    PlacementCandidate,
    Pose3D,
    RejectCode,
    Size3D,
    ValidationResult,
    plain,
)
from pac_common.adapters import pose_to_ros_pose_stamped, state_from_json
from pac_planning.geometry import center_pose


@pytest.mark.parametrize(
    "size", [(0, 1, 1), (-1, 1, 1), (float("nan"), 1, 1), (1, float("inf"), 1)]
)
def test_invalid_dimensions(size):
    with pytest.raises(ValueError):
        Size3D(*size)


@pytest.mark.parametrize(
    "field,value",
    [
        ("weight_kg", -1),
        ("confidence", 1.1),
        ("confidence", float("nan")),
        ("status", "MEASURED"),
    ],
)
def test_invalid_box(scene, field, value):
    with pytest.raises(ValueError):
        replace(scene[1], **{field: value})


def test_json_roundtrip(scene):
    state = scene[2]
    assert state_from_json(plain(state)) == state
    assert state.inventory.tracked_boxes[scene[1].box_id].allowed_yaws_rad == (
        0,
        math.pi / 2,
    )


def test_nested_snapshot_immutable(scene):
    state = scene[2]
    original = {"A": 1}
    inventory = InventoryState(dict(state.inventory.tracked_boxes), original)
    original["A"] = 99
    assert inventory.remaining_by_sku["A"] == 1
    with pytest.raises(TypeError):
        inventory.remaining_by_sku["A"] = 2
    with pytest.raises(TypeError):
        inventory.tracked_boxes.clear()


def test_status_and_inventory_validation(scene):
    state = scene[2]
    with pytest.raises(ValueError):
        InventoryState({}, {"A": -1})
    with pytest.raises(ValueError):
        replace(
            state,
            inventory=InventoryState(
                {"B001": replace(scene[1], status=BoxStatus.PLACED)}, {}
            ),
        )
    with pytest.raises(ValueError):
        ValidationResult(False, ("STALE_PLAN",))


def test_stale_and_wrong_box_rejected(scene, planner):
    box, state = scene[1:3]
    c = planner.generate_candidates(box, state)[0]
    stale = replace(c, base_state_version=state.state_version - 1)
    r = planner.plan(box, state, [stale], mode="current")
    assert not r.ranked
    assert r.rejected[c.candidate_id].codes == (RejectCode.STALE_PLAN,)
    other = replace(c, box_id="OTHER")
    assert planner.plan(box, state, [other]).rejected[
        c.candidate_id
    ].codes == (RejectCode.INVALID_STATE,)


def test_ros_adapter_and_center_convention(scene):
    def factory():
        return NS(
            header=NS(frame_id="", stamp=NS(sec=0, nanosec=0)),
            pose=NS(
                position=NS(x=0.0, y=0.0, z=0.0),
                orientation=NS(x=0.0, y=0.0, z=0.0, w=1.0),
            ),
        )

    box = scene[1]
    candidate = PlacementCandidate(
        "C", box.box_id, Pose3D("pallet", 0.1, 0.2, 0.3, yaw=math.pi / 2), 12
    )
    pose = center_pose(box, candidate)
    assert pose.x == pytest.approx(0.25)
    assert pose.y == pytest.approx(0.4)
    msg = pose_to_ros_pose_stamped(pose, 1.9999999996, factory)
    assert msg.header.frame_id == "pallet"
    assert (msg.header.stamp.sec, msg.header.stamp.nanosec) == (2, 0)
    assert msg.pose.orientation.z == pytest.approx(math.sqrt(0.5))
