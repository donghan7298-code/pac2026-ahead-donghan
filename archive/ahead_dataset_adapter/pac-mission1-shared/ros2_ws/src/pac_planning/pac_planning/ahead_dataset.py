"""AHEAD v1.2 JSON boundary for OFFLINE simulation, never a live State Manager.

Run the external generator in its own process. This module imports only this
project's pac_common, and never reads ground_truth or analysis CSVs. Observations
are released one at a time; the planner receives only the current snapshot.
"""

import argparse
from dataclasses import dataclass, replace
import hashlib
from itertools import islice
import json
from pathlib import Path
import re

import yaml
from pac_common import (
    BoxStatus, InventoryState, PlanningContext, Size3D, SkuSpec, SystemState, plain,
)
from pac_common.adapters import box_from_json, state_from_json
from pac_common.models import finite

UNITS = {"length": "m", "mass": "kg", "time": "s", "angle": "rad"}


def _read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _digest(value):
    raw = json.dumps(value, sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _yaml(path):
    value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("YAML must contain a mapping")
    return value


def _scenario_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise ValueError("Invalid scenario_id")
    return value


def _check_simulated(row, scenario_id):
    if (type(row.get("schema_version")) is not int
            or row["schema_version"] != 1
            or row.get("state_kind") != "SIMULATED"
            or row.get("scenario_id") != scenario_id):
        raise ValueError("Expected matching schema v1 SIMULATED data")


@dataclass(frozen=True)
class AheadEpisode:
    scenario_id: str
    family: str
    seed: int
    state: SystemState
    context: PlanningContext
    box_count: int
    dataset: "AheadDataset"

    def observations(self):
        """Simulator-only iterator; never pass the iterator to the planner."""
        path = self.dataset.checked_path(
            f"simulation_observations/{self.scenario_id}.jsonl"
        )
        seen = set()
        last_stamp = self.state.stamp_sec
        remaining = dict(self.state.inventory.remaining_by_sku)
        count = 0
        with path.open(encoding="utf-8") as stream:
            for index, line in enumerate(stream):
                row = json.loads(line)
                _check_simulated(row, self.scenario_id)
                if type(row.get("arrival_index")) is not int or row["arrival_index"] != index:
                    raise ValueError("Observation arrival_index must be sequential")
                box = box_from_json(row["observation"])
                if (box.source != "simulation_identity_observation"
                        or box.status != BoxStatus.DETECTED
                        or box.confidence != 1.0
                        or box.pose.frame_id != "conveyor"):
                    raise ValueError("Only complete identity observations are supported")
                if box.box_id in seen or box.box_id.startswith(("__future__", "__probe__")):
                    raise ValueError("Duplicate or reserved box_id")
                if box.stamp_sec < last_stamp:
                    raise ValueError("Observation timestamps moved backwards")
                spec = self.context.catalog.get(box.sku_id)
                if (spec is None or box.size != spec.size
                        or box.allowed_yaws_rad != spec.allowed_yaws_rad):
                    raise ValueError("Observation disagrees with the supplied catalog")
                if not 0 < box.weight_kg <= spec.weight_kg:
                    raise ValueError("Observed weight exceeds the configured prior bound")
                if remaining.get(box.sku_id, 0) <= 0:
                    raise ValueError("Observed SKU exceeds initial inventory")
                remaining[box.sku_id] -= 1
                seen.add(box.box_id)
                last_stamp = box.stamp_sec
                count += 1
                # An explicit simulation-only measurement gate. Do not promote
                # real DETECTED boxes here: they need the perception/measurement stage.
                yield replace(box, status=BoxStatus.READY_FOR_PICK,
                              source="AHEAD_IDENTITY_MEASUREMENT_SIMULATION")
        if count != self.box_count or any(remaining.values()):
            raise ValueError("Observation count disagrees with initial inventory")


class AheadDataset:
    def __init__(self, root, generator_config, adapter_config):
        self.root = Path(root)
        self.config = _yaml(generator_config)
        self.policy = _yaml(adapter_config)
        self.manifest = _read_json(self.root / "manifest.json")
        self.hashes = _read_json(self.root / "file_hashes.json")
        if (self.manifest.get("schema_version") != 1
                or self.manifest.get("generator_version") != "1.2.0"
                or self.manifest.get("units") != UNITS):
            raise ValueError("Unsupported generator version, schema, or SI units")
        if self.config.get("schema_version") != 1:
            raise ValueError("Unsupported generator config")
        if (self.policy.get("schema_version") != 1
                or self.policy.get("scope") != "OFFLINE_SIMULATION"
                or self.policy.get("height_reference") not in ("above_pallet", "floor")
                or self.policy.get("unseen_weight_policy") != "upper_bound"):
            raise ValueError("Explicit offline adapter policy is required")
        if (self.policy.get("allow_identity_measurement") is not True
                or self.config["generation"]["observation"]["mode"] != "identity"):
            raise ValueError("Identity measurement must be explicitly enabled")
        default_capacity = self.policy["default_top_load_capacity_n"]
        finite(default_capacity, "default top capacity", 0)
        overrides = self.policy.get("top_load_capacity_n_by_sku", {})
        self.catalog = {}
        # Stress weights in the source generator may exceed an individual SKU's
        # nominal range. Include that global range, even in conservative forecasts.
        rows = self.config["sku_catalog"]
        stress_max = self.config["generation"].get(
            "stress_weight_range_kg", [0, max(r["weight_max_kg"] for r in rows)]
        )[1]
        finite(stress_max, "stress weight upper bound", 0)
        for row in rows:
            sku = row["sku_id"]
            if sku in self.catalog:
                raise ValueError("Duplicate SKU in generator config")
            self.catalog[sku] = SkuSpec(
                sku, Size3D(**row["size_m"]),
                max(row["weight_max_kg"], stress_max),
                tuple(row["allowed_yaws_rad"]),
                overrides.get(sku, default_capacity),
            )
        if set(overrides) - set(self.catalog):
            raise ValueError("Capacity override names an unknown SKU")
        self.contract = {
            "adapter": "ahead-dataset-v1",
            "generator_version": self.manifest["generator_version"],
            "generator_config_sha256": _digest(self.config),
            "adapter_config_sha256": _digest(self.policy),
            "future_weights": "upper_bound; current weights are observed",
            "observation": "identity; sequential release; no future preview",
        }
        self.splits = _read_json(self.root / "splits.json")
        if set(self.splits) != {"train", "val", "test"}:
            raise ValueError("Expected train/val/test splits")
        all_ids = []
        for ids in self.splits.values():
            if not isinstance(ids, list):
                raise ValueError("Each split must contain a list of scenario IDs")
            all_ids.extend(_scenario_id(s) for s in ids)
        if len(set(all_ids)) != len(all_ids):
            raise ValueError("Dataset split leakage: duplicate scenario ID")
        if len(all_ids) != self.manifest["scenario_count"]:
            raise ValueError("Split count disagrees with manifest")

    def checked_path(self, relative):
        path = self.root / relative
        expected = self.hashes.get(relative)
        if expected is None or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError("Missing or mismatched source hash: " + relative)
        return path

    def episode(self, scenario_id, model=None):
        scenario_id = _scenario_id(scenario_id)
        row = _read_json(self.checked_path(f"test_data/{scenario_id}.json"))
        _check_simulated(row, scenario_id)
        if row.get("generator_version") != self.manifest["generator_version"]:
            raise ValueError("Scenario generator version disagrees with manifest")
        if "arrival_events" in row or "ground_truth" in row:
            raise ValueError("Planner fixture must not contain future ground truth")
        state = state_from_json(row["initial_system_state"])
        if state.pallet.boxes or state.inventory.tracked_boxes:
            raise ValueError("v1 adapter supports only empty initial pallets/inventory")
        cfg = self.config["pallet"]
        expected = Size3D(cfg["x_m"], cfg["y_m"], cfg["physical_height_m"])
        if state.pallet.size != expected:
            raise ValueError("Generator config does not match pallet dimensions")
        limits = row["constraints"]
        if (limits["max_height_m"] != cfg["max_height_m"]
                or limits.get("max_load_kg") != cfg.get("max_load_kg")):
            raise ValueError("Generator config does not match scenario constraints")
        height = limits["max_height_m"]
        if self.policy["height_reference"] == "floor":
            height -= state.pallet.size.z
        # Local copy only. Upstream size.z is physical thickness, while this
        # planner's reference geometry consumes an available cargo envelope.
        state = replace(state, pallet=replace(
            state.pallet, size=Size3D(state.pallet.size.x, state.pallet.size.y, height)
        ))
        if set(state.inventory.remaining_by_sku) - set(self.catalog):
            raise ValueError("Inventory contains an unknown SKU")
        max_weight = limits.get("max_load_kg")
        if max_weight is None:
            max_weight = self.policy.get("pallet_max_weight_kg")
        finite(max_weight, "explicit pallet load limit", 0)
        compatible = model is not None and getattr(model, "payload", {}).get(
            "dataset_contract") == self.contract
        context = PlanningContext(
            self.catalog, max_weight,
            distribution_status="IN_DISTRIBUTION" if compatible else "OOD",
        )
        seed = row["seed"]
        if type(seed) is not int:
            raise ValueError("Scenario seed must be an integer")
        return AheadEpisode(scenario_id, row["scenario_family"], seed, state,
                            context, sum(state.inventory.remaining_by_sku.values()), self)


def observe_ahead_box(state, box):
    """Offline State Manager: add one measured box and debit unseen inventory once."""
    if (box.status != BoxStatus.READY_FOR_PICK
            or box.source != "AHEAD_IDENTITY_MEASUREMENT_SIMULATION"):
        raise ValueError("Expected an adapter-validated identity measurement")
    if box.box_id in state.inventory.tracked_boxes or any(
        b.box_id == box.box_id for b in state.pallet.boxes
    ):
        raise ValueError("Box was already observed")
    if box.stamp_sec < state.stamp_sec:
        raise ValueError("Observation timestamp moved backwards")
    remaining = dict(state.inventory.remaining_by_sku)
    if remaining.get(box.sku_id, 0) <= 0:
        raise ValueError("Observation exceeds remaining inventory")
    remaining[box.sku_id] -= 1
    tracked = dict(state.inventory.tracked_boxes)
    tracked[box.box_id] = box
    return box, replace(
        state, state_version=state.state_version + 1, stamp_sec=box.stamp_sec,
        inventory=InventoryState(tracked, remaining),
    )


def main():
    from .config import load_config
    from .experiment import make_planner, write_json
    from .geometry import simulate_placement
    from .model import DualHeadRanker

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--generator-config", required=True)
    parser.add_argument("--adapter-config", required=True)
    parser.add_argument("--planner-config", default="config/default.yaml")
    parser.add_argument("--scenario", default="S0001")
    parser.add_argument("--max-steps", type=int, default=3)
    parser.add_argument("--model")
    parser.add_argument("--output", default="runs/ahead_dataset_smoke.json")
    args = parser.parse_args()
    if args.max_steps < 1:
        parser.error("--max-steps must be positive")
    dataset = AheadDataset(args.dataset, args.generator_config, args.adapter_config)
    model = DualHeadRanker.load(args.model) if args.model else None
    episode = dataset.episode(args.scenario, model=model)
    planner = make_planner(episode.context, load_config(args.planner_config), model)
    state, decisions = episode.state, []
    for box in islice(episode.observations(), args.max_steps):
        box, state = observe_ahead_box(state, box)
        result = planner.plan(box, state, seed=episode.seed + state.state_version,
                              use_time_budget=False)
        decisions.append({"box_id": box.box_id, "weight_kg": box.weight_kg,
                          "result": plain(result)})
        if not result.ranked:
            break
        state = simulate_placement(state, box, result.ranked[0]).state
    record = {
        "scope": "OFFLINE_SIMULATION; reference geometry; robot NOT_CHECKED",
        "dataset_contract": dataset.contract, "adapter_policy": dataset.policy,
        "scenario_id": episode.scenario_id, "requested_boxes": episode.box_count,
        "max_steps": args.max_steps, "placed_boxes": len(state.pallet.boxes),
        "decisions": decisions,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    write_json(args.output, record)
    print(json.dumps({k: v for k, v in record.items() if k != "decisions"}))


if __name__ == "__main__":
    main()
