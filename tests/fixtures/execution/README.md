# 실행 계약 테스트의 원본 출처

이 디렉터리는 설명/좌표/URDF 조립 검사를 위한 공개 소스 fixture다.
ROS 또는 Gazebo 실행 결과를 저장한 파일이 아니다. 실제 arm xacro 확장,
mesh 로딩 및 URDF→SDF 변환은 Ubuntu/Humble/Fortress에서 별도 검사한다.

- ahead_workcell_v2_hdp160.sdf, vacuum_gripper_v1.urdf.xacro:
  https://github.com/dlwotjd1289-cloud/pac2026-ahead/tree/fa3115a903117eeb57009defd12a201d5290345d
- hdr50_22.urdf: Hyundai hdr_description의 robot xacro를 그대로 저장했다.
  https://github.com/hyundai-robotics/hdr_description/blob/f0265929e9dd7ba920cdd8126cd8e510bee1c292/urdf/robots/hdr50_22/hdr50_22.urdf.xacro
- hdr50_22.srdf.xacro:
  https://github.com/hyundai-robotics/hdr_ros2_driver/blob/08163cffa189db7b20da08900b09673fe1bca861/hdr_moveit_config/hdr50_22_moveit_config/config/hdr50_22.srdf.xacro

새 physical plugin은 Gazebo Fortress의 fixed DetachableJoint component 방식을 사용한다.
원본 stock-startup attachment를 재사용하지 않고 별도 plugin에서 detached 시작을 구현했다.
https://github.com/gazebosim/gz-sim/tree/ign-gazebo6/src/systems/detachable_joint

Humble MoveIt message 2.2.1의 ExecuteTrajectory goal에는 controller_names가 없다.
새 executor는 해당 field가 있는 schema에서만 설정하고, 그 외에는 launch에 설정한
MoveIt controller mapping을 사용한다. 이 소스 확인은 실제 ROS 실행 검증을 대체하지 않는다.
https://github.com/moveit/moveit_msgs/blob/2.2.1/action/ExecuteTrajectory.action

HDR fixture의 줄 끝 공백만 정리했다. live robot asset은 고정된 공식 submodule에서 읽는다.
