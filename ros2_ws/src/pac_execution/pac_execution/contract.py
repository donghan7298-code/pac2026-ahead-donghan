"""Small, testable execution contract; coordinates use metres and radians.

The runtime owns inventory/pallet state. This module only validates commands,
converts measured poses and prevents an unsupported action from becoming success.
"""

from dataclasses import dataclass
import math
import re


class ExecutionFault(RuntimeError):
    pass


def vector(value, count, label):
    if not isinstance(value, (list, tuple)) or len(value) != count:
        raise ExecutionFault(f'{label}: expected {count} values')
    if any(type(x) not in (int, float) or not math.isfinite(x) for x in value):
        raise ExecutionFault(f'{label}: non-finite or non-numeric value')
    return tuple(float(x) for x in value)


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,79}', value):
        raise ExecutionFault('Invalid model/box identity')
    return value


def quaternion(value):
    q = vector(value, 4, 'quaternion')
    norm = math.sqrt(sum(x*x for x in q))
    if norm < 1e-8 or abs(norm - 1) > 1e-3:
        raise ExecutionFault('Quaternion must be normalized')
    return tuple(x / norm for x in q)


def multiply(a, b):
    x, y, z, w = a
    xx, yy, zz, ww = b
    return (w*xx+x*ww+y*zz-z*yy, w*yy-x*zz+y*ww+z*xx,
            w*zz+x*yy-y*xx+z*ww, w*ww-x*xx-y*yy-z*zz)


def inverse(q):
    return (-q[0], -q[1], -q[2], q[3])


def rotate(q, xyz):
    return multiply(multiply(q, (*xyz, 0.0)), inverse(q))[:3]


def relative_pose(origin, rotation, position, orientation):
    return (rotate(inverse(rotation), tuple(b-a for a, b in zip(origin, position))),
            multiply(inverse(rotation), orientation))


def upright_yaw(q, tolerance=math.radians(2)):
    q = quaternion(q)
    up = rotate(q, (0, 0, 1))
    if up[2] < math.cos(tolerance):
        raise ExecutionFault('Observed box is tilted or inverted')
    x, y, z, w = q
    return math.atan2(2*(w*z+x*y), 1-2*(y*y+z*z))


def rotated_size(size, yaw):
    size = vector(size, 3, 'box size')
    if min(size) <= 0:
        raise ExecutionFault('Box dimensions must be positive')
    turns = round(yaw / (math.pi/2))
    if abs(math.remainder(yaw - turns*math.pi/2, 2*math.pi)) > math.radians(2):
        raise ExecutionFault('Only upright quarter-turn cartons are supported')
    return (size[1], size[0], size[2]) if turns % 2 else size


@dataclass(frozen=True)
class PalletTransform:
    size: tuple
    origin_world: tuple
    yaw: float = 0.0

    def __post_init__(self):
        if min(vector(self.size, 3, 'pallet size')) <= 0:
            raise ExecutionFault('Pallet dimensions must be positive')
        vector(self.origin_world, 3, 'pallet origin')
        vector([self.yaw], 1, 'pallet yaw')

    def to_world(self, xyz):
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        x, y, z = xyz
        ox, oy, oz = self.origin_world
        return (ox+c*x-s*y, oy+s*x+c*y, oz+z)

    def to_pallet(self, xyz):
        ox, oy, oz = self.origin_world
        x, y, z = (a-b for a, b in zip(xyz, (ox, oy, oz)))
        c, s = math.cos(self.yaw), math.sin(self.yaw)
        return (c*x+s*y, -s*x+c*y, z)

    def measured_corner(self, size, center_world, q_world):
        # The team's hard mask models axis-aligned upright cartons. Large
        # observed rotations require a different geometry model, not rounding.
        yaw = upright_yaw(q_world, tolerance=.001) - self.yaw
        if abs(math.remainder(yaw, math.pi/2)) > .001:
            raise ExecutionFault('Measured carton is not aligned with the upright hard-mask model')
        # Only a measured deviation <= 1 mrad is projected into the team's
        # exact quarter-turn representation. Position is never replaced by
        # a target. The executor retains the raw world quaternion in its log.
        yaw = round(yaw/(math.pi/2))*(math.pi/2)
        dims = rotated_size(size, yaw)
        center = self.to_pallet(vector(center_world, 3, 'observed centre'))
        corner = tuple(c-d/2 for c, d in zip(center, dims))
        # A contact solver can leave a resting rigid body microscopically
        # below the deck. Project at most 0.5 mm onto the KNOWN pallet plane;
        # deeper penetration is a fault. XY stays measured. This is required
        # by the planner's nonnegative-Z state contract, not a target fallback.
        if corner[2] < -.0005:
            raise ExecutionFault('Measured carton penetrates below the pallet plane')
        return (*corner[:2], max(0., corner[2]), yaw)

    def tcp(self, size, corner, gap):
        x, y, z, yaw = vector(corner, 4, 'target corner')
        dx, dy, dz = rotated_size(size, yaw)
        point = self.to_world((x+dx/2, y+dy/2, z+dz+gap))
        world_yaw = yaw + self.yaw
        # Rz(yaw) Rx(pi): the suction direction points downwards.
        return point, (math.cos(world_yaw/2), math.sin(world_yaw/2), 0.0, 0.0)


