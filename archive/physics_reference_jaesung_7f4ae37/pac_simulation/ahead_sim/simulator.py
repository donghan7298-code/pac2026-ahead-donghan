from __future__ import annotations

from typing import Any, Dict, List, Optional

from .contact_graph import build_contact_graph
from .force_window import ContactForceAverager
from .metrics import compute_metrics
from .models import BoxSpec, SimulatorConfig
from .strength import evaluate_box_compression
from .world import BulletPalletWorld


class AheadLiveSimulator:
    """Always-on rigid-body execution world for selected AHEAD placements."""

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self.world = BulletPalletWorld(config)
        self.force_averager = ContactForceAverager(
            config.contact_force_average_window_s
        )
        self.sequence_index = 0
        self.demo_sequence = self._build_demo_sequence()

    def reset(self) -> None:
        self.world.reset()
        self.force_averager.clear()
        self.sequence_index = 0

    def step(self, count: int = 1) -> None:
        self.world.step(count)

    def place_box(self, spec: BoxSpec) -> int:
        return self.world.add_box(spec)

    def place_mapping(self, data: Dict[str, Any]) -> int:
        return self.place_box(BoxSpec.from_mapping(data))

    def add_next_demo_box(self) -> Optional[BoxSpec]:
        if self.sequence_index >= len(self.demo_sequence):
            return None
        spec = self.demo_sequence[self.sequence_index]
        self.place_box(spec)
        self.sequence_index += 1
        return spec

    def snapshot(self) -> Dict[str, Any]:
        boxes = self.world.snapshot_boxes()
        box_map = {str(b["id"]): b for b in boxes}
        contacts = self.world.contacts()
        pallet_contacts = self.world.pallet_contacts()

        metrics = compute_metrics(
            boxes,
            self.config.pallet,
            pallet_contacts,
            self.config.load_grid_size,
        )
        graph = build_contact_graph(
            contacts,
            box_map,
            self.config.contact_display_min_force_n,
        )

        # Keep the instantaneous values, but use a rolling average for load
        # reporting and compression evaluation.
        instant_load_map = metrics["load_map"]
        self.force_averager.update(
            self.world.simulation_time_s,
            instant_load_map,
            graph,
        )

        avg_load_map = self.force_averager.average_load_map()
        avg_per_box = self.force_averager.average_per_box()

        metrics["load_map_instant"] = instant_load_map
        metrics["load_map"] = avg_load_map

        for box_id, d in graph.get("per_box", {}).items():
            d.update(avg_per_box.get(box_id, {}))

        strength = evaluate_box_compression(boxes, avg_per_box)

        # Make strength data directly accessible in each box for the viewer.
        for b in boxes:
            b["strength"] = strength["per_box"].get(
                str(b["id"]),
                {
                    "status": "UNKNOWN",
                    "load_from_above_n_avg": 0.0,
                    "max_top_load_n": None,
                    "utilization": None,
                },
            )

        box_body_friction, pallet_body_friction = (
            self.config.physics.bullet_body_frictions()
        )

        return {
            "type": "state",
            "simulation_time_s": self.world.simulation_time_s,
            "pallet": self.config.pallet.as_dict(),
            "physics": {
                "gravity_m_s2": self.config.physics.gravity_m_s2,
                "physics_hz": self.config.physics.physics_hz,
                "always_on": True,
                "target_box_box_friction": (
                    self.config.physics.target_box_box_friction
                ),
                "target_box_pallet_friction": (
                    self.config.physics.target_box_pallet_friction
                ),
                "bullet_box_body_friction": box_body_friction,
                "bullet_pallet_body_friction": pallet_body_friction,
            },
            "boxes": boxes,
            "metrics": metrics,
            "contact_graph": graph,
            "strength": strength,
            "demo": {
                "next_index": self.sequence_index,
                "total": len(self.demo_sequence),
                "complete": self.sequence_index >= len(self.demo_sequence),
            },
        }

    def close(self) -> None:
        self.world.close()

    def _build_demo_sequence(self) -> List[BoxSpec]:
        return [
            BoxSpec(
                "B001", (0.40, 0.30, 0.20), 12.0,
                (-0.25, +0.25, 0.10), color="#b97843", source="demo"
            ),
            BoxSpec(
                "B002", (0.40, 0.30, 0.20), 8.0,
                (+0.25, +0.25, 0.10), color="#c98b52", source="demo"
            ),
            BoxSpec(
                "B003", (0.40, 0.30, 0.20), 10.0,
                (-0.25, -0.25, 0.10), color="#a96739", source="demo"
            ),
            BoxSpec(
                "B004", (0.40, 0.30, 0.20), 6.0,
                (+0.25, -0.25, 0.10), color="#d29961", source="demo"
            ),
            BoxSpec(
                "B005", (0.40, 0.30, 0.20), 5.0,
                (-0.25, +0.25, 0.30), color="#daa16d", source="demo"
            ),
            BoxSpec(
                "B006_UNSTABLE", (0.40, 0.30, 0.20), 9.0,
                (+0.52, +0.25, 0.30), color="#f4c65d",
                source="demo_unstable"
            ),
        ]
