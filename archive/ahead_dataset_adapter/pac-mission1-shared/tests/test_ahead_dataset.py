import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import shutil

import pytest
import yaml

from pac_common import BoxStatus, Size3D, plain
from pac_planning.ahead_dataset import AheadDataset, observe_ahead_box
from pac_planning.config import PlannerConfig
from pac_planning.experiment import benchmark, collect_groups, make_planner
from pac_planning.geometry import simulate_placement
from pac_planning.model import DualHeadRanker, train_model
from pac_planning.scenarios import sample_scenarios

ROOT = Path(__file__).parents[1]
FIXTURE = ROOT / "tests/fixtures/ahead"
POLICY = ROOT / "config/ahead_adapter.simulation.yaml"


def dataset(root=FIXTURE, policy=POLICY):
    return AheadDataset(root, root / "generator.yaml", policy)


def changed_policy(tmp_path, **changes):
    value = yaml.safe_load(POLICY.read_text())
    value.update(changes)
    path = tmp_path / "policy.yaml"
    path.write_text(yaml.safe_dump(value))
    return path


def edit_json(root, relative, mutate):
    path = root / relative
    value = json.loads(path.read_text())
    mutate(value)
    path.write_text(json.dumps(value))
    hashes_path = root / "file_hashes.json"
    hashes = json.loads(hashes_path.read_text())
    hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    hashes_path.write_text(json.dumps(hashes))


def test_height_is_converted_on_a_local_copy(tmp_path):
    before = (FIXTURE / "test_data/S0001.json").read_bytes()
    ep = dataset().episode("S0001")
    assert ep.state.pallet.size == Size3D(1.1, 1.1, 1.5)
    assert not ep.state.inventory.tracked_boxes
    assert not ep.context.observed_preview
    assert ep.context.distribution_status == "OOD"
    assert ep.context.pallet_max_weight_kg == 150
    assert all(s.top_load_capacity_n == 0 for s in ep.context.catalog.values())
    floor = dataset(policy=changed_policy(tmp_path, height_reference="floor"))
    assert floor.episode("S0001").state.pallet.size.z == pytest.approx(1.35)
    assert (FIXTURE / "test_data/S0001.json").read_bytes() == before


def test_missing_load_limit_does_not_silently_become_infinite(tmp_path):
    source = dataset(policy=changed_policy(tmp_path, pallet_max_weight_kg=None))
    with pytest.raises(ValueError, match="explicit pallet load"):
        source.episode("S0001")


@pytest.mark.parametrize("changes", [
    {"scope": "ACTUAL"}, {"allow_identity_measurement": False},
    {"height_reference": "guess"}, {"unseen_weight_policy": "ground_truth"},
])
def test_unsafe_or_ambiguous_policy_is_rejected(tmp_path, changes):
    with pytest.raises(ValueError):
        dataset(policy=changed_policy(tmp_path, **changes))


def test_identity_release_preserves_actual_weights_and_debits_once():
    ep = dataset().episode("S0006")
    initial = plain(ep.state)
    boxes = list(ep.observations())
    raw = [json.loads(line)["observation"] for line in
           (FIXTURE / "simulation_observations/S0006.jsonl").read_text().splitlines()]
    assert [b.weight_kg for b in boxes] == [b["weight_kg"] for b in raw]
    # Same SKU may have different measured weights; it is not replaced by its prior.
    assert any(a.sku_id == b.sku_id and a.weight_kg != b.weight_kg
               for a in boxes for b in boxes)
    state = ep.state
    for i, box in enumerate(boxes):
        assert box.status == BoxStatus.READY_FOR_PICK
        assert box.pose.z == box.size.z / 2
        _, state = observe_ahead_box(state, box)
        assert len(state.inventory.tracked_boxes) == i + 1
        assert sum(state.inventory.remaining_by_sku.values()) == ep.box_count - i - 1
        assert state.state_version == i + 1
    assert plain(ep.state) == initial
    with pytest.raises(ValueError, match="already observed"):
        observe_ahead_box(state, boxes[0])
    with pytest.raises(ValueError, match="adapter-validated"):
        observe_ahead_box(ep.state, replace(boxes[0], status=BoxStatus.DETECTED))


