"""Event-driven runtime core (no simulation, no ROS): the part a real cell
or a ROS 2 node drives.

    core = RuntimeCore(cell_info, cand_config, hl_config, rt_config, robot, policy)
    verdict = core.on_observation(raw_obs, base_view=camera.base_view)   # 1 -> 2 -> 8
    cmd = core.next_command()                                           # 3 -> 4 -> 5 -> 6
    ... robot executes cmd ...
    level = core.on_result(cmd, ExecutionReport(...))                   # 7 -> 8

All decisions read the State Manager snapshot; the state only changes in
``on_observation`` / ``on_result`` / ``on_conveyor_idle``.
"""

from collections import Counter
from dataclasses import dataclass, field, replace

from pac_common import PalletState, PlacementCandidate, SystemState
from pac_candidates.geometry import rotated_dims
from pac_highlevel import ActionType, HighLevelDecider

from .executor import ExecutorSim, TrueBox
from .placer import RobotAwarePlacer
from .state_manager import StateManager
from .state_validator import StateValidator
from .supervisor import Supervisor


@dataclass
class Command:
    """What the cell must do next (one high-level decision)."""
    action: str                     # PLACE_CURRENT | RETRIEVE_BUFFER | BUFFER_CURRENT | PALLET_CLOSE | PARTIAL_REPACK | REJECT_NG | WAIT
    state_version: int
    box_id: str | None = None
    slot: int | None = None
    candidate: object = None        # PlacementCandidate (pallet frame, min corner)
    robot: dict = field(default_factory=dict)        # stage-6 details (joints, gripper yaw, cycle time)
    repack: list = field(default_factory=list)       # [(box_id, candidate, stage-6 details)]
    reason: str = ""
    decided_by: str = ""


@dataclass
class ExecutionReport:
    """Measured outcome of a command (stage 7 sensors)."""
    ok: bool = True                 # grip / motion succeeded
    attempts: int = 1
    other_grasp: bool = False
    measured_pose: object = None    # Pose3D, pallet frame, min corner (top view)
    issues: tuple | None = None     # post-check findings of the top view; None = core checks the measured pose
    corrected_pose: object = None   # after an L4 HOLD: where the operator left the box (default: planned)
    repack_poses: dict = field(default_factory=dict)  # box_id -> measured Pose3D


