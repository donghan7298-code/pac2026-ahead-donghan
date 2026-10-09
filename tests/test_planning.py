from dataclasses import replace
import math
import pytest
from pac_common import (
    BoxStatus,
    InventoryState,
    PlacementCandidate,
    Pose3D,
    RejectCode as R,
    StateMode,
    ValidationResult,
    plain,
)
from pac_planning import PlacementPlanner
from pac_planning.features import FEATURE_NAMES
from pac_planning.geometry import simulate_placement
from pac_planning.reference_backend import ReferenceBackend


def test_end_to_end_reproducible_nonmutating(scene, planner):
    box, state = scene[1:3]
    before = plain(state)
    a = planner.plan(box, state, seed=7, use_time_budget=False)
    b = planner.plan(box, state, seed=7, use_time_budget=False)
    assert a.ranked == b.ranked
    assert a.evaluations == b.evaluations
    assert plain(state) == before
    assert a.requires_robot_validation
    assert a.state_mode == StateMode.PLANNED
    assert a.diagnostics["completed_scenarios"] == 7
    for row in a.evaluations:
        assert row.features.names == FEATURE_NAMES
        assert len(row.features.values) == 45
        assert row.candidate.score == pytest.approx(
            sum(row.candidate.score_detail.values())
        )
        assert row.future.worst <= row.future.cvar <= row.future.mean
        assert row.future_source == "ROLLOUT"


def test_invalid_never_scored(scene, planner):
    box, state = scene[1:3]
    bad = PlacementCandidate(
        "bad",
        box.box_id,
        Pose3D("pallet", 1.1, 0, 0),
        state.state_version,
        score=1e9,
    )
    result = planner.plan(box, state, [bad])
    assert not result.ranked
    assert R.OUT_OF_BOUND in result.rejected["bad"].codes
    with pytest.raises(ValueError):
        planner.evaluate_candidate(box, bad, state)


def test_missing_evidence_fails_closed(scene, planner):
    box, state = scene[1:3]
    p = PlacementPlanner(
        context=scene[3],
        generate_candidates=planner.generate_candidates,
        validate_constraints=lambda *_: ValidationResult(True),
    )
    result = p.plan(box, state)
    assert not result.ranked
    assert all(R.INVALID_STATE in v.codes for v in result.rejected.values())


def test_unknown_model_falls_back(scene, planner, tmp_path):
    p = PlacementPlanner(
        context=scene[3],
        generate_candidates=planner.generate_candidates,
        validate_constraints=planner.validate_constraints,
        model_path=tmp_path / "missing.json",
        config=planner.config,
    )
    r = p.plan(scene[1], scene[2], use_time_budget=False)
    assert r.ranked
    assert r.diagnostics["model_status"].startswith("MODEL_LOAD_FAILED")


def test_inference_failure_falls_back(scene, planner):
    class Broken:
        def predict(self, vectors):
            raise RuntimeError("inference failed")

    planner.model = Broken()
    r = planner.plan(scene[1], scene[2], use_time_budget=False)
    assert r.ranked
    assert r.diagnostics["model_status"].startswith("INFERENCE_FAILED")


def test_timeout_never_bypasses_hard_validator(scene, planner):
    calls = []

    def validator(*args):
        calls.append(args[1].candidate_id)
        return planner.validate_constraints(*args)

    tiny = replace(planner.config, timeout_sec=1e-12)
    p = PlacementPlanner(
        context=scene[3],
        generate_candidates=planner.generate_candidates,
        validate_constraints=validator,
        config=tiny,
    )
    generated = planner.generate_candidates(scene[1], scene[2])
    r = p.plan(scene[1], scene[2], generated)
    assert set(c.candidate_id for c in generated).issubset(set(calls))
    assert r.ranked
    assert r.diagnostics["completed_scenarios"] == 0
    assert r.diagnostics["no_rollout_fallback"]
    assert r.requires_robot_validation


def test_teacher_evaluates_all_valid(scene, planner):
    r = planner.plan(scene[1], scene[2], mode="teacher")
    assert len(r.ranked) == r.diagnostics["valid_count"]
    assert r.diagnostics["completed_scenarios"] == 7


def test_simulated_inventory_consumed_once(scene, planner):
    from pac_planning.features import sku_box

    box, state, context = scene[1:]
    current = planner.plan(box, state, mode="current").ranked[0]
    post = simulate_placement(state, box, current)
    assert post.mode == StateMode.SIMULATED
    assert (
        post.state.inventory.remaining_by_sku
        == state.inventory.remaining_by_sku
    )
    future = sku_box(context.catalog["B"], "__future__test", state.stamp_sec)
    candidate = planner.generate_candidates(future, post.state)[0]
    post2 = simulate_placement(
        post.state, future, candidate, consume_unseen=True
    )
    assert post2.state.inventory.remaining_by_sku["B"] == 2
    assert state.inventory.remaining_by_sku["B"] == 3
    with pytest.raises(ValueError):
        simulate_placement(post2.state, future, candidate, consume_unseen=True)


def test_reference_load_and_support(scene, planner):
    box, state, context = scene[1:]
    base_c = PlacementCandidate(
        "base", box.box_id, Pose3D("pallet", 0, 0, 0), 12
    )
    virtual = simulate_placement(state, box, base_c).state
    heavy = replace(
        box, box_id="HEAVY", weight_kg=9, status=BoxStatus.MEASURED
    )
    c = PlacementCandidate(
        "top", heavy.box_id, Pose3D("pallet", 0, 0, 0.2), 12
    )
    assert (
        R.LOAD_VIOLATION
        in planner.validate_constraints(heavy, c, virtual).codes
    )
    light = replace(heavy, weight_kg=2)
    assert planner.validate_constraints(
        light, replace(c, box_id=light.box_id), virtual
    ).success
    offset = replace(c, target_pose=Pose3D("pallet", 0.2, 0.2, 0.2))
    assert (
        R.LOW_SUPPORT
        in planner.validate_constraints(light, offset, virtual).codes
    )
    damaged_context = replace(context, capacity_overrides_n={box.box_id: 0.0})
    backend = ReferenceBackend(damaged_context, planner.config)
    assert (
        R.LOAD_VIOLATION
        in backend.validate_constraints(light, c, virtual).codes
    )


def test_nonzero_uncertainty_not_silently_certified(scene, planner):
    context = replace(scene[3], uncertain_box_ids=(scene[1].box_id,))
    backend = ReferenceBackend(context)
    c = planner.generate_candidates(scene[1], scene[2])[0]
    assert backend.validate_constraints(scene[1], c, scene[2]).codes == (
        R.SENSOR_UNCERTAIN,
    )


def test_robot_failover_and_stale_check(scene, planner):
    r = planner.plan(scene[1], scene[2], mode="current")
    # Stage 6 consumes ranked canonical candidates, checks version before IK.
    tested = []

    def robot_validate(candidate, current_version):
        if candidate.base_state_version != current_version:
            return ValidationResult(False, (R.STALE_PLAN,))
        tested.append(candidate.candidate_id)
        return ValidationResult(
            len(tested) > 1, () if len(tested) > 1 else (R.IK_FAIL,)
        )

    winner = next(
        c
        for c in r.ranked
        if robot_validate(c, scene[2].state_version).success
    )
    assert winner == r.ranked[1]
    assert robot_validate(winner, scene[2].state_version + 1).codes == (
        R.STALE_PLAN,
    )
    assert scene[1].status == BoxStatus.READY_FOR_PICK
