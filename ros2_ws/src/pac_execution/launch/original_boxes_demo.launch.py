"""One launch for the separate, measured original-box acceptance demo.

Requires installed pinned Hyundai packages, the AHEAD pac_eoat/pac_simulation
assets, patched team runtime packages, MoveIt and Gazebo Fortress. It builds a
single URDF for Gazebo/TF/MoveIt; it does not run the old gazebo_driver.
"""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET

from ament_index_python.packages import get_package_prefix, get_package_share_directory
from launch import LaunchDescription
from launch.actions import (DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction,
                            SetEnvironmentVariable, TimerAction)
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def setup(context):
    import yaml
    from pac_execution.assets import build_descriptions
    from pac_execution.contract import ExecutionFault

    def arg(name):
        return LaunchConfiguration(name).perform(context)

    own, team = Path(arg('planner_root')).resolve(), Path(arg('team_root')).resolve()
    demo = Path(arg('demo_dir')).resolve() if arg('demo_dir') else own/'config/demo_original6'
    session = uuid.uuid4().hex
    fixture_path, order_path = demo/'fixture.json', demo/'order.json'
    fixture = json.loads(fixture_path.read_text())
    if fixture['pallet']['size_m'][:2] != [1.2, 1.0]:
        raise ExecutionFault('The actual AHEAD pallet is 1.2 x 1.0 m')
    hdr = Path(get_package_share_directory('hdr_description'))
    moveit = Path(get_package_share_directory('hdr50_22_moveit_config'))
    eoat = Path(get_package_share_directory('pac_eoat'))
    sim = Path(get_package_share_directory('pac_simulation'))
    gz = Path(get_package_share_directory('ros_gz_sim'))
    # Package URIs need the parent share directory; also retain direct asset
    # roots and existing resource paths for the supported Gazebo resolver.
    resources = os.pathsep.join(map(str, (hdr.parent, hdr, eoat.parent, eoat, sim.parent, sim)))
    resources += os.pathsep+os.environ.get('IGN_GAZEBO_RESOURCE_PATH', '')
    control = Path(get_package_share_directory('hdr_simulation_gz'))/'config/hdr_controllers.yaml'
    world = sim/'worlds/ahead_workcell_v2_hdp160.sdf'
    urdf, srdf = build_descriptions(hdr, eoat, moveit, control, fixture['boxes'],
                                     fixture['base_position_world_m'], 'ahead_demo_robot')
    # Gazebo-specific shape/joint validity is checked by the actual Fortress
    # parser on the target machine before anything is spawned.
    with tempfile.TemporaryDirectory(prefix='ahead-urdf-') as temp:
        path = Path(temp)/'robot.urdf'
        path.write_text(urdf)
        parsed = subprocess.run(['ign', 'sdf', '-p', str(path)], capture_output=True, text=True, timeout=20.,
                                env={**os.environ, 'IGN_GAZEBO_RESOURCE_PATH': resources})
    if parsed.returncode != 0:
        raise ExecutionFault('Gazebo rejected the assembled URDF: '+parsed.stderr[-1200:])
    sdf = ET.fromstring(parsed.stdout)
    model = sdf.find('model')
    if model is None or model.find("link[@name='vacuum_tool_base']") is None:
        raise ExecutionFault('Gazebo conversion lost the physical EOAT link')
    if not {f'j{i}' for i in range(1, 7)} <= {j.get('name') for j in model.findall('joint')}:
        raise ExecutionFault('Gazebo conversion lost controlled robot joints')
    self_collision = model.find('self_collide')
    if self_collision is None:
        self_collision = ET.SubElement(model, 'self_collide')
    self_collision.text = 'true'
    plugins = [p for p in model.findall('plugin') if p.get('name') == 'pac_gazebo_grasp::ConfirmedGrasp']
    if len(plugins) != len(fixture['boxes']):
        raise ExecutionFault('Gazebo conversion lost the physical attachment plugins')
    robot_sdf = ET.tostring(sdf, encoding='unicode')

    def config(file):
        return yaml.safe_load((moveit/'config'/file).read_text())

    parameters = dict(robot_description=ParameterValue(urdf, value_type=str),
                      robot_description_semantic=ParameterValue(srdf, value_type=str),
                      robot_description_kinematics=config('kinematics.yaml'),
                      robot_description_planning=config('joint_limits.yaml'),
                      planning_pipelines=['ompl'], default_planning_pipeline='ompl',
                      ompl={'planning_plugin': 'ompl_interface/OMPLPlanner', **config('ompl_planning.yaml')},
                      moveit_controller_manager='moveit_simple_controller_manager/MoveItSimpleControllerManager',
                      moveit_simple_controller_manager=config('moveit_controllers.yaml'),
                      use_sim_time=True, publish_robot_description=True, publish_robot_description_semantic=True,
                      publish_planning_scene=True, publish_geometry_updates=True,
                      publish_state_updates=True, publish_transforms_updates=True,
                      moveit_manage_controllers=False,
                      **{'trajectory_execution.allowed_execution_duration_scaling': 1.5,
                         'trajectory_execution.allowed_goal_duration_margin': 2.,
                         'trajectory_execution.allowed_start_tolerance': .02,
                         'trajectory_execution.execution_duration_monitoring': True})
    runtime = dict(order_file=str(order_path), candidates_config=str(demo/'candidates_demo.yaml'),
                   highlevel_config=str(team/'config/taehyeon/highlevel.yaml'),
                   runtime_config=str(team/'config/taehyeon/runtime.yaml'),
                   robot_config=str(demo/'robot_check_demo.yaml'),
                   policy='rule', ranker=arg('ranker'), ranker_model_path=arg('model_path'),
                   ranker_config=arg('ranker_config'), use_sim_time=True)
    bridge_args = ['/clock@rosgraph_msgs/msg/Clock[ignition.msgs.Clock',
                   '/world/ahead_workcell_v2/pose/info@tf2_msgs/msg/TFMessage[ignition.msgs.Pose_V']
    for box in fixture['boxes']:
        base = '/pac/grasp/'+box['box_id']
        bridge_args.extend([base+'/attach@std_msgs/msg/Empty]ignition.msgs.Empty',
                            base+'/detach@std_msgs/msg/Empty]ignition.msgs.Empty',
                            base+'/state@std_msgs/msg/String[ignition.msgs.StringMsg'])
    pedestal = ('<sdf version="1.8"><model name="robot_pedestal"><static>true</static><pose>1.35 .15 .25 0 0 0</pose>'
                '<link name="link"><collision name="c"><geometry><box><size>.6 .6 .5</size></box></geometry></collision>'
                '<visual name="v"><geometry><box><size>.6 .6 .5</size></box></geometry></visual></link></model></sdf>')
    actions = [
        SetEnvironmentVariable('IGN_GAZEBO_RESOURCE_PATH', resources),
        SetEnvironmentVariable('IGN_GAZEBO_SYSTEM_PLUGIN_PATH',
            str(Path(get_package_prefix('pac_gazebo_grasp'))/'lib')+os.pathsep+
            os.environ.get('IGN_GAZEBO_SYSTEM_PLUGIN_PATH', '')),
        IncludeLaunchDescription(PythonLaunchDescriptionSource(str(gz/'launch/gz_sim.launch.py')),
                                 launch_arguments={'gz_args': '-r -v 3 '+str(world)}.items()),
        Node(package='robot_state_publisher', executable='robot_state_publisher', output='screen',
             parameters=[{'robot_description': ParameterValue(urdf, value_type=str), 'use_sim_time': True}]),
        Node(package='ros_gz_bridge', executable='parameter_bridge', output='screen', arguments=bridge_args,
             remappings=[('/world/ahead_workcell_v2/pose/info', '/pac/gazebo_world_poses')]),
        Node(package='ros_gz_sim', executable='create', output='screen',
             arguments=['-string', pedestal, '-name', 'robot_pedestal', '-allow_renaming', 'false']),
        TimerAction(period=2., actions=[Node(package='ros_gz_sim', executable='create', output='screen',
                    arguments=['-string', robot_sdf, '-name', 'ahead_demo_robot', '-allow_renaming', 'false'])]),
        Node(package='controller_manager', executable='spawner', output='screen',
             arguments=['joint_state_broadcaster', '--controller-manager-timeout', '60']),
        Node(package='controller_manager', executable='spawner', output='screen',
             arguments=['joint_trajectory_controller', '--controller-manager-timeout', '60']),
        Node(package='moveit_ros_move_group', executable='move_group', output='screen', parameters=[parameters]),
        Node(package='pac_execution', executable='gazebo_grasp', output='screen',
             parameters=[{'fixture_file': str(fixture_path), 'use_sim_time': True}]),
        Node(package='pac_execution', executable='verified_runtime', output='screen', parameters=[runtime]),
        Node(package='pac_execution', executable='moveit_executor', output='screen', parameters=[{
             'fixture_file': str(fixture_path), 'world_sdf': str(world),
             'candidates_config': str(team/'config/taehyeon/candidates.yaml'),
             'trace_file': str(demo/'execution_trace.jsonl'),
             'demo_session_id': session,
             'execute_enabled': arg('execute_enabled').lower() == 'true', 'use_sim_time': True}]),
        Node(package='pac_execution', executable='gazebo_box_source', output='screen',
             parameters=[{'fixture_file': str(fixture_path), 'use_sim_time': True,
                          'demo_session_id': session, 'completion_file': str(demo/'demo_complete.json')}]),
    ]
    if arg('rviz').lower() == 'true':
        actions.append(Node(package='rviz2', executable='rviz2', output='screen', parameters=[parameters]))
    return actions


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('planner_root', description='Donghan project root with config/demo_original6'),
        DeclareLaunchArgument('team_root', description='Patched team 81d0333 source root'),
        DeclareLaunchArgument('demo_dir', default_value=''),
        DeclareLaunchArgument('execute_enabled', default_value='false'),
        DeclareLaunchArgument('rviz', default_value='true'),
        DeclareLaunchArgument('ranker', default_value='donghan'),
        DeclareLaunchArgument('model_path', default_value=''),
        DeclareLaunchArgument('ranker_config', default_value=''),
        OpaqueFunction(function=setup),
    ])