def validate_command(command, boxes, pallet, latest_version=None):
    if type(command.get('state_version')) is not int or command['state_version'] < 0:
        raise ExecutionFault('A nonnegative integer state_version is required')
    if latest_version is not None and command['state_version'] < latest_version:
        raise ExecutionFault('STALE_PLAN: runtime has a newer snapshot')
    if command.get('action') not in ('PLACE_CURRENT',):
        # Buffer, NG, pallet replacement and repack need confirmed cell actuators.
        # The first robot acceptance demo deliberately stops at that boundary.
        raise ExecutionFault('CELL_ACTUATOR_REQUIRED: '+str(command.get('action')))
    bid = identity(command.get('box_id'))
    if bid not in boxes:
        raise ExecutionFault('Box is absent from the physical fixture')
    if not isinstance(command.get('candidate_id'), str) or not command['candidate_id']:
        raise ExecutionFault('Missing candidate identity')
    corner = vector(command.get('target_min_corner'), 4, 'target corner')
    dims = rotated_size(boxes[bid]['size_m'], corner[3])
    if any(v < -1e-7 or v+d > limit+1e-7 for v, d, limit in zip(corner[:3], dims, pallet.size)):
        raise ExecutionFault('Target box lies outside the physical pallet')
    return bid, corner


def measured_report(command, corner):
    return dict(state_version=command['state_version'], box_id=command['box_id'],
                candidate_id=command['candidate_id'], ok=True, attempts=1,
                measured_pose=list(vector(corner, 4, 'measured corner')),
                measurement_source='GAZEBO_WORLD_POSE', measured_frame='pallet')


def guard_runtime_result(command, report):
    """Missing observations/failure must not trigger RuntimeCore's target fallback."""
    if type(report.get('state_version')) is not int or report['state_version'] != command.state_version:
        raise ExecutionFault('Result version differs from outstanding command')
    if report.get('box_id') != command.box_id:
        raise ExecutionFault('Result belongs to another box')
    if report.get('ok') is not True:
        raise ExecutionFault('Physical failure requires inspection before state commit')
    if command.candidate is not None:
        if report.get('candidate_id') != command.candidate.candidate_id:
            raise ExecutionFault('Result belongs to another candidate')
        vector(report.get('measured_pose'), 4, 'measured corner')
        if report.get('measured_frame') != 'pallet':
            raise ExecutionFault('Measured placement must use the pallet lower-corner frame')
        if report.get('measurement_source') not in ('GAZEBO_WORLD_POSE', 'CALIBRATED_VISION'):
            raise ExecutionFault('A measured placement source is required')
        if 'issues' in report:
            raise ExecutionFault('External issues cannot override runtime geometry checking')
