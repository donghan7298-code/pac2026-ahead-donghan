"""Real stage-4 runtime -> EMS-backed 5-3/6, without robot execution."""
from dataclasses import replace

import pytest

pytest.importorskip("pac_highlevel")
from pac_candidates import CandidateBackend
from pac_common import BoxStatus, InventoryState, plain
from pac_highlevel import HighLevelConfig, HighLevelDecider, ActionType
from pac_planning import PlannerConfig
from pac_planning.team_bridge import plan_high_level_decision


@pytest.mark.parametrize("buffered", [False, True])
def test_actual_snapshot_handoff_preserves_box_version_and_ems(scene, buffered):
    _, box, state, context = scene
    box = replace(box, status=BoxStatus.BUFFERED if buffered else box.status)
    state = replace(state, inventory=InventoryState({box.box_id: box}, {}))
    cfg = HighLevelConfig()
    cfg = replace(cfg, buffer=replace(cfg.buffer, slots=context.buffer_capacity))
    decision = HighLevelDecider(context, config=cfg).decide(state)
    assert decision.action.type == (ActionType.RETRIEVE_BUFFER if buffered else ActionType.PLACE_CURRENT)
    before = plain(state)
    result = plan_high_level_decision(decision, state, CandidateBackend(context),
                                     config=PlannerConfig(horizon=1, scenario_count=1),
                                     use_time_budget=False)
    assert result.ranked
    assert all(c.box_id == box.box_id and c.base_state_version == state.state_version
               for c in result.ranked)
    assert all(e.features.geometry_source == "EMS_SUPPLIED" for e in result.evaluations)
    assert result.requires_robot_validation
    assert plain(state) == before
    with pytest.raises(ValueError, match="STALE_PLAN"):
        plan_high_level_decision(decision, replace(state, state_version=state.state_version+1),
                                 CandidateBackend(context))
    with pytest.raises(ValueError, match="authoritative"):
        plan_high_level_decision(replace(decision, box=replace(box, weight_kg=box.weight_kg+1)),
                                 state, CandidateBackend(context))
