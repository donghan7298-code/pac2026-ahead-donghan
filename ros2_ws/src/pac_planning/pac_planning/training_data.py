"""Disk-backed candidate queries for repeatable, larger low-level training.

One JSONL shard is one complete simulated episode. Packing streams shards
twice and writes NumPy memmaps; it never builds a giant list of feature dicts.
The model only receives the measured 45 features and rollout teacher targets.
"""

import gzip
import hashlib
import json
from pathlib import Path

import numpy as np

from .features import FEATURE_NAMES
from .model import OUTPUT_NAMES


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2,
                              allow_nan=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_queries(paths):
    for path in paths:
        with gzip.open(path, "rt", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    yield json.loads(line)


def query_digest(group):
    # The same snapshot can have different Monte Carlo targets in another
    # collection pass. Keep those samples; drop only identical complete rows.
    raw = json.dumps(group, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validate_query(group):
    rows = group["rows"]
    if not rows or not isinstance(group["base_group"], str):
        raise ValueError("Query needs rows and a string inventory-group fingerprint")
    x = np.asarray([r["features"] for r in rows], dtype=float)
    y = np.asarray([[r["future"][n] for n in OUTPUT_NAMES] for r in rows], dtype=float)
    rel = np.asarray([r["teacher_score"] for r in rows], dtype=float)
    if x.shape != (len(rows), len(FEATURE_NAMES)) or y.shape != (len(rows), 4):
        raise ValueError("Bad teacher feature/output shape")
    if not all(np.isfinite(a).all() for a in (x, y, rel)):
        raise ValueError("Non-finite teacher label")
    if np.any((y < 0) | (y > 1)) or np.any((rel < 0) | (rel > 1)):
        raise ValueError("Teacher targets must lie in [0, 1]")
    if np.any(y[:, 1] > y[:, 0] + 1e-7):
        raise ValueError("CVaR cannot exceed the mean")
    if any(r.get("geometry_source") != "EMS_SUPPLIED" for r in rows):
        raise ValueError("Actual EMS evidence is required for every training row")
    if len({r["candidate_id"] for r in rows}) != len(rows):
        raise ValueError("Duplicate candidate inside a query")
    return x, y, rel


class PackedQueries:
    """Flat arrays + query offsets; arrays stay disk-backed during training."""

    def __init__(self, directory):
        self.root = Path(directory)
        self.meta = json.loads((self.root / "metadata.json").read_text(encoding="utf-8"))
        if self.meta.get("format") != "ahead-packed-queries-v1":
            raise ValueError("Unknown packed query format")
        if self.meta["feature_names"] != list(FEATURE_NAMES):
            raise ValueError("Packed feature order differs from the planner")
        if self.meta["output_names"] != list(OUTPUT_NAMES):
            raise ValueError("Packed output order differs from the planner")
        for name in ("features", "future", "relevance", "offsets"):
            path = self.root / (name + ".npy")
            if file_digest(path) != self.meta["array_sha256"][name]:
                raise ValueError("Packed query integrity mismatch: " + name)
            setattr(self, name, np.load(path, mmap_mode="r", allow_pickle=False))
        n = self.meta["candidates"]
        q = self.meta["queries"]
        if (self.features.shape != (n, len(FEATURE_NAMES)) or self.future.shape != (n, 4)
                or self.relevance.shape != (n,) or self.offsets.shape != (q + 1,)
                or self.offsets[0] != 0 or self.offsets[-1] != n
                or np.any(np.diff(self.offsets) <= 0)):
            raise ValueError("Invalid packed query shapes/offsets")
        self.base_groups = set(self.meta["base_groups"])
        self.fingerprint = file_digest(self.root / "metadata.json")

    def __len__(self):
        return self.meta["queries"]


def pack_queries(paths, directory):
    """Stream complete shards into a new pack; exact duplicate queries are dropped."""
    paths = [Path(p) for p in paths]
    root = Path(directory)
    if root.exists() and any(root.iterdir()):
        known_partial = {"features.npy", "future.npy", "relevance.npy", "offsets.npy", "metadata.json.tmp"}
        if (root / "metadata.json").exists() or any(p.name not in known_partial for p in root.iterdir()):
            raise ValueError("Packed output must be new, empty or an unfinished array pack")
    root.mkdir(parents=True, exist_ok=True)
    seen, base_groups, offsets = set(), set(), [0]
    for group in read_queries(paths):
        key = query_digest(group)
        if key in seen:
            continue
        validate_query(group)
        seen.add(key)
        base_groups.add(group["base_group"])
        offsets.append(offsets[-1] + len(group["rows"]))
    if not seen:
        raise ValueError("No complete teacher queries")
    n = offsets[-1]
    shapes = {"features": (n, len(FEATURE_NAMES)), "future": (n, 4), "relevance": (n,)}
    # Compact storage for large datasets; the trainer converts batches and
    # all Adam parameters/moments to float64. Hard geometry never uses this pack.
    arrays = {name: np.lib.format.open_memmap(root / (name + ".npy"), mode="w+",
                                             dtype="float32", shape=shape)
              for name, shape in shapes.items()}
    written, index = set(), 0
    for group in read_queries(paths):
        key = query_digest(group)
        if key in written:
            continue
        x, y, rel = validate_query(group)
        stop = index + len(x)
        arrays["features"][index:stop] = x
        arrays["future"][index:stop] = y
        arrays["relevance"][index:stop] = rel
        written.add(key)
        index = stop
    for a in arrays.values():
        a.flush()
    np.save(root / "offsets.npy", np.asarray(offsets, dtype="int64"), allow_pickle=False)
    meta = {"format": "ahead-packed-queries-v1", "queries": len(seen), "candidates": n,
            "feature_names": list(FEATURE_NAMES), "output_names": list(OUTPUT_NAMES),
            "base_groups": sorted(base_groups),
            "source_sha256": {str(p.resolve()): file_digest(p) for p in paths},
            "array_sha256": {name: file_digest(root / (name + ".npy"))
                             for name in (*shapes, "offsets")}}
    atomic_json(root / "metadata.json", meta)
    return PackedQueries(root)
