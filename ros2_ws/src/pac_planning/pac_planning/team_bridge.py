"""Stage 4 -> team's stage 5-1/2 -> stage 5-3/6, without robot I/O.

Keep pac_common/pac_planning from the planner repository on the import path.
The workcell repository has different packages with those same names.
"""

from collections import Counter
from dataclasses import replace
import hashlib
import inspect
import json
from pathlib import Path

from pac_common import plain

from .config import PlannerConfig
from .model import DualHeadRanker
from .planner import PlacementPlanner


def backend_contract(backend):
    """Fingerprint the actual candidate implementation and checked config."""
    package = Path(inspect.getfile(type(backend))).parent
    return {
        "backend": type(backend).__module__ + "." + type(backend).__name__,
        "source_sha256": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(package.glob("*.py"))
        },
        "candidate_config": plain(backend.config),
    }


def plan_with_backend(box, state, backend, *, candidates=None, config=None,
                      model_path=None, model=None, seed=7, use_time_budget=True, mode="ahead"):
    """Preserve the authoritative context and attach real EMS to each candidate.

No model is loaded by default: the shipped reference-backend model is not a
validated model for the teammate's EMS/LBCP backend. The caller must explicitly
choose and validate a model before passing model_path.
"""
    if backend.context is None:
        raise ValueError("A PlanningContext with explicit load limits is required")
    if model is not None and model_path is not None:
        raise ValueError("Provide model or model_path, not both")
    if model_path is not None:
        model = DualHeadRanker.load(model_path)
    contract = getattr(model, "payload", {}).get("backend_contract")
    if contract is not None and contract != backend_contract(backend):
        raise ValueError("MODEL_BACKEND_MISMATCH: candidate source/config changed; evaluate or retrain")
    generation = backend.generate_with_report(box, state)
    context = backend.context_with_ems(
        replace(backend.context, ems_upper_by_candidate={}), generation
    )
    if candidates is None:
        candidates = list(generation.candidates)
    else:
        candidates = list(candidates)
        generated = {c.candidate_id: c for c in generation.candidates}
        # A cached ID is not enough: reject stale/modified poses and versions.
        for c in candidates:
            expected = generated.get(c.candidate_id)
            if (expected is None or c.box_id != expected.box_id
                    or c.target_pose != expected.target_pose
                    or c.base_state_version != expected.base_state_version):
                raise ValueError("Candidate does not match this backend snapshot")
    planner = PlacementPlanner(
        context=context, config=config or PlannerConfig(), model=model,
        generate_candidates=backend.generate_candidates,
        validate_constraints=backend.validate_constraints,
    )
    return planner.plan(box, state, candidates, seed=seed, mode=mode,
                        use_time_budget=use_time_budget)


def plan_high_level_decision(decision, state, backend, **planning_options):
    """New HighLevelDecider handoff on the exact actual snapshot (no I/O).

    Stage 4 chooses the box/action. Its candidate is advisory: regenerate all
    stage-5 candidates with EMS, then select the final position. The caller
    dispatches buffer/close/repack/NG actions separately and performs robot
    validation, execution and actual state commit after this PLANNED result.
    """
    if decision.state_version != state.state_version:
        raise ValueError("STALE_PLAN: high-level decision version differs from snapshot")
    if not decision.requires_low_level or decision.box is None:
        raise ValueError("High-level action does not request placement planning")
    if state.inventory.tracked_boxes.get(decision.box.box_id) != decision.box:
        raise ValueError("High-level box differs from the authoritative snapshot")
    if "candidates" in planning_options:
        raise ValueError("High-level handoff regenerates all candidates with real EMS")
    return plan_with_backend(decision.box, state, backend, **planning_options)


