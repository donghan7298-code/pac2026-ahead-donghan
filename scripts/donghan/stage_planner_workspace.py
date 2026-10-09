#!/usr/bin/env python3
"""Create a NEW planner-only colcon workspace, avoiding duplicate package names."""
import argparse
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--team-root", type=Path, required=True,
                        help="checkout containing taehyeon's ros2_ws/src/pac_candidates")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-highlevel", action="store_true",
                        help="also stage pac_highlevel for the stage-4 runtime handoff")
    parser.add_argument("--include-runtime", action="store_true",
                        help="also stage pac_highlevel, pac_robot_check and pac_runtime")
    args = parser.parse_args()
    own = Path(__file__).resolve().parents[2]
    sources = {n: own / "ros2_ws/src" / n for n in
               ("pac_common", "pac_planning", "pac_planning_interfaces")}
    sources["pac_candidates"] = args.team_root.resolve() / "ros2_ws/src/pac_candidates"
    if args.include_highlevel or args.include_runtime:
        sources["pac_highlevel"] = args.team_root.resolve() / "ros2_ws/src/pac_highlevel"
    if args.include_runtime:
        for name in ("pac_robot_check", "pac_runtime"):
            sources[name] = args.team_root.resolve() / "ros2_ws/src" / name
    for name, source in sources.items():
        if not (source / "package.xml").is_file():
            parser.error("Missing source for " + name + ": " + str(source))
    target = args.output.resolve()
    if target.exists() and any(target.iterdir()):
        parser.error("Output must be new or empty; existing files are never replaced")
    (target / "src").mkdir(parents=True, exist_ok=True)
    for name, source in sources.items():
        (target / "src" / name).symlink_to(source, target_is_directory=True)
    print("Planner workspace: " + str(target))
    if args.include_runtime:
        print("Runtime and analytical robot check are included; MoveIt/controller are not.")
    else:
        print("No robot backend, simulator, or ACTUAL State Manager is included.")


if __name__ == "__main__":
    main()
