#!/usr/bin/env python3
"""Stage a NEW measured-demo workspace, with exactly one copy of each package."""
import argparse
import json
from pathlib import Path
import xml.etree.ElementTree as ET


def find_packages(root):
    sources = {}
    for xml in root.rglob('package.xml'):
        if set(xml.relative_to(root).parts) & {'.git', 'build', 'install', 'log'}:
            continue
        name = ET.parse(xml).getroot().findtext('name')
        sources.setdefault(name, set()).add(xml.parent.resolve())
    return sources


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--team-root', type=Path, required=True)
    parser.add_argument('--ahead-root', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    own, team, ahead = Path(__file__).resolve().parents[2], args.team_root.resolve(), args.ahead_root.resolve()
    sources = {name: own/'ros2_ws/src'/name for name in
               ('pac_common', 'pac_planning', 'pac_planning_interfaces', 'pac_execution', 'pac_gazebo_grasp')}
    sources.update({name: team/'ros2_ws/src'/name for name in
                    ('pac_candidates', 'pac_highlevel', 'pac_runtime', 'pac_robot_check')})
    assets = find_packages(ahead)
    for name in ('pac_eoat', 'pac_simulation', 'hdr_description', 'hdr_simulation_gz', 'hdr50_22_moveit_config'):
        matches = assets.get(name, set())
        if len(matches) != 1:
            parser.error(f'Expected one source for {name}; found {len(matches)}. '
                         'Initialize AHEAD Hyundai submodules (git submodule update --init --recursive). '
                         'Do not copy multiple pac_common/pac_planning packages into this workspace.')
        sources[name] = next(iter(matches))
    for name, source in sources.items():
        if not (source/'package.xml').is_file() or ET.parse(source/'package.xml').getroot().findtext('name') != name:
            parser.error('Missing/mismatched package: '+str(source))
    target = args.output.resolve()
    if target.exists() and any(target.iterdir()):
        parser.error('Output must be new or empty; no existing workspace is replaced')
    (target/'src').mkdir(parents=True, exist_ok=True)
    for name, source in sorted(sources.items()):
        (target/'src'/name).symlink_to(source, target_is_directory=True)
    (target/'sources.json').write_text(json.dumps({k: str(v) for k, v in sorted(sources.items())}, indent=2)+'\n')
    print(json.dumps({'workspace': str(target), 'packages': len(sources),
                      'pac_common_owner': 'donghan', 'pac_planning_owner': 'donghan',
                      'robot_execution_verified': False}))


if __name__ == '__main__':
    main()
