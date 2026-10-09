from glob import glob
from setuptools import setup

setup(name='pac_execution', version='0.3.0', packages=['pac_execution'],
      data_files=[('share/ament_index/resource_index/packages', ['resource/pac_execution']),
                  ('share/pac_execution', ['package.xml']),
                  ('share/pac_execution/launch', glob('launch/*.launch.py'))],
      install_requires=['setuptools', 'PyYAML'], zip_safe=True,
      maintainer='Donghan', maintainer_email='donghan@example.invalid',
      description='Confirmed planning, execution, grasp and observed placement.', license='Apache-2.0',
      entry_points={'console_scripts': [
          'verified_runtime = pac_execution.runtime_node:main',
          'moveit_executor = pac_execution.executor_node:main',
          'gazebo_grasp = pac_execution.grasp_node:main',
          'gazebo_box_source = pac_execution.source_node:main',
      ]})
