"""Label leakage, actual EMS and deployment-contract regression checks."""
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

pytest.importorskip("pac_candidates")
from pac_candidates import CandidateBackend
from pac_common import InventoryState, Size3D, plain
from pac_planning import PlannerConfig
from pac_planning.model import train_model
from pac_planning.planning_service import plan_request
from pac_planning.team_bridge import backend_contract, plan_with_backend
from pac_planning.team_training import (TeacherPlacer, inventory_group, training_splits,
                                        measurement_xy_assessment)


def test_same_inventory_different_order_cannot_cross_training_split(scene):
    _, box, _, _ = scene
    other = replace(box, box_id="other", sku_id=box.sku_id + "_other")
    specs = [SimpleNamespace(scenario_id="A", arrivals=(box, other)),
             SimpleNamespace(scenario_id="B", arrivals=(other, box)),
             SimpleNamespace(scenario_id="C", arrivals=(box,)),
             SimpleNamespace(scenario_id="D", arrivals=(other,))]
    dataset = SimpleNamespace(scenarios=specs, splits={"train": ["A", "C"],
                                                    "val": ["B"], "test": ["D"]})
    assert inventory_group(specs[0]) == inventory_group(specs[1])
    splits, note = training_splits(dataset)
    assert note["repartitioned"]
    assert next(name for name, ids in splits.items() if "A" in ids) == next(
        name for name, ids in splits.items() if "B" in ids)
    assert all(splits.values())


def test_real_ems_teacher_labels_model_inference_and_backend_guard(scene, tmp_path):
    _, box, state, context = scene
    backend = CandidateBackend(context)
    cfg = PlannerConfig(horizon=1, scenario_count=1)
    valid = backend.candidate_set(box, state).valid
    teacher = TeacherPlacer(cfg, "TRAIN", "inventory_a")
    chosen = teacher(valid, box, state, backend)
    assert chosen and len(teacher.groups) == 1
    assert teacher(valid, box, state, backend) == chosen
    assert len(teacher.groups) == 1  # options() repetition is not new training data
    group = teacher.groups[0]
    assert all(r["geometry_source"] == "EMS_SUPPLIED" for r in group["rows"])
    assert all(len(r["features"]) == 45 for r in group["rows"])
    # Shape/contract test only; the real generator holdout is run separately.
    validation = [{**group, "base_group": "inventory_b"}]
    model, _ = train_model([group], validation, epochs=2)
    model.payload["backend_contract"] = backend_contract(backend)
    model.payload["rollout_contract"] = {"horizon": 1, "scenario_count": 1, "cvar_alpha": cfg.cvar_alpha}
    path = tmp_path / "model.json"
    model.save(path)
    result = plan_with_backend(box, state, backend, config=cfg, model_path=path,
                               mode="ranking", use_time_budget=False)
    assert result.ranked and result.diagnostics["model_status"] == "PROVIDED"
    assert all(e.future_source == "AI_ESTIMATE" for e in result.evaluations)
    service_result = json.loads(plan_request(json.dumps(plain(state)), json.dumps(plain(context)),
        box.box_id, state.state_version, backend.config, cfg, model=model, use_time_budget=False))
    assert service_result["ranked"]
    assert service_result["diagnostics"]["model_status"] == "PROVIDED"
    wrong = CandidateBackend(context, replace(backend.config, cache_size=backend.config.cache_size + 1))
    with pytest.raises(ValueError, match="MODEL_BACKEND_MISMATCH"):
        plan_with_backend(box, state, wrong, config=cfg, model_path=path)


def test_split_with_missing_or_duplicated_scenario_is_rejected(scene):
    _, box, _, _ = scene
    dataset = SimpleNamespace(scenarios=[SimpleNamespace(scenario_id="A", arrivals=(box,))],
                              splits={"train": ["A"], "val": ["A"], "test": ["A"]})
    with pytest.raises(ValueError, match="exactly once"):
        training_splits(dataset)


def test_uncertain_measurement_can_pass_mask_and_protrude_at_true_size(scene):
    _, box, state, context = scene
    box = replace(box, size=Size3D(.388, .3, .2))
    state = replace(state, inventory=InventoryState({box.box_id: box}, {}),
                    pallet=replace(state.pallet, boxes=()))
    context = replace(context, observed_preview=(), uncertain_box_ids=(box.box_id,))
    backend = CandidateBackend(context)
    valid = backend.candidate_set(box, state).valid
    chosen = min((c for c in valid if abs(c.target_pose.yaw) < 1e-8), key=lambda c: c.target_pose.x)
    # A reproducible contract counterexample, not the unidentified report case.
    assert backend.validate_constraints(box, chosen, state).success
    assert chosen.target_pose.x + box.size.x / 2 - .4 / 2 < 0
    observer = SimpleNamespace(dimension_noise_std_m=.001, dimension_noise_clip_m=.003,
                               uncertain_dimension_noise_std_m=.004, uncertain_probability=.05)
    diag = measurement_xy_assessment(backend.config, SimpleNamespace(observation=observer))
    assert diag["status"] == "XY_MARGIN_NOT_COVERED"
    assert not next(c for c in diag["cases"] if c["box_class"] == "uncertain")["bound_covered"]


def test_xy_diagnostic_covers_bound_without_claiming_physical_safety():
    observer = SimpleNamespace(dimension_noise_std_m=.001, dimension_noise_clip_m=.003,
                               uncertain_dimension_noise_std_m=.004, uncertain_probability=.05)
    unc = SimpleNamespace(size_tolerance_m=.0061, uncertain_multiplier=2., uncertain_policy="robust")
    diag = measurement_xy_assessment(SimpleNamespace(uncertainty=unc),
                                      SimpleNamespace(observation=observer))
    assert diag["status"] == "COVERED"
    assert diag["physical_safety_verified"] is False
