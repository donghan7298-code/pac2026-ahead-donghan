from __future__ import annotations

from collections import deque
from typing import Any, Deque, Dict, List, Tuple


class ContactForceAverager:
    """Simulation-time rolling average for noisy contact-force measurements."""

    def __init__(self, window_s: float) -> None:
        self.window_s = max(0.0, float(window_s))
        self._samples: Deque[
            Tuple[float, Dict[str, Any], Dict[str, Any]]
        ] = deque()

    def clear(self) -> None:
        self._samples.clear()

    def update(
        self,
        sim_time_s: float,
        load_map: Dict[str, Any],
        contact_graph: Dict[str, Any],
    ) -> None:
        self._samples.append((sim_time_s, load_map, contact_graph))
        cutoff = sim_time_s - self.window_s
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    def average_load_map(self) -> Dict[str, Any]:
        if not self._samples:
            return {
                "grid_size": 0,
                "force_n": [],
                "max_cell_force_n": 0.0,
                "total_normal_force_n": 0.0,
                "equivalent_supported_mass_kg": 0.0,
                "center_of_pressure_m": None,
                "averaging_samples": 0,
                "averaging_window_s": self.window_s,
            }

        latest = self._samples[-1][1]
        n = int(latest.get("grid_size", 0))
        if n <= 0:
            return latest

        grid = [[0.0 for _ in range(n)] for _ in range(n)]
        for _, load_map, _ in self._samples:
            src = load_map.get("force_n", [])
            if len(src) != n:
                continue
            for y in range(n):
                if len(src[y]) != n:
                    continue
                for x in range(n):
                    grid[y][x] += float(src[y][x])

        count = max(1, len(self._samples))
        for y in range(n):
            for x in range(n):
                grid[y][x] /= count

        total = sum(sum(row) for row in grid)

        # Grid-cell center of pressure.  This is used for display/metrics only.
        cop = None
        if total > 1e-12:
            cop = [0.0, 0.0, 0.002]  # precise XY is filled by caller if needed

        return {
            "grid_size": n,
            "force_n": grid,
            "max_cell_force_n": max((max(r) for r in grid), default=0.0),
            "total_normal_force_n": total,
            "equivalent_supported_mass_kg": total / 9.80665,
            "center_of_pressure_m": latest.get("center_of_pressure_m"),
            "averaging_samples": count,
            "averaging_window_s": self.window_s,
        }

    def average_per_box(self) -> Dict[str, Dict[str, float]]:
        sums: Dict[str, Dict[str, float]] = {}
        for _, _, graph in self._samples:
            per_box = graph.get("per_box", {})
            for box_id, d in per_box.items():
                s = sums.setdefault(
                    box_id,
                    {
                        "load_from_above_n": 0.0,
                        "support_force_below_n": 0.0,
                        "sample_count": 0.0,
                    },
                )
                s["load_from_above_n"] += float(
                    d.get("load_from_above_n", 0.0)
                )
                s["support_force_below_n"] += float(
                    d.get("support_force_below_n", 0.0)
                )
                s["sample_count"] += 1.0

        result: Dict[str, Dict[str, float]] = {}
        total_samples = max(1, len(self._samples))
        for box_id, s in sums.items():
            # Missing-contact samples count as zero force.
            result[box_id] = {
                "load_from_above_n_avg": (
                    s["load_from_above_n"] / total_samples
                ),
                "support_force_below_n_avg": (
                    s["support_force_below_n"] / total_samples
                ),
                "averaging_samples": float(total_samples),
            }
        return result
