"""Checkpoint, held-out data and paired-evaluation evidence regressions."""

import copy
import gzip
import json

import numpy as np
import pytest

from pac_planning import PlannerConfig
from pac_planning.evaluation import paired_comparison, packed_query_report
from pac_planning.features import FEATURE_NAMES
from pac_planning.model import lambda_gradient, train_model
from pac_planning.training_data import PackedQueries, pack_queries


def group(base, key=None):
    rows = []
    for i, relevance in enumerate((.1, .5, .9)):
        x = [0.] * len(FEATURE_NAMES)
        x[0] = relevance
        x[FEATURE_NAMES.index("safety_saturated")] = 1.
        x[FEATURE_NAMES.index("ems_known")] = 1.
        rows.append(dict(candidate_id="c" + str(i), features=x, geometry_source="EMS_SUPPLIED",
                         static=dict(safety=1., space=0., time=0.), teacher_score=relevance,
                         future=dict(mean=relevance, cvar=.8 * relevance,
                                     blocking_rate=1 - relevance, failure_rate=(1 - relevance) / 2)))
    return dict(base_group=base, group_id=key or base, rows=rows)


def _pack(tmp_path, name, groups):
    path = tmp_path / (name + ".gz")
    with gzip.open(path, "wt") as f:
        for g in groups:
            f.write(json.dumps(g) + "\n")
    return pack_queries([path], tmp_path / name)


def test_disk_pack_deduplicates_and_detects_byte_changes(tmp_path):
    data = _pack(tmp_path, "train", [group("A"), group("A"), group("B")])
    assert len(data) == 2 and len(data.features) == 6
    assert isinstance(data.features, np.memmap)
    path = data.root / "future.npy"
    raw = bytearray(path.read_bytes())
    raw[-1] ^= 1
    path.write_bytes(raw)
    with pytest.raises(ValueError, match="integrity mismatch"):
        PackedQueries(data.root)


def test_disk_labels_without_ems_or_with_invalid_future_are_rejected(tmp_path):
    bad = group("A")
    bad["rows"][0]["geometry_source"] = "FOOTPRINT_COLUMN_PROXY"
    with pytest.raises(ValueError, match="EMS"):
        _pack(tmp_path, "bad_ems", [bad])
    bad = group("A")
    bad["rows"][0]["future"]["cvar"] = .99
    with pytest.raises(ValueError, match="CVaR"):
        _pack(tmp_path, "bad_future", [bad])


def test_same_snapshot_with_different_monte_carlo_targets_is_preserved(tmp_path):
    first = group("A", "same_snapshot")
    second = copy.deepcopy(first)
    second["rows"][0]["future"]["mean"] = .12
    data = _pack(tmp_path, "repeated_pass", [first, first, second])
    assert len(data) == 2 and len(data.features) == 6
    assert data.base_groups == {"A"}
    assert np.allclose([data.future[0, 0], data.future[3, 0]], [.1, .12])


def test_interrupted_array_pack_can_restart_without_trusting_partial_arrays(tmp_path):
    root = tmp_path / "interrupted"
    root.mkdir()
    (root / "features.npy").write_bytes(b"incomplete")
    data = _pack(tmp_path, "interrupted", [group("A")])
    assert len(data) == 1 and len(data.features) == 3


def test_checkpoint_resume_matches_uninterrupted_minibatch_training(tmp_path):
    train = _pack(tmp_path, "train", [group("A"), group("B"), group("C")])
    val = _pack(tmp_path, "val", [group("V")])
    settings = dict(seed=4, hidden=8, batch_queries=2, patience=0)
    full, full_hist = train_model(train, val, epochs=7, **settings)
    path = tmp_path / "checkpoint.json"
    train_model(train, val, epochs=3, checkpoint_path=path, **settings)
    resumed, resumed_hist = train_model(train, val, epochs=7, checkpoint_path=path, resume=True, **settings)
    assert resumed.payload == full.payload
    assert resumed_hist == full_hist
    # A different dataset or optimizer must not inherit stale Adam state.
    with pytest.raises(ValueError, match="contract mismatch"):
        train_model(train, val, epochs=8, checkpoint_path=path, resume=True,
                    **{**settings, "learning_rate": .001})
    changed = _pack(tmp_path, "changed", [group("D")])
    with pytest.raises(ValueError, match="contract mismatch"):
        train_model(changed, val, epochs=8, checkpoint_path=path, resume=True, **settings)


def test_warm_start_protects_previous_validation_inventory(tmp_path):
    model, _ = train_model([group("A")], [group("V")], hidden=8, epochs=2)
    with pytest.raises(ValueError, match="previous held-out"):
        train_model([group("V")], [group("NEW")], initial_model=model, hidden=8, epochs=2)
    new, _ = train_model([group("NEW")], [group("W")], initial_model=model, hidden=8, epochs=2)
    assert new.payload["training"]["initialization"] == "WARM_START_NEW_ADAM"


def test_vectorized_lambdas_equal_reference_with_block_boundary():
    rng = np.random.default_rng(7)
    n = 270
    scores, relevance = rng.normal(size=n), rng.choice([0., .3, .7, 1.], size=n)
    rel = 3 * (relevance - relevance.min()) / np.ptp(relevance)
    gain = 2 ** rel - 1
    discount = 1 / np.log2(np.arange(n) + 2)
    ideal = np.sort(gain)[::-1] @ discount
    rank = np.empty(n, dtype=int)
    rank[np.argsort(-scores, kind="stable")] = np.arange(n)
    reference = np.zeros(n)
    for i in range(n):
        for j in range(n):
            if rel[i] > rel[j]:
                weight = abs((gain[i] - gain[j]) * (discount[rank[i]] - discount[rank[j]])) / ideal
                pair = weight / (1 + np.exp(np.clip(scores[i] - scores[j], -40, 40)))
                reference[i] -= pair
                reference[j] += pair
    assert np.allclose(lambda_gradient(scores, relevance), reference, atol=1e-13)


def episode(mode, inventory, seed, **overrides):
    return dict(mode=mode, scenario_id=inventory, inventory_group=inventory, seed=seed,
                variant="clean", stream_sha256=inventory + str(seed), pallet_equivalent=1., pallets=1,
                placed=8, placed_volume_m3=.2, fill_true_mean=.2, time_s=80., L4=0,
                final_audit_issues=0, executability_rate=1., ems_verified=True, **overrides)


def test_repeated_seeds_are_clustered_and_sparse_evidence_cannot_promote():
    rows = [episode(m, "A", seed) for m in ("dblf", "learned_ranking") for seed in range(12)]
    report = paired_comparison(rows, "learned_ranking")
    assert report["episodes"] == 12 and report["independent_inventory_groups"] == 1
    assert report["metrics"]["pallet_equivalent"]["ci95"] == [0., 0.]
    assert report["deployment_gate"]["status"] == "KEEP_BASELINE"
    assert "TOO_FEW_INDEPENDENT_INVENTORIES_FOR_PROMOTION" in report["deployment_gate"]["reasons"]
    rows[-1]["stream_sha256"] = "different"
    with pytest.raises(ValueError, match="streams differ"):
        paired_comparison(rows, "learned_ranking")


def test_packed_teacher_report_retains_safety_priority(tmp_path):
    data = _pack(tmp_path, "queries", [group("A")])
    report = packed_query_report(data, PlannerConfig(top_k=3))
    assert report["queries"] == 1 and report["candidates"] == 3
    assert report["teacher_best_top_k_recall"] == 1.
    assert report["teacher_top1_relevance_regret"] >= 0.