class RuntimeCore:
    def __init__(self, cell, cand_config, hl_config, rt_config, robot, policy, ranker=None):
        self.cell = cell
        self.cfg = rt_config
        self.hl = hl_config
        self.robot = robot
        self.validator = StateValidator(cell.catalog, rt_config.validator, cell.weight_ranges)
        self.supervisor = Supervisor(rt_config.supervisor, cell.expected_by_sku)
        self.sm = StateManager(cell.pallet_size, cell.catalog, cell.expected_by_sku,
                               pallet_max_weight_kg=cell.pallet_max_weight_kg, pallet_prefix=cell.pallet_prefix,
                               buffer_slots=hl_config.buffer.slots)
        self.placer = RobotAwarePlacer(robot, rt_config.robot_checks_per_option, ranker)
        self.decider = HighLevelDecider(self.sm.context(), cand_config, hl_config, policy=policy,
                                        placer=self.placer)
        self._checker = ExecutorSim(rt_config.execution, rt_config.verify, None)  # geometry checks only
        self.counts = Counter()
        self.anomalies = Counter()
        self.inspection = []
        self.repacks_for_current = 0
        self.closed = []
        self.last_check = {}

    # ---------------------------------------------------------------- 1, 2
    def on_observation(self, obs, base_view=None):
        """Stage 2 on a raw observation; PLAN boxes enter the State Manager.
        A box id that is already tracked or placed (re-published message,
        re-scan) is ignored and returns None."""
        if obs.box_id in self.sm.tracked or any(p.box_id == obs.box_id for p in self.sm.placed):
            self.counts["duplicate_observation"] += 1
            return None
        verdict = self.validator.validate(obs, _BaseView(base_view) if base_view else None, None)
        self.anomalies[verdict.kind.value] += 1
        sku = verdict.sku
        if sku is not None:
            self.supervisor.on_arrival(sku)
        if verdict.route == "INSPECTION":
            if sku in self.sm.remaining:
                self.sm.discard_expected(sku)
            self.inspection.append({"box_id": obs.box_id, "reason": verdict.kind.value, "stage": 2})
            return verdict
        self.sm.arrive(verdict.box, uncertain=verdict.uncertain, no_load=verdict.no_load_on_top)
        self.repacks_for_current = 0
        return verdict

    # ---------------------------------------------------------------- 3
    def on_conveyor_idle(self, idle_s):
        """Stage 3: confirm MISSING once the conveyor has been idle long enough."""
        out = self.supervisor.confirm_missing(self.sm.t, idle_s)
        self.sm.confirm_missing(out)
        return out

    def has_work(self):
        return self.sm.current_id() is not None or bool(self.sm.buffer_slots())

    # ---------------------------------------------------------------- 4, 5, 6
    def next_command(self):
        sm = self.sm
        if not self.supervisor.can_pick() or not self.has_work():
            return Command("WAIT", sm.version, reason=self.supervisor.mode.value if self.has_work() else "NO_BOX")
        self.decider.context = sm.context()
        state = sm.snapshot()
        d = self.decider.decide(state, current_box_id=sm.current_id(), buffer_slots=sm.buffer_slots(),
                                buffer_age=sm.buffer_age(), repack_attempts=self.repacks_for_current)
        sm.decisions += 1
        if d.action is None:
            return Command("WAIT", state.state_version, reason=d.reason)
        a = d.action.type
        self.counts[a.value] += 1
        cmd = Command(a.value, state.state_version, d.box.box_id if d.box is not None else None, d.slot,
                      d.candidate, reason=d.reason, decided_by=d.decided_by)
        if a in (ActionType.PLACE_CURRENT, ActionType.RETRIEVE_BUFFER):
            v6 = self.robot.validate_robot_motion(d.box, d.candidate, state)
            if not v6.success:  # the placer only hands out executable candidates
                raise RuntimeError(f"stage 6 rejected the chosen candidate: {v6.codes}")
            cmd.robot = dict(v6.details)
        elif a == ActionType.PARTIAL_REPACK:
            self.repacks_for_current += 1
            placed = {p.box_id: p for p in state.pallet.boxes}
            layout = dict(placed)      # earlier moves of this repack applied
            for box_id, pose in d.repack_moves:
                without = SystemState(state.state_version, state.stamp_sec,
                                      PalletState(state.pallet.pallet_id, state.pallet.size,
                                                  tuple(p for k, p in layout.items() if k != box_id)),
                                      state.inventory)
                cand = PlacementCandidate(f"repack-{box_id}", box_id, pose, state.state_version)
                v6 = self.robot.validate_robot_motion(placed[box_id], cand, without)
                if not v6.success:
                    self.counts["repack_aborted_stage6"] += 1
                    return Command("WAIT", state.state_version, reason="REPACK_NOT_EXECUTABLE")
                cmd.repack.append((box_id, cand, dict(v6.details)))
                layout[box_id] = replace(placed[box_id], pose=pose)
        return cmd

    # ---------------------------------------------------------------- 7, 8
    def verify(self, box, candidate, measured_pose, stack=None):
        """Stage 7 post check on the measured pose against the measured stack."""
        stack = stack if stack is not None else [
            TrueBox(p.box_id, p.size, p.pose) for p in self.sm.placed]
        v = self.cfg.verify
        planned = candidate.target_pose
        dx, dy, _ = rotated_dims(box.size, planned.yaw)
        dxy = ((measured_pose.x - planned.x) ** 2 + (measured_pose.y - planned.y) ** 2) ** 0.5
        dz = abs(measured_pose.z - planned.z)
        issues = self._checker.check(box.size, measured_pose, stack, self.sm.pallet_size)
        if issues:
            return "L4", dxy, dz, issues
        if dxy <= v.l0_xy_m and dz <= v.l0_z_m:
            return "L0", dxy, dz, ()
        if dxy <= v.l1_xy_m and dz <= v.l1_z_m:
            return "L1", dxy, dz, ()
        return "L2", dxy, dz, ()

    def on_result(self, cmd, report=None):
        """Apply a finished command; returns the escalation level for placements."""
        sm, a = self.sm, cmd.action
        report = report or ExecutionReport()
        if cmd.state_version != sm.version and a not in ("WAIT",):
            self.counts["stale_result"] += 1
            raise ValueError("STALE_RESULT: state changed after the command was planned")
        if a == "REJECT_NG":
            sm.reject(cmd.box_id)
            self.inspection.append({"box_id": cmd.box_id, "reason": cmd.reason, "stage": 4})
        elif a == "BUFFER_CURRENT":
            sm.to_buffer(cmd.box_id, cmd.slot)
        elif a == "PALLET_CLOSE":
            self.closed.append((sm.pallet_id, tuple(sm.placed)))
            sm.close_pallet()
        elif a == "PARTIAL_REPACK":
            tol = max(self.cfg.verify.max_overlap_m, self.cfg.verify.max_protrusion_m)
            for box_id, cand, _ in cmd.repack:
                pb = next(p for p in sm.placed if p.box_id == box_id)
                pose, _ = sm.reconcile(pb.size, report.repack_poses.get(box_id, cand.target_pose), tol, ignore=box_id)
                sm.move_placed(box_id, pose)
                self.counts["repack_moves"] += 1
        elif a in ("PLACE_CURRENT", "RETRIEVE_BUFFER"):
            if report.attempts > 1:
                self.counts["grip_retries"] += report.attempts - 1
            if not report.ok:
                self.counts["L3"] += 1
                sm.reject(cmd.box_id)
                self.inspection.append({"box_id": cmd.box_id, "reason": "GRIP_FAIL", "stage": 7})
                return "L3"
            if report.other_grasp:
                self.counts["L3_other_grasp"] += 1
            box = sm.tracked[cmd.box_id]
            measured = report.measured_pose or cmd.candidate.target_pose
            level, dxy, dz, issues = self.verify(box, cmd.candidate, measured)
            if report.issues is not None:
                level = "L4" if report.issues else (level if level != "L4" else "L2")
                issues = tuple(report.issues)
            self.counts[level] += 1
            self.last_check = {"level": level, "dxy_m": dxy, "dz_m": dz, "issues": list(issues)}
            if level == "L4":
                self.sm.t += self.supervisor.hold(self.sm.t, "L4: " + ",".join(issues))
                measured = report.corrected_pose or cmd.candidate.target_pose
            tol = max(self.cfg.verify.max_overlap_m, self.cfg.verify.max_protrusion_m)
            measured, shift = sm.reconcile(box.size, measured, tol)
            if shift > 1e-9:
                self.counts["stage8_reconciled"] += 1
            sm.place(cmd.box_id, measured)
            return level
        return None


class _BaseView:
    """Adapter: ``base_view(previous_obs) -> obs`` for the validator."""

    def __init__(self, fn):
        self.fn = fn

    def base_view(self, _field, previous):
        return self.fn(previous)
