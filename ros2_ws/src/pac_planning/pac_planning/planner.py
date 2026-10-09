"""ROS-free orchestration of 5-3 -> 5-4 -> 5-5 -> 5-6."""

from dataclasses import replace
import math
import time
from pac_common import (
    BoxStatus,
    CandidateEvaluation,
    ConstraintEvidence,
    FutureStats,
    PlacementCandidate,
    PlanningResult,
    RejectCode as R,
    ValidationResult,
)
from .config import PlannerConfig
from .features import FEATURE_SCHEMA, compute_features
from .geometry import EPS, bounds, overlap
from .model import DualHeadRanker
from .rollout import evaluate_shared, greedy_key
from .scenarios import sample_scenarios
from .scoring import priority, score_terms

ZERO_FUTURE = FutureStats(0.0, None, 0.0, 0.0, 0.0)


class PlacementPlanner:
    """Callbacks use exactly the v0.2 generator/validator signatures.

    No robot commands, buffer decisions, actual commits or global mutable state.
    Use explicit ReferenceBackend for the offline demo; production callbacks are
    supplied by the owners of stages 5-1 and 5-2.
    """

    def __init__(
        self,
        *,
        context,
        generate_candidates,
        validate_constraints,
        config=None,
        model=None,
        model_path=None,
        clock=time.perf_counter
    ):
        self.context = context
        self.generate_candidates = generate_candidates
        self.validate_constraints = validate_constraints
        self.config = config or PlannerConfig()
        self.model = model
        self.model_status = "PROVIDED" if model else "NO_MODEL_HEURISTIC"
        self.clock = clock
        if model_path is not None:
            try:
                self.model = DualHeadRanker.load(model_path)
                self.model_status = "TRAINED_DUAL_HEAD"
            except (OSError, ValueError, KeyError, TypeError) as error:
                self.model = None
                self.model_status = "MODEL_LOAD_FAILED:" + type(error).__name__

    def _check_input(self, box, state):
        if box.status not in (
            BoxStatus.MEASURED,
            BoxStatus.ON_CONVEYOR,
            BoxStatus.READY_FOR_PICK,
            BoxStatus.BUFFERED,
        ):
            raise ValueError("High-level must provide a measured pending box")
        if state.inventory.tracked_boxes.get(box.box_id) != box:
            raise ValueError("Current box must match State Manager snapshot")
        if any(b.box_id == box.box_id for b in state.pallet.boxes):
            raise ValueError("Current box already placed")
        if box.box_id.startswith(("__future__", "__probe__")):
            raise ValueError("Reserved ID namespace")
        for sku in state.inventory.remaining_by_sku:
            if sku not in self.context.catalog:
                raise ValueError("Missing catalog entry: " + sku)
        for b in (
            box,
            *state.pallet.boxes,
            *state.inventory.tracked_boxes.values(),
        ):
            if b.sku_id not in self.context.catalog:
                raise ValueError("Missing catalog entry: " + b.sku_id)
        for b in self.context.observed_preview:
            if (
                b.box_id == box.box_id
                or state.inventory.tracked_boxes.get(b.box_id) != b
            ):
                raise ValueError("Preview must match a distinct tracked box")
            if b.status not in (
                BoxStatus.MEASURED,
                BoxStatus.ON_CONVEYOR,
                BoxStatus.READY_FOR_PICK,
            ):
                raise ValueError("Invalid preview status")
        nbuffer = sum(
            b.status == BoxStatus.BUFFERED
            for b in state.inventory.tracked_boxes.values()
        )
        if nbuffer > self.context.buffer_capacity:
            raise ValueError("Buffer exceeds capacity")
        p = state.pallet.size
        existing = []
        for b in state.pallet.boxes:
            lo, hi = bounds(b)
            if any(a < -EPS for a in lo) or any(
                a > limit + EPS for a, limit in zip(hi, (p.x, p.y, p.z))
            ):
                raise ValueError("Existing box outside pallet")
            if any(overlap(lo, hi, a, z) for a, z in existing):
                raise ValueError("Existing boxes overlap")
            existing.append((lo, hi))

    def _rows(self, box, state, candidates):
        rows = []
        rejected = {}
        seen = set()
        for candidate in candidates:
            if (
                not isinstance(candidate, PlacementCandidate)
                or candidate.candidate_id in seen
            ):
                raise ValueError("Invalid/duplicate candidate identity")
            seen.add(candidate.candidate_id)
            if candidate.base_state_version != state.state_version:
                rejected[candidate.candidate_id] = ValidationResult(
                    False, (R.STALE_PLAN,)
                )
                continue
            if candidate.box_id != box.box_id:
                rejected[candidate.candidate_id] = ValidationResult(
                    False, (R.INVALID_STATE,)
                )
                continue
            verdict = self.validate_constraints(box, candidate, state)
            if not isinstance(verdict, ValidationResult):
                raise ValueError(
                    "Validator must return pac_common.ValidationResult"
                )
            if not verdict.success:
                rejected[candidate.candidate_id] = verdict
                continue
            evidence = verdict.details.get("evidence")
            if not isinstance(evidence, ConstraintEvidence):
                rejected[candidate.candidate_id] = ValidationResult(
                    False,
                    (R.INVALID_STATE,),
                    {"reason": "Missing typed ConstraintEvidence"},
                )
                continue
            try:
                feature = compute_features(
                    box,
                    candidate,
                    state,
                    evidence,
                    self.context,
                    self.config,
                    self.generate_candidates,
                    self.validate_constraints,
                )
            except ValueError as error:
                # Bad feature/EMS input is never scored. Preserve the other
                # candidates and record the failure instead of killing the plan.
                rejected[candidate.candidate_id] = ValidationResult(
                    False, (R.INVALID_STATE,),
                    {"reason": "FEATURE_INPUT_INVALID", "error": str(error)},
                )
                continue
            rows.append((candidate, feature))
        return rows, rejected

    def plan(
        self,
        box,
        state,
        candidates=None,
        *,
        seed=42,
        mode="ahead",
        use_time_budget=True,
        excluded_candidate_ids=()
    ):
        if mode not in ("ahead", "teacher", "ranking", "current", "greedy"):
            raise ValueError("Unknown mode")
        started = self.clock()
        # Offline teacher labels use a fixed work budget and never truncate.
        deadline = (
            started + self.config.timeout_sec
            if use_time_budget and mode != "teacher"
            else None
        )
        self._check_input(box, state)
        supplied = (
            list(candidates)
            if candidates is not None
            else self.generate_candidates(box, state)
        )
        supplied = [
            c for c in supplied if c.candidate_id not in excluded_candidate_ids
        ]
        rows, rejected = self._rows(box, state, supplied)
        diagnostics = {
            "algorithm_version": "0.1.0",
            "feature_schema": FEATURE_SCHEMA,
            "mode": mode,
            "seed": seed,
            "generated_count": len(supplied),
            "valid_count": len(rows),
            "masked_count": len(rejected),
            "robot_validation": "NOT_CHECKED",
            "budget_kind": "SOFT_SAFETY_NEVER_SKIPPED",
        }
        if not rows:
            diagnostics.update(
                planning_time_sec=self.clock() - started,
                reason="NO_VALID_FINITE_SEARCH_CANDIDATE",
                completed_scenarios=0,
            )
            return PlanningResult(
                state.state_version, (), (), rejected, diagnostics
            )
        model_status = self.model_status
        predictions = None
        if self.context.distribution_status == "OOD":
            model_status = "OOD_HEURISTIC_FALLBACK"
        elif self.model is not None and mode in ("ahead", "ranking"):
            try:
                contract = getattr(self.model, "payload", {}).get(
                    "rollout_contract"
                )
                if contract is not None and any(
                    contract.get(name) != getattr(self.config, name)
                    for name in ("horizon", "scenario_count", "cvar_alpha")
                ):
                    raise ValueError(
                        "Model rollout horizon/scenario/CVaR contract mismatch"
                    )
                predictions = self.model.predict([f for _, f in rows])
                if len(predictions) != len(rows) or any(
                    not math.isfinite(r) or not isinstance(f, FutureStats)
                    for r, f in predictions
                ):
                    raise ValueError("Invalid predictor output")
            except (
                ValueError,
                TypeError,
                RuntimeError,
                FloatingPointError,
            ) as error:
                predictions = None
                model_status = "INFERENCE_FAILED:" + type(error).__name__
        if predictions is None:
            predictions = [
                (
                    sum(score_terms(f, ZERO_FUTURE, self.config).values()),
                    ZERO_FUTURE,
                )
                for _, f in rows
            ]
        ranked = [
            (c, f, float(r), value)
            for (c, f), (r, value) in zip(rows, predictions)
        ]
        if mode == "greedy":
            ranked.sort(key=lambda row: greedy_key(box, row[0], state))
        else:
            ranked.sort(
                key=lambda row: (
                    *(-x for x in priority(row[1], row[2])),
                    row[0].candidate_id,
                )
            )
        pre_ranking = tuple(c.candidate_id for c, _, _, _ in ranked)
        k = (
            len(ranked)
            if mode == "teacher"
            else min(self.config.top_k, len(ranked))
        )
        n = self.config.scenario_count
        depth = self.config.horizon
        degradation = []
        if deadline is not None and mode == "ahead":
            fraction = max(
                0.0, (deadline - self.clock()) / self.config.timeout_sec
            )
            if fraction < 0.6:
                k = max(1, k // 2)
                degradation.append("REDUCED_TOP_K")
            if fraction < 0.35:
                n = max(1, n // 2)
                degradation.append("REDUCED_SCENARIOS")
            if fraction < 0.2:
                depth = 1
                degradation.append("HORIZON_ONE")
        selected = ranked[:k]
        outcomes = {}
        completed = ()
        interrupted = False
        scenarios = ()
        if mode in ("ahead", "teacher"):
            scenarios = sample_scenarios(
                box, state, self.context, seed, n, depth
            )
            outcomes, completed, interrupted = evaluate_shared(
                box,
                [r[0] for r in selected],
                state,
                scenarios,
                self.generate_candidates,
                self.validate_constraints,
                self.config,
                deadline,
                self.clock,
            )
        evaluations = []
        for c, feature, rank, predicted in selected:
            future = outcomes.get(c.candidate_id, predicted)
            source = (
                "ROLLOUT"
                if c.candidate_id in outcomes
                else (
                    "AI_ESTIMATE"
                    if self.model is not None
                    and model_status in ("PROVIDED", "TRAINED_DUAL_HEAD")
                    and mode in ("ahead", "ranking")
                    else "CURRENT_ONLY"
                )
            )
            terms = score_terms(feature, future, self.config)
            score = sum(terms.values())
            scored = replace(c, score=score, score_detail=terms)
            evaluations.append(
                CandidateEvaluation(scored, feature, rank, future, source)
            )
        if mode not in ("greedy", "ranking"):
            evaluations.sort(
                key=lambda e: (
                    *(-x for x in priority(e.features, e.candidate.score)),
                    e.candidate.candidate_id,
                )
            )
        elapsed = self.clock() - started
        diagnostics.update(
            model_status=model_status,
            distribution_status=self.context.distribution_status,
            pre_ranking=pre_ranking,
            top_k=k,
            requested_scenarios=n,
            horizon=depth,
            scenario_kinds=tuple(s.kind for s in scenarios),
            common_scenario_ids=completed,
            completed_scenarios=len(completed),
            interrupted_round_discarded=interrupted,
            degradation=tuple(degradation),
            planning_time_sec=elapsed,
            budget_exceeded=deadline is not None
            and elapsed > self.config.timeout_sec,
            no_rollout_fallback=mode == "ahead" and not completed,
        )
        return PlanningResult(
            state.state_version,
            tuple(e.candidate for e in evaluations),
            tuple(evaluations),
            rejected,
            diagnostics,
        )

    def evaluate_candidate(self, box, candidate, state):
        """v0.2 evaluator signature; current-state score after mandatory validation."""
        result = self.plan(
            box, state, [candidate], mode="current", use_time_budget=False
        )
        if not result.ranked:
            raise ValueError(
                "Candidate failed hard validation: " + repr(result.rejected)
            )
        return result.ranked[0]

    def evaluate_future_value(self, box, candidate, state, seed):
        """v0.2 look-ahead signature; all work remains SIMULATED."""
        result = self.plan(box, state, [candidate], seed=seed, mode="teacher")
        if not result.evaluations:
            raise ValueError("Candidate failed hard validation")
        return result.evaluations[0].future.mean
