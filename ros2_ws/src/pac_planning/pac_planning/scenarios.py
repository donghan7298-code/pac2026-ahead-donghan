"""Inventory-proportional draws without replacement and seven stress strata."""

from dataclasses import dataclass
import hashlib
import random
from pac_common import BoxState
from .features import sku_box
from .geometry import volume

SCENARIO_KINDS = (
    "random_mixed",
    "large_late",
    "heavy_late",
    "small_early",
    "sku_run",
    "fragmentation",
    "stress",
)


@dataclass(frozen=True)
class FutureItem:
    box: BoxState
    consume_unseen: bool


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    kind: str
    items: tuple[FutureItem, ...]


def stable_seed(*parts):
    data = "\x1f".join(map(str, parts)).encode("utf-8")
    return int.from_bytes(hashlib.sha256(data).digest()[:8], "big")


def sample_scenarios(box, state, context, seed, count, horizon):
    if type(seed) is not int:
        raise ValueError("seed must be an integer")
    result = []
    for draw in range(count):
        kind = SCENARIO_KINDS[draw % len(SCENARIO_KINDS)]
        rng = random.Random(
            stable_seed(seed, state.state_version, box.box_id, draw)
        )
        # Preview boxes already have IDs and are excluded from remaining_by_sku.
        prefix = [
            FutureItem(b, False) for b in context.observed_preview[:horizon]
        ]
        available = dict(state.inventory.remaining_by_sku)
        needed = min(horizon - len(prefix), sum(available.values()))
        tail = []
        reserved = None
        choices = [s for s, n in sorted(available.items()) if n > 0]
        if (
            choices
            and needed > 1
            and kind in ("large_late", "heavy_late", "stress")
        ):
            if kind == "heavy_late":
                key = lambda s: context.catalog[s].weight_kg
            elif kind == "large_late":
                key = lambda s: volume(context.catalog[s].size)
            else:
                key = (
                    lambda s: context.catalog[s].size.x
                    * context.catalog[s].size.y
                    * (1 + context.catalog[s].weight_kg)
                )
            reserved = max(choices, key=key)
            available[reserved] -= 1
        slots = needed - int(reserved is not None)
        for _ in range(slots):
            ticket = rng.randrange(sum(available.values()))
            for sku, n in sorted(available.items()):
                if ticket < n:
                    tail.append(sku)
                    available[sku] -= 1
                    break
                ticket -= n
        cat = context.catalog
        if kind == "large_late":
            tail.sort(key=lambda s: volume(cat[s].size))
        elif kind == "heavy_late":
            tail.sort(key=lambda s: cat[s].weight_kg)
        elif kind == "small_early":
            tail.sort(key=lambda s: cat[s].size.x * cat[s].size.y)
        elif kind == "sku_run":
            tail.sort()
        elif kind in ("fragmentation", "stress"):
            ordered = sorted(tail, key=lambda s: cat[s].size.x / cat[s].size.y)
            tail = []
            while ordered:
                tail.append(ordered.pop(0))
                if ordered:
                    tail.append(ordered.pop())
        if reserved is not None:
            tail.append(reserved)
        for i, sku in enumerate(tail):
            identity = (
                f"__future__{state.state_version}_{box.box_id}_{draw}_{i}"
            )
            if identity in state.inventory.tracked_boxes:
                raise ValueError("Synthetic future ID collision")
            prefix.append(
                FutureItem(sku_box(cat[sku], identity, state.stamp_sec), True)
            )
        result.append(Scenario(f"scenario-{draw:03d}", kind, tuple(prefix)))
    return tuple(result)
