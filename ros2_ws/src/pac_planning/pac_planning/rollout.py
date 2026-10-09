"""Stage 5-5: greedy future placement, shared scenarios and fair timeout rounds."""

from dataclasses import dataclass
import math
import time
from pac_common import FutureStats
from .geometry import bounds, simulate_placement, volume


class RolloutTimeout(Exception):
    pass


@dataclass(frozen=True)
class ScenarioOutcome:
    value: float
    blocked: bool
    failure_fraction: float
    placed_count: int


def check_budget(deadline, clock):
    if deadline is not None and clock() >= deadline:
        raise RolloutTimeout()


def greedy_key(box, candidate, state):
    hi = bounds(box, candidate.target_pose)[1]
    p = state.pallet.size
    return (
        hi[2] / p.z,
        candidate.target_pose.y / p.y,
        candidate.target_pose.x / p.x,
        candidate.candidate_id,
    )


def run_scenario(
    box,
    candidate,
    state,
    scenario,
    generator,
    validator,
    config,
    deadline=None,
    clock=time.perf_counter,
):
    virtual = simulate_placement(state, box, candidate)
    added_volume = 0.0
    placed = 0
    for item in scenario.items:
        check_budget(deadline, clock)
        future = item.box
        proposals = generator(future, virtual.state)
        proposals.sort(key=lambda c: greedy_key(future, c, virtual.state))
        # The proposals are already in the exact greedy order. The first
        # hard-mask-valid one is the same minimum previously obtained after
        # collecting up to rollout_candidate_limit valid proposals. Do not
        # spend geometry checks on later candidates that cannot be selected.
        # Check identities for the entire generated list before short-circuiting.
        if any(c.box_id != future.box_id or
               c.base_state_version != virtual.state.state_version for c in proposals):
            raise ValueError("Invalid future generator contract")
        best = None
        for proposal in proposals:
            check_budget(deadline, clock)
            if validator(future, proposal, virtual.state).success:
                best = proposal
                break
        if best is None:
            break  # No implicit discard/reorder/buffer of an unplaceable arrival.
        virtual = simulate_placement(
            virtual.state, future, best, consume_unseen=item.consume_unseen
        )
        added_volume += volume(future.size)
        placed += 1
    n = len(scenario.items)
    return ScenarioOutcome(
        added_volume / volume(state.pallet.size),
        placed < n,
        (n - placed) / n if n else 0.0,
        placed,
    )


def lower_tail_cvar(values, alpha):
    """Exact lower alpha mass of the equal-weight empirical distribution."""
    if not values or not 0 < alpha <= 1:
        raise ValueError("CVaR requires samples and alpha in (0,1]")
    ordered = sorted(values)
    mass = len(ordered) * alpha
    whole = int(math.floor(mass))
    fractional = mass - whole
    total = sum(ordered[:whole])
    if fractional > 1e-12:
        total += fractional * ordered[whole]
    return total / mass


def summarize(outcomes, alpha):
    if not outcomes:
        raise ValueError("No completed common scenario")
    values = tuple(o.value for o in outcomes)
    n = len(values)
    return FutureStats(
        sum(values) / n,
        min(values),
        lower_tail_cvar(values, alpha),
        sum(o.blocked for o in outcomes) / n,
        sum(o.failure_fraction for o in outcomes) / n,
        values,
    )


def evaluate_shared(
    box,
    candidates,
    state,
    scenarios,
    generator,
    validator,
    config,
    deadline=None,
    clock=time.perf_counter,
):
    """Commit one scenario only after ALL candidates finish it.

    Incomplete rounds are discarded, so candidate order cannot buy more samples.
    A callback can itself overrun: this is a cooperative SOFT deadline.
    """
    outcomes = {c.candidate_id: [] for c in candidates}
    completed = []
    interrupted = False
    for scenario in scenarios:
        current_round = {}
        try:
            for candidate in candidates:
                check_budget(deadline, clock)
                current_round[candidate.candidate_id] = run_scenario(
                    box,
                    candidate,
                    state,
                    scenario,
                    generator,
                    validator,
                    config,
                    deadline,
                    clock,
                )
            check_budget(deadline, clock)
        except RolloutTimeout:
            interrupted = True
            break
        for identity, outcome in current_round.items():
            outcomes[identity].append(outcome)
        completed.append(scenario.scenario_id)
    return (
        {k: summarize(v, config.cvar_alpha) for k, v in outcomes.items() if v},
        tuple(completed),
        interrupted,
    )
