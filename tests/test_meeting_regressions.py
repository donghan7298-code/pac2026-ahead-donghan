"""Concrete integration failures found before the 2026-10-08 team meeting."""
from dataclasses import replace

from pac_common import PlacementCandidate, Pose3D, ValidationResult, RejectCode
from pac_planning import PlacementPlanner, PlannerConfig
from pac_planning.features import has_placement
from pac_planning.reference_backend import ReferenceBackend


def test_probe_checks_valid_candidate_after_sixteen_invalid_ones(scene):
    _, box, state, _ = scene
    proposals = [PlacementCandidate(str(i), box.box_id, Pose3D("pallet", 0., 0., 0.),
                                    state.state_version) for i in range(17)]
    calls = []

    def validate(b, c, s):
        calls.append(c.candidate_id)
        return (ValidationResult(True) if c.candidate_id == "16"
                else ValidationResult(False, (RejectCode.LOW_SUPPORT,)))

    assert has_placement(box, state, lambda b, s: proposals, validate, 16)
    assert calls[-1] == "16"


def test_one_bad_ems_does_not_abort_other_valid_candidates(scene):
    _, box, state, context = scene
    backend = ReferenceBackend(context, PlannerConfig())
    candidates = backend.generate_candidates(box, state)
    assert len(candidates) > 1
    bad = candidates[0]
    context = replace(context, ems_upper_by_candidate={bad.candidate_id: bad.target_pose})
    planner = PlacementPlanner(context=context,
                               generate_candidates=backend.generate_candidates,
                               validate_constraints=backend.validate_constraints)
    result = planner.plan(box, state, candidates, mode="current", use_time_budget=False)
    assert result.ranked
    assert bad.candidate_id not in [c.candidate_id for c in result.ranked]
    assert result.rejected[bad.candidate_id].details["reason"] == "FEATURE_INPUT_INVALID"
    assert "EMS upper" in result.rejected[bad.candidate_id].details["error"]
