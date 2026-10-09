"""Stage 5-4: NumPy MLP with LambdaRank and independent future regression.

Rank logits are comparable only WITHIN one decision. The regression predicts
additional volume / pallet capacity, lower-tail value, blocking and unserved
fractions; it is the only head used as the future value in final scoring.
"""

import json
from pathlib import Path
import numpy as np
from pac_common import FutureStats
from .features import FEATURE_NAMES, FEATURE_SCHEMA

OUTPUT_NAMES = ("mean", "cvar", "blocking_rate", "failure_rate")
MODEL_FORMAT = "ahead-dual-head-lambdarank-v1"


class DualHeadRanker:
    def __init__(self, payload):
        if (
            payload.get("format") != MODEL_FORMAT
            or payload.get("feature_schema") != FEATURE_SCHEMA
        ):
            raise ValueError(
                "Incompatible model schema; retraining is required"
            )
        if payload.get("feature_names") != list(FEATURE_NAMES) or payload.get(
            "output_names"
        ) != list(OUTPUT_NAMES):
            raise ValueError("Incompatible feature/output order")
        self.payload = payload
        names = ("mean", "scale", "w1", "b1", "wr", "br", "wv", "bv")
        for name in names:
            setattr(self, name, np.asarray(payload[name], dtype=float))
        f = len(FEATURE_NAMES)
        if self.w1.ndim != 2:
            raise ValueError("Bad hidden weights")
        h = self.w1.shape[1]
        shapes = {
            "mean": (f,),
            "scale": (f,),
            "w1": (f, h),
            "b1": (h,),
            "wr": (h,),
            "br": (),
            "wv": (h, 4),
            "bv": (4,),
        }
        if any(getattr(self, n).shape != shape for n, shape in shapes.items()):
            raise ValueError("Incompatible model shapes")
        if any(
            not np.isfinite(getattr(self, n)).all() for n in names
        ) or np.any(self.scale <= 0):
            raise ValueError("Invalid model values")

    @classmethod
    def load(cls, path):
        return cls(json.loads(Path(path).read_text(encoding="utf-8")))

    def save(self, path):
        Path(path).write_text(
            json.dumps(self.payload, ensure_ascii=False, allow_nan=False),
            encoding="utf-8",
        )

    def predict(self, vectors):
        x = np.asarray([v.values for v in vectors], dtype=float)
        if (
            any(v.names != FEATURE_NAMES for v in vectors)
            or x.ndim != 2
            or x.shape[1] != len(FEATURE_NAMES)
        ):
            raise ValueError("Bad feature schema")
        h = np.tanh((x - self.mean) / self.scale @ self.w1 + self.b1)
        ranks = h @ self.wr + self.br
        values = np.clip(h @ self.wv + self.bv, 0.0, 1.0)
        if not np.isfinite(ranks).all() or not np.isfinite(values).all():
            raise ValueError("Non-finite model output")
        values[:, 1] = np.minimum(values[:, 1], values[:, 0])
        return [
            (
                float(r),
                FutureStats(
                    float(v[0]), None, float(v[1]), float(v[2]), float(v[3])
                ),
            )
            for r, v in zip(ranks, values)
        ]


def lambda_gradient(scores, relevance):
    """Pairwise RankNet derivative weighted by absolute NDCG swap change."""
    n = len(scores)
    gradient = np.zeros(n)
    if n < 2 or np.ptp(relevance) < 1e-10:
        return gradient
    # Continuous teacher utilities -> graded relevance, local to a query.
    rel = 3 * (relevance - np.min(relevance)) / np.ptp(relevance)
    gain = 2**rel - 1
    discount = 1 / np.log2(np.arange(n) + 2)
    ideal = float(np.sort(gain)[::-1] @ discount)
    order = np.argsort(-scores, kind="stable")
    rank = np.empty(n, dtype=int)
    rank[order] = np.arange(n)
    # Same LambdaRank derivative, vectorised in row blocks. This keeps
    # O(n^2) pair arithmetic while bounding temporary memory to 256 x n.
    for start in range(0, n, 256):
        stop = min(n, start + 256)
        mask = rel[start:stop, None] > rel[None, :]
        delta = np.abs((gain[start:stop, None] - gain[None, :]) *
                       (discount[rank[start:stop], None] - discount[rank][None, :])) / max(ideal, 1e-12)
        rho = 1 / (1 + np.exp(np.clip(scores[start:stop, None] - scores[None, :], -40, 40)))
        pair = np.where(mask, delta * rho, 0.)
        gradient[start:stop] -= pair.sum(1)
        gradient += pair.sum(0)
    return gradient


def train_model(groups, validation_groups, *, seed=42, epochs=100, hidden=32, **options):
    """Train on in-memory legacy groups or disk-backed PackedQueries.

    Optimizer/early-stop/checkpoint options are defined in training.py.
    Inference schema and the independent rank/future heads remain compatible.
    """
    from .training import train_queries

    return train_queries(groups, validation_groups, seed=seed, epochs=epochs,
                         hidden=hidden, **options)
