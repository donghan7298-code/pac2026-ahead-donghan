cmake_minimum_required(VERSION 3.10.2)
project(pac_gazebo_grasp)
find_package(ament_cmake REQUIRED)
find_package(ignition-gazebo6 REQUIRED)
find_package(ignition-plugin1 REQUIRED COMPONENTS register)
find_package(ignition-transport11 REQUIRED)
add_library(pac_confirmed_grasp SHARED src/confirmed_grasp.cpp)
target_compile_features(pac_confirmed_grasp PRIVATE cxx_std_17)
target_compile_options(pac_confirmed_grasp PRIVATE -Wall -Wextra -Wpedantic)
target_link_libraries(pac_confirmed_grasp PRIVATE
  ignition-gazebo6::ignition-gazebo6
  ignition-plugin1::ignition-plugin1
  ignition-transport11::ignition-transport11)
install(TARGETS pac_confirmed_grasp LIBRARY DESTINATION lib)
ament_package()
