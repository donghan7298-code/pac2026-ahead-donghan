"""Stage 5-6: saturation safety + space + future - downside risk - time."""


def score_terms(features, future, config):
    m = features.metrics
    w = config.weights
    risk = (
        max(0.0, future.mean - future.cvar)
        + 0.25 * future.blocking_rate
        + 0.25 * future.failure_rate
    )
    return {
        "safety": w["safety"] * m["safety"],
        "space": w["space"] * m["space"],
        "future": w["future"] * future.mean,
        "risk": -w["risk"] * risk,
        "time": -w["time"] * m["time"],
    }


def priority(features, score):
    safety = features.metrics["safety"]
    target_met = safety >= 1 - 1e-8
    return (int(target_met), 0.0 if target_met else safety, score)
