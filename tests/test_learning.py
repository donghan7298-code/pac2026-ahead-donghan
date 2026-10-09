import copy
import numpy as np
import pytest
from pac_common import FeatureVector
from pac_planning.features import FEATURE_NAMES
from pac_planning.model import DualHeadRanker, lambda_gradient, train_model


def toy_group(base):
    rows = []
    for value in (0.1, 0.5, 0.9):
        features = [0.0] * len(FEATURE_NAMES)
        features[0] = value
        rows.append(
            {
                "features": features,
                "teacher_score": value,
                "future": {
                    "mean": value,
                    "cvar": value * 0.8,
                    "blocking_rate": 1 - value,
                    "failure_rate": (1 - value) / 2,
                },
            }
        )
    return {"base_group": base, "rows": rows}


def test_lambda_gradient_pushes_better_item_up():
    gradient = lambda_gradient(np.array([0.0, 0.0]), np.array([1.0, 0.0]))
    assert gradient[0] < 0 < gradient[1]
    assert gradient.sum() == pytest.approx(0)
    assert np.array_equal(
        lambda_gradient(np.array([0.0, 1.0]), np.array([1.0, 1.0])), [0.0, 0.0]
    )


def test_two_heads_train_reload_and_predict(tmp_path):
    train = [toy_group(1), toy_group(2)]
    validation = [toy_group(3)]
    model, _ = train_model(train, validation, seed=3, epochs=60, hidden=8)
    vectors = [
        FeatureVector(
            FEATURE_NAMES, tuple(row["features"]), {}, "TEST", "TEST"
        )
        for row in validation[0]["rows"]
    ]
    output = model.predict(vectors)
    assert output[2][0] > output[1][0] > output[0][0]
    assert output[2][1].mean > output[0][1].mean
    assert all(value.cvar <= value.mean for _, value in output)
    path = tmp_path / "model.json"
    model.save(path)
    assert DualHeadRanker.load(path).predict(vectors) == output
    bad = copy.deepcopy(model.payload)
    bad["feature_schema"] = "old-115-features"
    with pytest.raises(ValueError):
        DualHeadRanker(bad)


def test_training_rejects_inventory_group_leakage():
    with pytest.raises(ValueError, match="leakage"):
        train_model([toy_group(1)], [toy_group(1)], epochs=1)


def test_released_model_contract_and_horizon_fallback(scene, planner):
    from dataclasses import replace
    from pathlib import Path
    from pac_planning import PlannerConfig, PlacementPlanner

    path = Path(__file__).parents[1] / "models/dual_head_ranker.json"
    loaded = PlacementPlanner(
        context=scene[3],
        config=PlannerConfig(),
        generate_candidates=planner.generate_candidates,
        validate_constraints=planner.validate_constraints,
        model_path=path,
    )
    result = loaded.plan(scene[1], scene[2], mode="ranking")
    assert result.diagnostics["model_status"] == "TRAINED_DUAL_HEAD"
    assert result.evaluations[0].future_source == "AI_ESTIMATE"
    loaded.config = replace(loaded.config, horizon=8)
    result = loaded.plan(scene[1], scene[2], mode="ranking")
    assert result.diagnostics["model_status"] == "INFERENCE_FAILED:ValueError"
    assert result.evaluations[0].future_source == "CURRENT_ONLY"
