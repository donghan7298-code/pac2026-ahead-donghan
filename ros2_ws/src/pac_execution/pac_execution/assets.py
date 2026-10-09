"""Assemble a separate demo description from installed team/Hyundai assets.

The original files stay untouched. The same generated URDF is used by Gazebo,
robot_state_publisher and MoveIt, including the pedestal offset and EOAT.
"""

from copy import deepcopy
from itertools import combinations
from pathlib import Path
import math
import tempfile
import xml.etree.ElementTree as ET

from .contract import ExecutionFault, identity, multiply, rotate, vector


def rpy_quaternion(rpy):
    r, p, y = (v/2 for v in rpy)
    return (math.sin(r)*math.cos(p)*math.cos(y)-math.cos(r)*math.sin(p)*math.sin(y),
            math.cos(r)*math.sin(p)*math.cos(y)+math.sin(r)*math.cos(p)*math.sin(y),
            math.cos(r)*math.cos(p)*math.sin(y)-math.sin(r)*math.sin(p)*math.cos(y),
            math.cos(r)*math.cos(p)*math.cos(y)+math.sin(r)*math.sin(p)*math.sin(y))


def pose_of(element):
    text = element.findtext('pose', '0 0 0 0 0 0')
    values = vector([float(v) for v in text.split()], 6, 'SDF pose')
    if element.find('pose') is not None and element.find('pose').get('relative_to'):
        raise ExecutionFault('SDF relative_to needs a frame resolver')
    return values[:3], rpy_quaternion(values[3:])


def compose(a, b):
    p, q = a
    pp, qq = b
    offset = rotate(q, pp)
    return tuple(x+y for x, y in zip(p, offset)), multiply(q, qq)


def world_obstacles(world_xml):
    """Use collision shapes, never visual-only rails/rollers as obstacles."""
    root = ET.fromstring(world_xml)
    world = root.find('world')
    if world is None:
        raise ExecutionFault('Expected one SDF world')
    result = []
    for model in world.findall('model'):
        if model.findtext('static') != 'true':
            continue
        for link in model.findall('link'):
            link_pose = compose(pose_of(model), pose_of(link))
            for collision in link.findall('collision'):
                position, orientation = compose(link_pose, pose_of(collision))
                geometry = collision.find('geometry')
                if geometry.find('box') is not None:
                    kind = 'box'
                    dimensions = [float(x) for x in geometry.findtext('box/size').split()]
                elif geometry.find('cylinder') is not None:
                    kind = 'cylinder'
                    dimensions = [float(geometry.findtext('cylinder/length')),
                                  float(geometry.findtext('cylinder/radius'))]
                else:
                    raise ExecutionFault('Unmapped world collision geometry: '+model.get('name'))
                result.append(dict(id='/'.join((model.get('name'), link.get('name'), collision.get('name'))),
                                   kind=kind, dimensions=dimensions, position=position,
                                   orientation=orientation))
    return world.get('name'), result


def add_inertia(link, mass):
    collision = link.find('collision')
    if collision is None or link.find('inertial') is not None:
        raise ExecutionFault('EOAT inertia input differs from the reviewed geometry')
    geometry = collision.find('geometry')
    if geometry.find('box') is not None:
        x, y, z = (float(v) for v in geometry.find('box').get('size').split())
        i = (mass*(y*y+z*z)/12, mass*(x*x+z*z)/12, mass*(x*x+y*y)/12)
    elif geometry.find('cylinder') is not None:
        cylinder = geometry.find('cylinder')
        radius, length = float(cylinder.get('radius')), float(cylinder.get('length'))
        side = mass*(3*radius*radius+length*length)/12
        i = (side, side, mass*radius*radius/2)
    else:
        raise ExecutionFault('Unsupported EOAT inertia geometry')
    inertia = ET.SubElement(link, 'inertial')
    ET.SubElement(inertia, 'mass', value=str(mass))
    ET.SubElement(inertia, 'origin', xyz='0 0 0', rpy='0 0 0')
    ET.SubElement(inertia, 'inertia', ixx=str(i[0]), iyy=str(i[1]), izz=str(i[2]),
                  ixy='0', ixz='0', iyz='0')