class TeamPlacer:
    """Optional pac_highlevel.PalletizingWorld placer; all states stay SIMULATED.

Only changes placement selection. It does NOT replace the PPO's trained
value_provider=proxy observation with a different future-value definition.
"""

    wants_context = True
    name = "donghan_ems_rollout_v1"

    def __init__(self, config=None, *, seed=7, use_time_budget=True, on_plan=None,
                 model_path=None, mode="ahead"):
        if mode not in ("ahead", "ranking", "current", "greedy", "teacher"):
            raise ValueError("Unknown placement mode")
        self.config = config or PlannerConfig()
        self.model = DualHeadRanker.load(model_path) if model_path is not None else None
        self.mode = mode
        self.seed = seed
        self.use_time_budget = use_time_budget
        self.last_result = None
        self.calls = 0
        self.on_plan = on_plan

    def __call__(self, valid, box, state, backend):
        self.last_result = None
        self.calls += 1
        # High-level retains the arrival object while a buffered snapshot has
        # status BUFFERED. Use the State Manager's exact current object.
        box = state.inventory.tracked_boxes[box.box_id]
        self.last_result = plan_with_backend(
            box, state, backend, candidates=valid, config=self.config,
            model=self.model, mode=self.mode, seed=self.seed,
            use_time_budget=self.use_time_budget,
        )
        if self.on_plan is not None:
            self.on_plan(box, state, self.last_result)
        return self.last_result.ranked[0] if self.last_result.ranked else None


class TeamRuntimeRanker:
    """Ordered stage-5 output for ``pac_runtime.RobotAwarePlacer``.

    The runtime's ranker contract returns a list rather than one candidate.
    Reuse :func:`plan_with_backend` so the runtime cannot silently bypass the
    real EMS mapping or a learned model's candidate-backend fingerprint.
    Candidates rejected while building typed features are not appended again.
    """

    name = TeamPlacer.name

    def __init__(self, config=None, *, seed=7, use_time_budget=False,
                 on_plan=None, model_path=None, mode="ahead"):
        if mode not in ("ahead", "ranking", "current", "greedy", "teacher"):
            raise ValueError("Unknown placement mode")
        self.config = config or PlannerConfig()
        self.model = DualHeadRanker.load(model_path) if model_path is not None else None
        self.mode = mode
        self.seed = seed
        self.use_time_budget = use_time_budget
        self.on_plan = on_plan
        self.last_result = None
        self.calls = 0
        self.candidate_evaluations = 0
        self.geometry_sources = Counter()
        self.model_statuses = Counter()
        self.robot_validation_required_calls = 0
        self.backend_contract_sha256 = None

    def __call__(self, valid, box, state, backend):
        self.last_result = None
        self.calls += 1
        box = state.inventory.tracked_boxes[box.box_id]
        self.last_result = plan_with_backend(
            box, state, backend, candidates=valid, config=self.config,
            model=self.model, mode=self.mode, seed=self.seed,
            use_time_budget=self.use_time_budget,
        )
        if self.backend_contract_sha256 is None:
            payload = json.dumps(
                backend_contract(backend), sort_keys=True,
                separators=(",", ":"), ensure_ascii=True,
            ).encode("utf-8")
            self.backend_contract_sha256 = hashlib.sha256(payload).hexdigest()
        self.candidate_evaluations += len(self.last_result.evaluations)
        self.geometry_sources.update(
            evaluation.features.geometry_source
            for evaluation in self.last_result.evaluations
        )
        self.model_statuses[str(
            self.last_result.diagnostics.get("model_status", "UNKNOWN")
        )] += 1
        self.robot_validation_required_calls += int(
            self.last_result.requires_robot_validation
        )
        if self.on_plan is not None:
            self.on_plan(box, state, self.last_result)
        return list(self.last_result.ranked)

    def provenance(self):
        """Compact evidence for runtime reports; does not change ranking."""
        evaluated = sum(self.geometry_sources.values())
        return {
            "ranker": self.name,
            "calls": self.calls,
            "candidate_evaluations": self.candidate_evaluations,
            "geometry_sources": dict(sorted(self.geometry_sources.items())),
            "ems_verified": (
                evaluated > 0
                and set(self.geometry_sources) == {"EMS_SUPPLIED"}
            ),
            "model_statuses": dict(sorted(self.model_statuses.items())),
            "robot_validation_required_calls": self.robot_validation_required_calls,
            "backend_contract_sha256": self.backend_contract_sha256,
        }


def check_policy_contract(contract, *, placer_name, value_provider):
    """Changing the low-level placer changes PPO observations and transitions.

Do not relabel a DBLF-trained policy as compatible by rewriting its metadata.
Train/evaluate the policy with the intended placer before deploying it.
"""
    if (contract.get("placer") != placer_name
            or contract.get("value_provider") != value_provider):
        raise ValueError("PPO placer/value-provider mismatch: retraining or explicit offline evaluation required")
