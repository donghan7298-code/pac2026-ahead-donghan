#!/usr/bin/env python3
"""Read-only Ubuntu/Humble/Fortress checks before launching the physical demo.

No dependency is installed and no robot or simulator is commanded by this tool.
PASS means prerequisites exist; it does not mean pick & place was validated.
"""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path)
    parser.add_argument('--live', action='store_true', help='also inspect the running ROS graph (read-only)')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    checks = []
    def check(name, ok, detail):
        checks.append(dict(name=name, passed=bool(ok), detail=detail))
    os_release = Path('/etc/os-release').read_text() if Path('/etc/os-release').exists() else ''
    check('ubuntu_22_04', 'ID=ubuntu' in os_release and 'VERSION_ID="22.04"' in os_release,
          'Ubuntu 22.04 is the target; this check does not install or change the OS')
    check('ros_humble_sourced', os.environ.get('ROS_DISTRO') == 'humble', os.environ.get('ROS_DISTRO', 'not sourced'))
    for tool in ('ros2', 'colcon', 'ign'):
        check(tool, shutil.which(tool) is not None, shutil.which(tool) or 'not installed/on PATH')
    for module in ('rclpy', 'xacro', 'moveit_msgs', 'ament_index_python'):
        check(module, importlib.util.find_spec(module) is not None, 'import availability only')
    if args.workspace:
        root = args.workspace.resolve()
        check('workspace_built', (root/'install/setup.bash').is_file(), str(root/'install/setup.bash'))
        packages = []
        for xml in (root/'src').glob('*/package.xml'):
            import xml.etree.ElementTree as ET
            packages.append(ET.parse(xml).getroot().findtext('name'))
        check('no_duplicate_packages', len(packages) == len(set(packages)), str(sorted(packages)))
    if importlib.util.find_spec('ament_index_python') is not None:
        from ament_index_python.packages import get_package_prefix, get_package_share_directory
        for package in ('pac_execution', 'pac_gazebo_grasp', 'hdr_description', 'hdr50_22_moveit_config',
                        'hdr_simulation_gz', 'pac_eoat', 'pac_simulation', 'gz_ros2_control', 'moveit_ros_move_group'):
            try:
                path = get_package_share_directory(package)
                check(package, True, path)
                if package == 'pac_gazebo_grasp':
                    library = Path(get_package_prefix(package))/'lib/libpac_confirmed_grasp.so'
                    check('compiled_grasp_plugin', library.is_file(), str(library))
            except Exception as error:
                check(package, False, str(error))
    if args.live and shutil.which('ros2'):
        for kind, expected in (
            ('action', ('/move_action', '/execute_trajectory')),
            ('service', ('/apply_planning_scene', '/compute_cartesian_path', '/pac/grasp/initialize')),
            ('topic', ('/clock', '/joint_states', '/tf', '/pac/gazebo_world_poses', '/pac/status'))):
            try:
                result = subprocess.run(['ros2', kind, 'list'], capture_output=True, text=True, timeout=12.)
                names = set(result.stdout.splitlines())
                for name in expected:
                    check(kind+':'+name, result.returncode == 0 and name in names, name)
            except Exception as error:
                check('live_'+kind, False, str(error))
    report = dict(scope='PREREQUISITES_ONLY', status='PASS' if all(c['passed'] for c in checks) else 'BLOCKED',
                  checks=checks, robot_execution_verified=False, physics_verified=False)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2)+'\n')
    print(json.dumps({'status': report['status'], 'failed': [c['name'] for c in checks if not c['passed']]}))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    sys.exit(main())
