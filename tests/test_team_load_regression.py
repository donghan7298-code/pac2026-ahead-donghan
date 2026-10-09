"""5-3/6 integration gates for pac_candidates commit 8e934a4.

These exercise the real teammate validator, not a mock physics/robot backend.
The low-share supporter case is adapted from its 5-2 regression fixture.
"""
from dataclasses import replace
import json

import pytest

pytest.importorskip("pac_candidates")
from pac_candidates import CandidateBackend
from pac_candidates.config import CandidateConfig
from pac_common import InventoryState, PlacedBox, PlacementCandidate, Pose3D, Size3D, plain
from pac_planning import PlacementPlanner, PlannerConfig
from pac_planning.rollout import run_scenario
from pac_planning.scenarios import FutureItem, Scenario


@pytest.fixture
def low_share_scene(scene):
    _, template, state, context = scene
    strong = PlacedBox("strong", "A", Size3D(.848, .3, .2), 50.,
                       Pose3D("pallet", .002, .002, 0.))
    light = PlacedBox("light", "A", Size3D(.2, .3, .2), .5,
                      Pose3D("pallet", .858, .002, 0.))
    box = replace(template, box_id="heavy", size=Size3D(.928, .3, .2),
                  weight_kg=40., allowed_yaws_rad=(0.,))
    state = replace(state, pallet=replace(state.pallet, size=Size3D(1.1, 1.1, 1.35),
                                         boxes=(strong, light)),
                    inventory=InventoryState({box.box_id: box}, {}))
    context = replace(context, observed_preview=(), pallet_max_weight_kg=1000.,
                      capacity_overrides_n={"strong": 1e5, "light": 1e5})
    cfg = CandidateConfig()
    # Isolate heavy-on-light from the separate pallet-CoG requirement.
    cfg = replace(cfg, constraints=replace(cfg.constraints,
                  pallet_cog=replace(cfg.constraints.pallet_cog, enabled=False)))
    backend = CandidateBackend(context, cfg)
    candidate = PlacementCandidate("low-share", box.box_id,
                                   Pose3D("pallet", .002, .002, .2), state.state_version)
    return box, candidate, state, context, backend


def test_low_share_rejection_reaches_scoring_without_a_score(low_share_scene):
    box, candidate, state, context, backend = low_share_scene
    before = plain(state)
    planner = PlacementPlanner(context=context,
                               generate_candidates=backend.generate_candidates,
                               validate_constraints=backend.validate_constraints,
                               config=PlannerConfig(horizon=1, scenario_count=1))
    result = planner.plan(box, state, [candidate], use_time_budget=False)
    assert not result.ranked
    assert not result.evaluations
    verdict = result.rejected[candidate.candidate_id]
    assert "HEAVY_ON_LIGHT:light" in verdict.details["reasons"]
    assert 0 < verdict.details["metrics"]["supporter_shares"]["light"] < backend.config.constraints.heavy_on_light.min_share
    assert result.requires_robot_validation
    json.dumps(plain(result), allow_nan=False)
    assert plain(state) == before


def test_rollout_does_not_count_low_share_overload_as_future_capacity(low_share_scene):
    future, bad, state, _, backend = low_share_scene
    current = replace(future, box_id="current", size=Size3D(.05, .05, .05), weight_kg=1.)
    state = replace(state, inventory=InventoryState({current.box_id: current,
                                                     future.box_id: future}, {}))
    current_candidate = replace(bad, candidate_id="floor", box_id=current.box_id,
                                target_pose=Pose3D("pallet", .002, .9, 0.))
    assert backend.validate_constraints(current, current_candidate, state).success
    before = plain(state)
    # Restrict future search to the known unsafe proposal; validity is still
    # decided by the real backend. This isolates the rollout/validator contract.
    def proposal(box, snapshot):
        return [replace(bad, box_id=box.box_id, base_state_version=snapshot.state_version)]

    outcome = run_scenario(current, current_candidate, state,
                           Scenario("low-share", "regression", (FutureItem(future, False),)),
                           proposal, backend.validate_constraints,
                           PlannerConfig(horizon=1, scenario_count=1))
    assert outcome.blocked
    assert outcome.placed_count == 0
    assert outcome.value == 0.
    assert outcome.failure_fraction == 1.
    assert plain(state) == before
