from dataclasses import replace
import pytest
from pac_common import (
    BoxStatus,
    InventoryState,
    PlacementCandidate,
    Pose3D,
    RejectCode as R,
    Size3D,
)
from pac_planning import PlacementPlanner
from pac_planning.geometry import simulate_placement


def test_size_corrected_requires_new_snapshot_and_candidates(scene, planner):
    box, state = scene[1:3]
    original = planner.generate_candidates(box, state)
    corrected = replace(box, size=Size3D(0.5, 0.35, 0.2))
    state2 = replace(
        state,
        state_version=state.state_version + 1,
        inventory=InventoryState(
            {box.box_id: corrected}, dict(state.inventory.remaining_by_sku)
        ),
    )
    stale = planner.plan(corrected, state2, original, mode="current")
    assert not stale.ranked
    assert all(v.codes == (R.STALE_PLAN,) for v in stale.rejected.values())
    new = planner.plan(corrected, state2, mode="current")
    assert new.ranked and all(c.base_state_version == 13 for c in new.ranked)


def test_tracking_lost_or_rejected_is_not_a_placement_request(scene, planner):
    box, state = scene[1:3]
    for status in (BoxStatus.UNKNOWN, BoxStatus.REJECTED, BoxStatus.FAILED):
        changed = replace(box, status=status)
        invalid = replace(
            state, inventory=InventoryState({box.box_id: changed}, {})
        )
        with pytest.raises(ValueError):
            planner.plan(changed, invalid)


def test_invalid_actual_pose_requires_state_reconciliation(scene, planner):
    box, state = scene[1:3]
    c = PlacementCandidate(
        "bad_actual", box.box_id, Pose3D("pallet", 1.1, 0, 0), 12
    )
    # Simulate a reported execution discrepancy; next planning must reject it.
    actual = simulate_placement(state, box, c).state
    next_box = replace(box, box_id="NEXT")
    tracked = dict(actual.inventory.tracked_boxes)
    tracked[next_box.box_id] = next_box
    actual = replace(actual, inventory=InventoryState(tracked, {}))
    with pytest.raises(ValueError, match="outside"):
        planner.plan(next_box, actual)


def test_ood_skips_model(scene, planner):
    class MustNotRun:
        def predict(self, *args):
            raise AssertionError("OOD should not call learned model")

    p = PlacementPlanner(
        context=replace(scene[3], distribution_status="OOD"),
        generate_candidates=planner.generate_candidates,
        validate_constraints=planner.validate_constraints,
        model=MustNotRun(),
        config=planner.config,
    )
    result = p.plan(scene[1], scene[2], use_time_budget=False)
    assert result.ranked
    assert result.diagnostics["model_status"] == "OOD_HEURISTIC_FALLBACK"
