from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument("candidate_config"),
        DeclareLaunchArgument("planner_config"),
        DeclareLaunchArgument("model_path", default_value=""),
        DeclareLaunchArgument("use_sim_time", default_value="true"),
        Node(package="pac_planning", executable="placement_planner_node",
             output="screen", parameters=[{
                 "candidate_config": LaunchConfiguration("candidate_config"),
                 "planner_config": LaunchConfiguration("planner_config"),
                 "model_path": LaunchConfiguration("model_path"),
                 "use_sim_time": LaunchConfiguration("use_sim_time"),
             }]),
    ])