@pytest.mark.parametrize("scenario_id", [f"S{i:04d}" for i in range(1, 7)])
def test_six_generated_families_reach_the_real_planner(scenario_id):
    ep = dataset().episode(scenario_id)
    box, state = observe_ahead_box(ep.state, next(ep.observations()))
    planner = make_planner(ep.context, PlannerConfig(horizon=1, scenario_count=7))
    result = planner.plan(box, state, use_time_budget=False)
    assert result.ranked
    assert result.diagnostics["completed_scenarios"] == 7
    assert result.diagnostics["model_status"] == "OOD_HEURISTIC_FALLBACK"
    assert len(result.evaluations[0].features.values) == 45
    after = simulate_placement(state, box, result.ranked[0]).state
    assert after.pallet.boxes[0].weight_kg == box.weight_kg
    assert after.inventory.remaining_by_sku == state.inventory.remaining_by_sku


def test_future_sampler_remains_independent_of_hidden_order(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(FIXTURE, root)
    # A poison ground-truth document must have no influence on planning.
    (root / "ground_truth").mkdir()
    (root / "ground_truth/S0001.json").write_text("NOT EVEN JSON")
    ep = dataset(root).episode("S0001")
    box, state = observe_ahead_box(ep.state, next(ep.observations()))
    futures = sample_scenarios(box, state, ep.context, 17, 7, 2)
    assert len(futures) == 7
    assert not ep.context.observed_preview
    assert set(state.inventory.tracked_boxes) == {box.box_id}
    assert all(item.box.box_id.startswith("__future__")
               for future in futures for item in future.items)
    assert all(item.box.weight_kg == 30.0 for f in futures for item in f.items)


def test_old_model_falls_back_and_new_contract_can_be_used():
    source = dataset()
    old = DualHeadRanker.load(ROOT / "models/dual_head_ranker.json")
    assert source.episode("S0001", old).context.distribution_status == "OOD"
    payload = copy.deepcopy(old.payload)
    payload["dataset_contract"] = source.contract
    tagged = DualHeadRanker(payload)
    assert source.episode("S0001", tagged).context.distribution_status == "IN_DISTRIBUTION"


def test_corrupted_source_hash_is_rejected(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(FIXTURE, root)
    with (root / "test_data/S0001.json").open("a") as stream:
        stream.write(" ")
    with pytest.raises(ValueError, match="source hash"):
        dataset(root).episode("S0001")


def test_split_overlap_is_rejected(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(FIXTURE, root)
    value = json.loads((root / "splits.json").read_text())
    value["test"] = [value["train"][0]]
    (root / "splits.json").write_text(json.dumps(value))
    with pytest.raises(ValueError, match="split leakage"):
        dataset(root)


def test_inconsistent_inventory_is_rejected(tmp_path):
    root = tmp_path / "data"
    shutil.copytree(FIXTURE, root)
    edit_json(root, "test_data/S0001.json", lambda row:
              row["initial_system_state"]["inventory"].update(remaining_by_sku={}))
    with pytest.raises(ValueError, match="exceeds initial inventory"):
        list(dataset(root).episode("S0001").observations())


def test_external_training_path_never_calls_legacy_generator(monkeypatch):
    import pac_planning.experiment as experiment

    def forbidden(*args, **kwargs):
        raise AssertionError("Legacy box generator must not be used")

    monkeypatch.setattr(experiment, "make_episode", forbidden)
    source = dataset()
    config = PlannerConfig(horizon=1, scenario_count=1, top_k=1)
    train = collect_groups(source.splits["train"][:1], config, max_steps=1, dataset=source)
    val = collect_groups(source.splits["val"], config, max_steps=1, dataset=source)
    model, _ = train_model(train, val, epochs=1, hidden=4)
    model.payload["dataset_contract"] = source.contract
    result = benchmark(source.splits["test"], config, model, dataset=source, max_steps=1)
    assert len(result["episodes"]) == 5
    assert all(r["placed_boxes"] == 1 for r in result["episodes"])
    assert all(r["truncated_for_smoke"] for r in result["episodes"])
    assert not {g["base_group"] for g in train} & {g["base_group"] for g in val}
