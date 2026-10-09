"""Standalone JSON/JSONL + exact top-view SVG demo; ROS is optional."""

import argparse
from dataclasses import replace
import html
import json
from pathlib import Path
import subprocess
from pac_common import plain
from pac_common.adapters import context_from_json, state_from_json
from .config import load_config
from .geometry import bounds, simulate_placement
from .planner import PlacementPlanner
from .reference_backend import ReferenceBackend


def scene_from_file(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("schema_version") != "pac-common-v0.2+planning-v1":
        raise ValueError("Unsupported fixture schema")
    state = state_from_json(data["state"])
    context = context_from_json(data["context"])
    return (
        data,
        state.inventory.tracked_boxes[data["current_box_id"]],
        state,
        context,
    )


def preview_svg(state, box, result):
    selected = result.ranked[0] if result.ranked else None
    after = (
        simulate_placement(state, box, selected).state if selected else state
    )
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1100" height="600" viewBox="0 0 1100 600">',
        '<rect width="1100" height="600" fill="#f3f6fa"/>',
        '<text x="40" y="45" font-family="sans-serif" font-size="25" fill="#152d48">AHEAD | Low-level placement planner 5-3 to 5-6</text>',
    ]
    for offset, snapshot, title in (
        (40, state, "ACTUAL snapshot"),
        (580, after, "PLANNED candidate | robot check required"),
    ):
        p = snapshot.pallet.size
        scale = min(440 / p.x, 400 / p.y)
        parts.append(
            f'<text x="{offset}" y="92" font-family="sans-serif" font-size="18">{title}</text>'
        )
        parts.append(
            f'<rect x="{offset}" y="120" width="{p.x * scale}" height="{p.y * scale}" fill="white" stroke="#64748b" stroke-width="2"/>'
        )
        for b in sorted(snapshot.pallet.boxes, key=lambda b: b.pose.z):
            lo, hi = bounds(b)
            color = "#8b5cf6" if b.box_id == box.box_id else "#4096b1"
            xx, yy = offset + lo[0] * scale, 120 + (p.y - hi[1]) * scale
            width, height = (hi[0] - lo[0]) * scale, (hi[1] - lo[1]) * scale
            parts.append(
                f'<rect x="{xx}" y="{yy}" width="{width}" height="{height}" fill="{color}" fill-opacity="0.7" stroke="white"/>'
            )
            parts.append(
                f'<text x="{xx + 4}" y="{yy + 18}" font-size="12" font-family="sans-serif">{html.escape(b.box_id)} z={lo[2]:.2f}m</text>'
            )
    d = result.diagnostics
    caption = f"Generated {d['generated_count']} | Valid {d['valid_count']} | Shared scenarios {d.get('completed_scenarios', 0)} | {d['planning_time_sec'] * 1000:.1f} ms"
    parts.append(
        f'<text x="40" y="560" font-family="sans-serif" font-size="17">{html.escape(caption)}</text>'
    )
    parts.append("</svg>")
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario", default="test_data/scenario_001_basic.json"
    )
    parser.add_argument("--config", default="config/default.yaml")
    parser.add_argument("--model")
    parser.add_argument(
        "--mode",
        default="ahead",
        choices=("ahead", "teacher", "ranking", "current", "greedy"),
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fixed-work", action="store_true")
    parser.add_argument("--output", default="runs/demo")
    args = parser.parse_args()
    data, box, state, context = scene_from_file(args.scenario)
    config = load_config(args.config)
    backend = ReferenceBackend(context, config)
    planner = PlacementPlanner(
        context=context,
        config=config,
        model_path=args.model,
        generate_candidates=backend.generate_candidates,
        validate_constraints=backend.validate_constraints,
    )
    result = planner.plan(
        box,
        state,
        seed=args.seed,
        mode=args.mode,
        use_time_budget=not args.fixed_work,
    )
    output = Path(args.output)
    output.mkdir(parents=True, exist_ok=True)
    git = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True
    )
    commit = (
        git.stdout.strip() if git.returncode == 0 else "UNCOMMITTED_WORKTREE"
    )
    record = {
        "schema_version": "pac-planning-log-v1",
        "run_id": f"demo-{args.seed}",
        "scenario_id": data["scenario_id"],
        "git_commit": commit,
        "timestamp": state.stamp_sec,
        "state_version": state.state_version,
        "box_id": box.box_id,
        "candidate_id": (
            result.ranked[0].candidate_id if result.ranked else None
        ),
        "constraint_result": "PASS" if result.ranked else "NO_VALID_CANDIDATE",
        "reject_codes": {
            k: [c.value for c in v.codes] for k, v in result.rejected.items()
        },
        "score": result.ranked[0].score if result.ranked else None,
        "score_detail": (
            plain(result.ranked[0].score_detail) if result.ranked else {}
        ),
        "robot_validation": "NOT_CHECKED",
        "execution_result": None,
        "planning_time_sec": result.diagnostics["planning_time_sec"],
        "seed": args.seed,
        "config": plain(config),
        "result": plain(result),
    }
    encoded = json.dumps(record, indent=2, ensure_ascii=False, allow_nan=False)
    (output / "result.json").write_text(encoded + "\n", encoding="utf-8")
    (output / "decisions.jsonl").write_text(
        json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    (output / "preview.svg").write_text(
        preview_svg(state, box, result), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "candidate": record["candidate_id"],
                "score": record["score"],
                "model_status": result.diagnostics.get("model_status"),
                "completed_scenarios": result.diagnostics.get(
                    "completed_scenarios"
                ),
                "planning_time_sec": record["planning_time_sec"],
                "output": str(output),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
