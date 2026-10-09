"""Order-list visibility must reach 5-3/6 without reconstructing hidden counts."""
from dataclasses import replace

import pytest

pytest.importorskip("pac_highlevel")
from pac_candidates import CandidateConfig
from pac_highlevel import Arrival, HighLevelConfig, PalletizingWorld
from pac_planning import PlannerConfig
from pac_planning.scenarios import sample_scenarios
from pac_planning.team_bridge import TeamPlacer


@pytest.mark.parametrize("known", [True, False])
def test_highlevel_order_visibility_reaches_planner(scene, known):
    _, template, state, context = scene
    boxes = [replace(template, box_id=f"order-{i}") for i in range(4)]
    cfg = HighLevelConfig()
    cfg = replace(cfg, features=replace(cfg.features, order_list_known=known))
    placer = TeamPlacer(PlannerConfig(horizon=1, scenario_count=1), use_time_budget=False)
    world = PalletizingWorld([Arrival(b) for b in boxes], state.pallet.size,
                            context.catalog, CandidateConfig(), cfg, placer=placer)
    snapshot = world.state()
    assert dict(snapshot.inventory.remaining_by_sku) == ({template.sku_id: 3} if known else {})
    backend = world.backend()
    current = snapshot.inventory.tracked_boxes[boxes[0].box_id]
    scenarios = sample_scenarios(current, snapshot, backend.context, 7, 1, 1)
    assert len(scenarios[0].items) == (1 if known else 0)
    chosen = placer(backend.candidate_set(current, snapshot).valid, current, snapshot, backend)
    assert chosen is not None
    assert placer.last_result.requires_robot_validation
    assert world.state() == snapshot
