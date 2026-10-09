from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pybullet as p

from .models import BoxSpec, SimulatorConfig


@dataclass
class BodyRecord:
    body_id: int
    spec: BoxSpec


class BulletPalletWorld:
    """PyBullet rigid-body world.

    Placement validity and dynamic stability are intentionally separated:
    - initial rigid-body interpenetration is rejected;
    - after a valid insertion, gravity/contact/friction determine the motion.
    """

    def __init__(self, config: SimulatorConfig) -> None:
        self.config = config
        self.client_id = -1
        self.floor_id: Optional[int] = None
        self.pallet_id: Optional[int] = None
        self.boxes: Dict[str, BodyRecord] = {}
        self._body_to_name: Dict[int, str] = {}
        self.step_count = 0
        self.connect()
        self.reset()

    @property
    def simulation_time_s(self) -> float:
        return self.step_count / float(self.config.physics.physics_hz)

    def connect(self) -> None:
        if self.client_id >= 0:
            return
        self.client_id = p.connect(p.DIRECT)

    def reset(self) -> None:
        p.resetSimulation(physicsClientId=self.client_id)

        cfg = self.config
        ph = cfg.physics
        p.setGravity(
            0.0, 0.0, -ph.gravity_m_s2, physicsClientId=self.client_id
        )
        p.setTimeStep(1.0 / float(ph.physics_hz), physicsClientId=self.client_id)
        p.setPhysicsEngineParameter(
            numSolverIterations=ph.solver_iterations,
            physicsClientId=self.client_id,
        )

        self.boxes.clear()
        self._body_to_name.clear()
        self.step_count = 0

        floor_shape = p.createCollisionShape(
            p.GEOM_PLANE, physicsClientId=self.client_id
        )
        floor_z = -cfg.pallet.deck_height_m - 0.02
        self.floor_id = p.createMultiBody(
            baseMass=0.0,
            baseCollisionShapeIndex=floor_shape,
            basePosition=[0.0, 0.0, floor_z],
            physicsClientId=self.client_id,
        )
        self._body_to_name[self.floor_id] = "FLOOR"
        p.changeDynamics(
            self.floor_id,
            -1,
            lateralFriction=0.80,
            restitution=0.0,
            physicsClientId=self.client_id,
        )

        self.pallet_id = self._create_pallet_body()
        self._body_to_name[self.pallet_id] = "PALLET"

        box_friction, pallet_friction = ph.bullet_body_frictions()
        p.changeDynamics(
            self.pallet_id,
            -1,
            lateralFriction=pallet_friction,
            spinningFriction=ph.spinning_friction,
            rollingFriction=ph.rolling_friction,
            restitution=ph.restitution,
            physicsClientId=self.client_id,
        )

    def _linspace_centers(self, count: int, span: float) -> List[float]:
        if count <= 1:
            return [0.0]
        return [
            -span / 2.0 + i * span / float(count - 1)
            for i in range(count)
        ]

    def _create_pallet_body(self) -> int:
        cfg = self.config.pallet

        if cfg.collision_model.lower() == "solid":
            shape = p.createCollisionShape(
                p.GEOM_BOX,
                halfExtents=[
                    cfg.length_m / 2.0,
                    cfg.width_m / 2.0,
                    cfg.deck_height_m / 2.0,
                ],
                physicsClientId=self.client_id,
            )
            return p.createMultiBody(
                baseMass=0.0,
                baseCollisionShapeIndex=shape,
                basePosition=[0.0, 0.0, -cfg.deck_height_m / 2.0],
                physicsClientId=self.client_id,
            )

        # Configurable slatted development model.  Top surface remains z=0.
        L, W, H = cfg.length_m, cfg.width_m, cfg.deck_height_m

        top_w = W * cfg.top_board_width_ratio
        top_h = H * cfg.top_board_thickness_ratio
        block_l = L * cfg.support_block_length_ratio
        block_w = W * cfg.support_block_width_ratio
        block_h = H * cfg.support_block_height_ratio
        bottom_w = W * cfg.bottom_board_width_ratio
        bottom_h = H * cfg.bottom_board_thickness_ratio

        shape_types: List[int] = []
        half_extents: List[List[float]] = []
        positions: List[List[float]] = []
        orientations: List[List[float]] = []

        identity = p.getQuaternionFromEuler([0.0, 0.0, 0.0])

        # Top deck boards run in X direction.
        top_span = max(0.0, W - top_w)
        for y in self._linspace_centers(cfg.top_board_count, top_span):
            shape_types.append(p.GEOM_BOX)
            half_extents.append([L / 2.0, top_w / 2.0, top_h / 2.0])
            positions.append([0.0, y, -top_h / 2.0])
            orientations.append(identity)

        # Support blocks.
        block_z = -top_h - block_h / 2.0
        x_span = max(0.0, L - block_l)
        y_span = max(0.0, W - block_w)
        for x in self._linspace_centers(cfg.support_block_count_x, x_span):
            for y in self._linspace_centers(cfg.support_block_count_y, y_span):
                shape_types.append(p.GEOM_BOX)
                half_extents.append(
                    [block_l / 2.0, block_w / 2.0, block_h / 2.0]
                )
                positions.append([x, y, block_z])
                orientations.append(identity)

        # Bottom runner boards.
        bottom_z = -top_h - block_h - bottom_h / 2.0
        bottom_span = max(0.0, W - bottom_w)
        for y in self._linspace_centers(cfg.bottom_board_count, bottom_span):
            shape_types.append(p.GEOM_BOX)
            half_extents.append([L / 2.0, bottom_w / 2.0, bottom_h / 2.0])
            positions.append([0.0, y, bottom_z])
            orientations.append(identity)

        shape = p.createCollisionShapeArray(
            shapeTypes=shape_types,
            halfExtents=half_extents,
            collisionFramePositions=positions,
            collisionFrameOrientations=orientations,
            physicsClientId=self.client_id,
        )

        return p.createMultiBody(
            baseMass=0.0,
            baseCollisionShapeIndex=shape,
            basePosition=[0.0, 0.0, 0.0],
            physicsClientId=self.client_id,
        )

    def add_box(self, spec: BoxSpec) -> int:
        """Insert one box without allowing initial rigid-body penetration."""
        if spec.box_id in self.boxes:
            raise ValueError(f"duplicate box id: {spec.box_id}")

        sx, sy, sz = spec.size_m
        collision = p.createCollisionShape(
            p.GEOM_BOX,
            halfExtents=[sx / 2.0, sy / 2.0, sz / 2.0],
            physicsClientId=self.client_id,
        )
        orientation = p.getQuaternionFromEuler([0.0, 0.0, spec.yaw_rad])

        tx, ty, tz = spec.target_position_m
        spawn_z = tz + self.config.physics.spawn_clearance_m

        body = p.createMultiBody(
            baseMass=spec.mass_kg,
            baseCollisionShapeIndex=collision,
            basePosition=[tx, ty, spawn_z],
            baseOrientation=orientation,
            physicsClientId=self.client_id,
        )

        box_friction, _ = self.config.physics.bullet_body_frictions()
        p.changeDynamics(
            body,
            -1,
            lateralFriction=box_friction,
            spinningFriction=self.config.physics.spinning_friction,
            rollingFriction=self.config.physics.rolling_friction,
            restitution=self.config.physics.restitution,
            linearDamping=0.04,
            angularDamping=0.04,
            physicsClientId=self.client_id,
        )

        p.performCollisionDetection(physicsClientId=self.client_id)

        penetration_tolerance_m = 0.001
        worst_by_body: Dict[str, float] = {}

        for c in p.getContactPoints(
            bodyA=body,
            physicsClientId=self.client_id,
        ):
            other = int(c[2])
            distance = float(c[8])

            if distance < -penetration_tolerance_m:
                name = self.body_name(other)
                worst_by_body[name] = min(
                    distance,
                    worst_by_body.get(name, 0.0),
                )

        if worst_by_body:
            p.removeBody(body, physicsClientId=self.client_id)
            detail = ", ".join(
                f"{name}: {abs(depth) * 1000.0:.2f} mm penetration"
                for name, depth in sorted(worst_by_body.items())
            )
            raise ValueError(
                "initial placement overlaps current physical state: " + detail
            )

        self.boxes[spec.box_id] = BodyRecord(body, spec)
        self._body_to_name[body] = spec.box_id
        return body

    def step(self, count: int = 1) -> None:
        n = max(1, count)
        for _ in range(n):
            p.stepSimulation(physicsClientId=self.client_id)
        self.step_count += n

    def body_name(self, body_id: int) -> str:
        return self._body_to_name.get(body_id, f"BODY_{body_id}")

    def snapshot_boxes(self) -> List[Dict[str, Any]]:
        result: List[Dict[str, Any]] = []
        for box_id, rec in self.boxes.items():
            pos, quat = p.getBasePositionAndOrientation(
                rec.body_id, physicsClientId=self.client_id
            )
            lin, ang = p.getBaseVelocity(
                rec.body_id, physicsClientId=self.client_id
            )
            aabb_min, aabb_max = p.getAABB(
                rec.body_id, physicsClientId=self.client_id
            )
            roll, pitch, yaw = p.getEulerFromQuaternion(quat)
            result.append(
                {
                    "id": box_id,
                    "body_id": rec.body_id,
                    "size_m": list(rec.spec.size_m),
                    "mass_kg": rec.spec.mass_kg,
                    "target_position_m": list(rec.spec.target_position_m),
                    "target_yaw_rad": rec.spec.yaw_rad,
                    "position_m": list(pos),
                    "quaternion_xyzw": list(quat),
                    "euler_rad": [roll, pitch, yaw],
                    "linear_velocity_m_s": list(lin),
                    "angular_velocity_rad_s": list(ang),
                    "aabb_min_m": list(aabb_min),
                    "aabb_max_m": list(aabb_max),
                    "color": rec.spec.color,
                    "source": rec.spec.source,
                    "max_top_load_n": rec.spec.max_top_load_n,
                }
            )
        return result

    def contacts(self) -> List[Dict[str, Any]]:
        contacts = p.getContactPoints(physicsClientId=self.client_id)
        result: List[Dict[str, Any]] = []

        for c in contacts:
            body_a = int(c[1])
            body_b = int(c[2])
            result.append(
                {
                    "body_a": body_a,
                    "body_b": body_b,
                    "name_a": self.body_name(body_a),
                    "name_b": self.body_name(body_b),
                    "position_on_a_m": list(c[5]),
                    "position_on_b_m": list(c[6]),
                    "normal_on_b": list(c[7]),
                    "contact_distance_m": float(c[8]),
                    "normal_force_n": float(c[9]),
                    "lateral_friction_1_n": float(c[10]),
                    "lateral_friction_2_n": float(c[12]),
                }
            )
        return result

    def pallet_contacts(self) -> List[Dict[str, Any]]:
        if self.pallet_id is None:
            return []

        result: List[Dict[str, Any]] = []
        for c in p.getContactPoints(
            bodyA=self.pallet_id,
            physicsClientId=self.client_id,
        ):
            body_b = int(c[2])
            name = self.body_name(body_b)
            if name in {"PALLET", "FLOOR"}:
                continue
            if name not in self.boxes:
                continue

            result.append(
                {
                    "box_id": name,
                    "position_on_pallet_m": list(c[5]),
                    "position_on_box_m": list(c[6]),
                    "normal_force_n": float(c[9]),
                }
            )
        return result

    def close(self) -> None:
        if self.client_id >= 0:
            p.disconnect(physicsClientId=self.client_id)
            self.client_id = -1