def assemble_urdf(arm_xml, eoat_xml, boxes, base_position, name):
    arm, eoat = ET.fromstring(arm_xml), ET.fromstring(eoat_xml)
    arm.set('name', identity(name))
    world_joint = next((j for j in arm.findall('joint')
                        if j.find('parent').get('link') == 'world'
                        and j.find('child').get('link') == 'base_link'), None)
    if world_joint is None:
        raise ExecutionFault('Reviewed world -> base_link joint is missing')
    world_joint.find('origin').set('xyz', ' '.join(map(str, base_position)))
    for element in list(arm):
        if element.get('name') in ('ground_plane', 'ground_plane_joint'):
            arm.remove(element)  # the actual workcell already has a floor
    for element in eoat:
        if element.tag not in ('link', 'joint', 'material'):
            raise ExecutionFault('EOAT xacro was not fully expanded')
        arm.append(deepcopy(element))
    masses = {'vacuum_tool_base': 8.0, 'vacuum_cup_plate': 4.0,
              **{f'vacuum_cup_{n}': .75 for n in range(1, 5)}}
    links = {link.get('name'): link for link in arm.findall('link')}
    for link, mass in masses.items():
        if link not in links:
            raise ExecutionFault('Reviewed EOAT link is missing: '+link)
        add_inertia(links[link], mass)  # total 15 kg is the team's stated assumption
    ET.SubElement(arm, 'link', name='vacuum_contact')
    joint = ET.SubElement(arm, 'joint', name='vacuum_contact_joint', type='fixed')
    ET.SubElement(joint, 'parent', link='vacuum_cup_plate')
    ET.SubElement(joint, 'child', link='vacuum_contact')
    # Cup joint z=.03 and cylinder half-length=.0175: physical face=.0475.
    ET.SubElement(joint, 'origin', xyz='0 0 0.0475', rpy='0 0 0')
    for joint in arm.findall('joint'):
        child_link = links.get(joint.find('child').get('link'))
        if (joint.get('type') == 'fixed' and joint.get('name').startswith('vacuum_')
                and child_link is not None and child_link.find('inertial') is not None):
            gazebo = ET.SubElement(arm, 'gazebo', reference=joint.get('name'))
            ET.SubElement(gazebo, 'preserveFixedJoint').text = 'true'
    gazebo = ET.SubElement(arm, 'gazebo')
    ET.SubElement(gazebo, 'self_collide').text = 'true'
    for box in boxes:
        bid = identity(box['box_id'])
        plugin = ET.SubElement(gazebo, 'plugin', filename='libpac_confirmed_grasp.so',
                               name='pac_gazebo_grasp::ConfirmedGrasp')
        # Fortress source uses child_link (despite the tutorial's old name).
        for key, value in {'parent_link': 'vacuum_tool_base', 'child_model': bid, 'child_link': 'link',
                           'attach_topic': f'/pac/grasp/{bid}/attach',
                           'detach_topic': f'/pac/grasp/{bid}/detach',
                           'output_topic': f'/pac/grasp/{bid}/state'}.items():
            ET.SubElement(plugin, key).text = value
    return ET.tostring(arm, encoding='unicode')


def assemble_srdf(srdf_xml, name):
    root = ET.fromstring(srdf_xml)
    root.set('name', identity(name))
    group = root.find("group[@name='hdr_manipulator']/chain")
    if group is None:
        raise ExecutionFault('Reviewed MoveIt chain is missing')
    group.set('tip_link', 'vacuum_contact')
    for virtual in list(root.findall('virtual_joint')):
        root.remove(virtual)  # the URDF already defines world -> base_link
    rigid = ['flange', 'tool0', 'vacuum_tool_base', 'vacuum_cup_plate',
             *[f'vacuum_cup_{n}' for n in range(1, 5)], 'vacuum_tcp', 'vacuum_contact']
    for a, b in combinations(rigid, 2):
        ET.SubElement(root, 'disable_collisions', link1=a, link2=b, reason='SameRigidEOAT')
    return ET.tostring(root, encoding='unicode')


def build_descriptions(hdr_share, eoat_share, moveit_share, controllers, boxes, base_position, name):
    import xacro  # target ROS environment only
    hdr_share, eoat_share, moveit_share = map(Path, (hdr_share, eoat_share, moveit_share))
    arm = xacro.process_file(str(hdr_share/'urdf/hdr.urdf.xacro'), mappings={
        'robot_model': 'hdr50_22', 'name': name, 'use_sim': 'true', 'use_mock_hardware': 'false',
        'hdr_ros2_control': str(controllers),
        'initial_positions_file': str(moveit_share/'config/initial_positions.yaml')}).toxml()
    wrapper = ET.Element('robot', {'xmlns:xacro': 'http://www.ros.org/wiki/xacro'})
    ET.SubElement(wrapper, 'xacro:include', filename=str(eoat_share/'urdf/vacuum_gripper_v1.urdf.xacro'))
    ET.SubElement(wrapper, 'xacro:vacuum_gripper_v1', parent='tool0')
    with tempfile.TemporaryDirectory(prefix='ahead-eoat-') as temp:
        path = Path(temp)/'eoat.xacro'
        path.write_text(ET.tostring(wrapper, encoding='unicode'))
        eoat = xacro.process_file(str(path)).toxml()
    srdf = xacro.process_file(str(moveit_share/'config/hdr50_22.srdf.xacro'), mappings={'name': name}).toxml()
    return (assemble_urdf(arm, eoat, boxes, base_position, name), assemble_srdf(srdf, name))
