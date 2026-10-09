"""Query minibatches, validation selection and resumable Adam for the two heads.

Kept separate from inference so ROS nodes need only NumPy and the model JSON.
The test split is never accepted by this trainer or used for early stopping.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from .features import FEATURE_NAMES
from .model import DualHeadRanker, MODEL_FORMAT, OUTPUT_NAMES, lambda_gradient
from .features import FEATURE_SCHEMA


def arrays_for(groups):
    if hasattr(groups, "features") and hasattr(groups, "offsets"):
        if not len(groups):
            raise ValueError("Nonempty training and validation queries required")
        return groups
    if not groups or any(not g["rows"] for g in groups):
        raise ValueError("Separate nonempty training and validation groups required")
    rows = [r for g in groups for r in g["rows"]]
    x = np.asarray([r["features"] for r in rows], dtype=float)
    y = np.asarray([[r["future"][n] for n in OUTPUT_NAMES] for r in rows], dtype=float)
    rel = np.asarray([r["teacher_score"] for r in rows], dtype=float)
    offsets = np.cumsum([0] + [len(g["rows"]) for g in groups])
    base = set(g["base_group"] for g in groups)
    h = hashlib.sha256()
    for a in (x, y, rel, offsets):
        h.update(a.tobytes())
    h.update(json.dumps(sorted(base, key=str)).encode())
    return SimpleNamespace(features=x, future=y, relevance=rel, offsets=offsets,
                           base_groups=base, fingerprint=h.hexdigest())


def _statistics(data, chunk=16384):
    total = np.zeros(len(FEATURE_NAMES))
    square = np.zeros(len(FEATURE_NAMES))
    for start in range(0, len(data.features), chunk):
        x = np.asarray(data.features[start:start + chunk], dtype=float)
        if not np.isfinite(x).all():
            raise ValueError("Non-finite training feature")
        total += x.sum(0)
        square += (x * x).sum(0)
    mean = total / len(data.features)
    scale = np.maximum(np.sqrt(np.maximum(0, square / len(data.features) - mean * mean)), 0.05)
    return mean, scale


def _validate(params, data, mean, scale):
    squared, count, regrets = 0.0, 0, []
    w1, b1, wr, br, wv, bv = params
    for gi in range(len(data.offsets) - 1):
        a, b = data.offsets[gi:gi + 2]
        x = (np.asarray(data.features[a:b], dtype=float) - mean) / scale
        y = np.asarray(data.future[a:b], dtype=float)
        h = np.tanh(x @ w1 + b1)
        rank = h @ wr + br
        predicted = h @ wv + bv
        squared += float(np.sum((predicted - y) ** 2))
        count += y.size
        truth = data.relevance[a:b]
        safety = data.features[a:b, FEATURE_NAMES.index("safety_saturated")]
        order = sorted(range(len(rank)), key=lambda i: (
            int(safety[i] >= 1 - 1e-8), 0. if safety[i] >= 1 - 1e-8 else float(safety[i]),
            float(rank[i])), reverse=True)
        regrets.append(float(np.max(truth) - truth[order[0]]))
    mse, regret = squared / count, float(np.mean(regrets))
    return mse, regret


def train_queries(groups, validation_groups, *, seed=42, epochs=100, hidden=32,
                  learning_rate=0.002, batch_queries=1, patience=25,
                  min_delta=1e-6, weight_decay=1e-5, checkpoint_path=None,
                  resume=False, initial_model=None, on_epoch=None):
    from .training_data import atomic_json

    if min(epochs, hidden, batch_queries) < 1 or patience < 0:
        raise ValueError("epochs/hidden/batch_queries positive; patience nonnegative")
    if (not np.isfinite(learning_rate) or learning_rate <= 0 or min_delta < 0
            or weight_decay < 0):
        raise ValueError("Invalid optimizer settings")
    if resume and initial_model is not None:
        raise ValueError("Use resume or initial_model, not both")
    train, val = arrays_for(groups), arrays_for(validation_groups)
    if train.base_groups & val.base_groups:
        raise ValueError("Inventory-group train/validation leakage")
    for data in (train, val):
        if data.features.shape[1] != len(FEATURE_NAMES):
            raise ValueError("Bad training feature width")
        if not np.isfinite(data.future).all() or not np.isfinite(data.relevance).all():
            raise ValueError("Non-finite training target")
    options = dict(seed=seed, hidden=hidden, learning_rate=learning_rate,
                   batch_queries=batch_queries, weight_decay=weight_decay,
                   patience=patience, min_delta=min_delta)
    fingerprint = train.fingerprint + ":" + val.fingerprint
    rng = np.random.default_rng(seed)
    mean, scale = _statistics(train)
    value_mean = np.asarray(train.future).mean(0, dtype=np.float64)
    params = [rng.normal(0, 1 / np.sqrt(len(FEATURE_NAMES)), (len(FEATURE_NAMES), hidden)),
              np.zeros(hidden), rng.normal(0, .05, hidden), np.asarray(0.),
              rng.normal(0, .05, (hidden, 4)), value_mean]
    initialization = "NEW_MODEL"
    if initial_model is not None:
        heldout = set(initial_model.payload.get("training", {}).get("validation_base_groups", []))
        prior = initial_model.payload.get("training_provenance", {}).get("heldout_inventory_groups", [])
        if train.base_groups & (heldout | set(prior)):
            raise ValueError("Warm start would train on a previous held-out inventory")
        if initial_model.w1.shape[1] != hidden:
            raise ValueError("Warm-start hidden width mismatch")
        mean, scale = initial_model.mean.copy(), initial_model.scale.copy()
        params = [getattr(initial_model, n).copy() for n in ("w1", "b1", "wr", "br", "wv", "bv")]
        initialization = "WARM_START_NEW_ADAM"
    moment, variance = [np.zeros_like(p) for p in params], [np.zeros_like(p) for p in params]
    best, best_loss, best_epoch, stale = None, float("inf"), 0, 0
    history, step, start_epoch = [], 0, 0
    if resume:
        if checkpoint_path is None or not Path(checkpoint_path).is_file():
            raise ValueError("Resume requires an existing checkpoint")
        saved = json.loads(Path(checkpoint_path).read_text(encoding="utf-8"))
        if (saved.get("format") != "ahead-training-checkpoint-v1"
                or saved["data_fingerprint"] != fingerprint or saved["options"] != options):
            raise ValueError("Checkpoint dataset/optimizer contract mismatch")
        params = [np.asarray(p, dtype=float) for p in saved["params"]]
        moment = [np.asarray(p, dtype=float) for p in saved["moment"]]
        variance = [np.asarray(p, dtype=float) for p in saved["variance"]]
        best = [np.asarray(p, dtype=float) for p in saved["best"]]
        mean, scale = np.asarray(saved["mean"]), np.asarray(saved["scale"])
        best_loss, best_epoch, stale = saved["best_loss"], saved["best_epoch"], saved["stale"]
        history, step, start_epoch = saved["history"], saved["step"], saved["epoch"]
        rng.bit_generator.state = saved["rng_state"]
        initialization = saved["initialization"]
    for epoch in range(start_epoch, epochs):
        if patience and stale >= patience:
            break
        order = rng.permutation(len(train.offsets) - 1)
        for start in range(0, len(order), batch_queries):
            indices = order[start:start + batch_queries]
            grads = [np.zeros_like(p) for p in params]
            w1, b1, wr, br, wv, bv = params
            for gi in indices:
                a, b = train.offsets[gi:gi + 2]
                batch = (np.asarray(train.features[a:b], dtype=float) - mean) / scale
                h = np.tanh(batch @ w1 + b1)
                ranks, values = h @ wr + br, h @ wv + bv
                # Equal query weight avoids large candidate sets dominating Adam.
                dr = lambda_gradient(ranks, train.relevance[a:b]) / max(1, b - a)
                dv = 2 * (values - train.future[a:b]) / max(1, values.size)
                dh = (dr[:, None] * wr[None, :] + dv @ wv.T) * (1 - h * h)
                local = [batch.T @ dh, dh.sum(0), h.T @ dr, np.asarray(dr.sum()),
                         h.T @ dv, dv.sum(0)]
                for k, g in enumerate(local):
                    grads[k] += g / len(indices)
            step += 1
            for k, (p, grad) in enumerate(zip(params, grads)):
                grad = np.clip(grad + weight_decay * p, -3., 3.)
                moment[k] = .9 * moment[k] + .1 * grad
                variance[k] = .999 * variance[k] + .001 * grad * grad
                p -= learning_rate * (moment[k] / (1 - .9**step)) / (
                    np.sqrt(variance[k] / (1 - .999**step)) + 1e-8)
        mse, regret = _validate(params, val, mean, scale)
        loss = mse + regret
        if not np.isfinite(loss):
            raise ValueError("Training diverged; checkpoint not overwritten")
        if loss < best_loss - min_delta:
            best, best_loss, best_epoch, stale = [p.copy() for p in params], loss, epoch + 1, 0
        else:
            stale += 1
        metric = dict(epoch=epoch + 1, validation_mse=mse, validation_regret=regret,
                      validation_objective=loss, selected_epoch=best_epoch, steps=step)
        history.append(metric)
        if checkpoint_path is not None:
            atomic_json(checkpoint_path, dict(
                format="ahead-training-checkpoint-v1", data_fingerprint=fingerprint,
                options=options, params=[p.tolist() for p in params],
                moment=[p.tolist() for p in moment], variance=[p.tolist() for p in variance],
                best=[p.tolist() for p in best], best_loss=best_loss, best_epoch=best_epoch,
                stale=stale, epoch=epoch + 1, history=history, step=step,
                rng_state=rng.bit_generator.state, mean=mean.tolist(), scale=scale.tolist(),
                initialization=initialization))
        if on_epoch is not None:
            on_epoch(metric)
    if best is None:
        raise ValueError("No epoch completed")
    payload = dict(format=MODEL_FORMAT, feature_schema=FEATURE_SCHEMA,
                   feature_names=list(FEATURE_NAMES), output_names=list(OUTPUT_NAMES),
                   mean=mean.tolist(), scale=scale.tolist())
    payload.update({n: p.tolist() for n, p in zip(("w1", "b1", "wr", "br", "wv", "bv"), best)})
    payload["training"] = dict(seed=seed, selected_epoch=best_epoch,
                               train_base_groups=sorted(train.base_groups, key=str),
                               validation_base_groups=sorted(val.base_groups, key=str),
                               objective="LambdaRank + independent future-outcome MSE",
                               optimizer=options, initialization=initialization,
                               train_queries=len(train.offsets) - 1,
                               train_candidates=len(train.features),
                               validation_queries=len(val.offsets) - 1)
    return DualHeadRanker(payload), dict(selected_epoch=best_epoch, history=history,
                                        early_stopped=len(history) < epochs,
                                        completed_epochs=len(history))
