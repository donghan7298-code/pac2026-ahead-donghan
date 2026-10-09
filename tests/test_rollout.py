from collections import Counter
from dataclasses import replace
import pytest
from pac_common import BoxStatus, InventoryState
from pac_planning.rollout import (
    ScenarioOutcome,
    evaluate_shared,
    lower_tail_cvar,
)
from pac_planning.scenarios import SCENARIO_KINDS, sample_scenarios


def test_fractional_cvar():
    assert lower_tail_cvar([0.0, 0.5, 1.0], 0.5) == pytest.approx(1 / 6)
    assert lower_tail_cvar([0.0, 0.5, 1.0], 1) == pytest.approx(0.5)
    assert lower_tail_cvar([0.3], 0.2) == pytest.approx(0.3)


def test_seven_strata_preserve_counts_and_known_prefix(scene):
    box, state, context = scene[1:]
    preview = replace(box, box_id="PREVIEW", status=BoxStatus.ON_CONVEYOR)
    state = replace(
        state,
        inventory=InventoryState(
            {box.box_id: box, preview.box_id: preview},
            {"A": 1, "B": 2, "C": 1},
        ),
    )
    context = replace(context, observed_preview=(preview,))
    a = sample_scenarios(box, state, context, 3, 7, 20)
    assert a == sample_scenarios(box, state, context, 3, 7, 20)
    assert tuple(s.kind for s in a) == SCENARIO_KINDS
    for s in a:
        assert s.items[0].box == preview
        assert not s.items[0].consume_unseen
        unseen = [i for i in s.items if i.consume_unseen]
        assert Counter(i.box.sku_id for i in unseen) == {
            "A": 1,
            "B": 2,
            "C": 1,
        }
        assert len({i.box.box_id for i in s.items}) == 5


def test_partial_round_discarded_for_every_candidate(
    scene, planner, monkeypatch
):
    box, state, context = scene[1:]
    candidates = planner.generate_candidates(box, state)[:2]
    scenarios = sample_scenarios(box, state, context, 1, 3, 1)
    elapsed = [0.0]

    def simulated_run(*args, **kwargs):
        elapsed[0] += 1
        return ScenarioOutcome(0.1, False, 0.0, 1)

    monkeypatch.setattr("pac_planning.rollout.run_scenario", simulated_run)
    outcomes, completed, interrupted = evaluate_shared(
        box,
        candidates,
        state,
        scenarios,
        planner.generate_candidates,
        planner.validate_constraints,
        planner.config,
        deadline=3.0,
        clock=lambda: elapsed[0],
    )
    assert interrupted
    assert len(completed) == 1
    assert all(len(f.scenario_values) == 1 for f in outcomes.values())


def test_zero_inventory_has_zero_additional_value(scene, planner):
    box, state = scene[1:3]
    empty = replace(state, inventory=InventoryState({box.box_id: box}, {}))
    r = planner.plan(box, empty, use_time_budget=False)
    for e in r.evaluations:
        assert e.future.mean == 0
        assert e.future.blocking_rate == 0
        assert e.future.failure_rate == 0


def test_sorted_rollout_stops_at_first_valid_without_changing_selected_pose(scene, planner):
    from pac_common import RejectCode, ValidationResult
    from pac_planning.rollout import run_scenario, greedy_key
    from pac_planning.geometry import simulate_placement
    box, state, context = scene[1:]
    root = next(c for c in planner.generate_candidates(box, state)
                if planner.validate_constraints(box, c, state).success)
    scenario = sample_scenarios(box, state, context, 1, 1, 1)[0]
    after = simulate_placement(state, box, root).state
    future = scenario.items[0].box
    proposals = sorted(planner.generate_candidates(future, after),
                       key=lambda c: greedy_key(future, c, after))
    assert len(proposals) >= 3
    calls = []
    def validator(b, c, s):
        calls.append(c.candidate_id)
        if c.candidate_id == proposals[0].candidate_id:
            return ValidationResult(False, (RejectCode.BOX_COLLISION,))
        return ValidationResult(True)
    selected = []
    def apply(s, b, c, **kwargs):
        selected.append(c.candidate_id)
        return simulate_placement(s, b, c, **kwargs)
    import unittest.mock
    with unittest.mock.patch('pac_planning.rollout.simulate_placement', apply):
        result = run_scenario(box, root, state, scenario, planner.generate_candidates,
                              validator, replace(planner.config, rollout_candidate_limit=16))
    assert result.placed_count == 1
    assert calls == [c.candidate_id for c in proposals[:2]]
    assert selected[-1] == proposals[1].candidate_id


def test_short_circuit_rollout_still_rejects_stale_later_proposals(scene, planner):
    from pac_common import ValidationResult
    from pac_planning.rollout import run_scenario
    box, state, context = scene[1:]
    root = next(c for c in planner.generate_candidates(box, state)
                if planner.validate_constraints(box, c, state).success)
    scenario = sample_scenarios(box, state, context, 1, 1, 8)[0]
    def generator(b, s):
        candidates = planner.generate_candidates(b, s)
        candidates[-1] = replace(candidates[-1], base_state_version=s.state_version + 1)
        return candidates
    with pytest.raises(ValueError, match='generator contract'):
        run_scenario(box, root, state, scenario, generator,
                     lambda *a: ValidationResult(True), planner.config)
