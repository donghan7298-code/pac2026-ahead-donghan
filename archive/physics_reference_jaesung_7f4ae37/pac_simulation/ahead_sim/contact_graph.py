from __future__ import annotations

from typing import Any, Dict, List, Mapping, Sequence

import networkx as nx


def build_contact_graph(
    contacts: Sequence[Dict[str, Any]],
    boxes_by_id: Mapping[str, Dict[str, Any]],
    min_force_n: float = 0.05,
) -> Dict[str, Any]:
    """Create an actual contact-force graph from the current Bullet contacts.

    Edge direction is upper body -> supporting lower body.
    Edge weight is the sum of Bullet normal forces for that contact pair.
    """

    graph = nx.DiGraph()
    graph.add_node("PALLET", kind="pallet")
    for box_id, b in boxes_by_id.items():
        graph.add_node(
            box_id,
            kind="box",
            mass_kg=float(b["mass_kg"]),
            z_m=float(b["position_m"][2]),
        )

    for c in contacts:
        force = float(c["normal_force_n"])
        if force < min_force_n:
            continue

        a = str(c["name_a"])
        b = str(c["name_b"])

        valid = set(boxes_by_id) | {"PALLET"}
        if a not in valid or b not in valid or a == b:
            continue

        if a == "PALLET":
            upper, lower = b, a
        elif b == "PALLET":
            upper, lower = a, b
        else:
            za = float(boxes_by_id[a]["position_m"][2])
            zb = float(boxes_by_id[b]["position_m"][2])
            if za >= zb:
                upper, lower = a, b
            else:
                upper, lower = b, a

        if graph.has_edge(upper, lower):
            graph[upper][lower]["normal_force_n"] += force
            graph[upper][lower]["contact_points"] += 1
        else:
            graph.add_edge(
                upper,
                lower,
                normal_force_n=force,
                contact_points=1,
            )

    edges: List[Dict[str, Any]] = []
    for source, target, d in graph.edges(data=True):
        edges.append(
            {
                "source": source,
                "target": target,
                "normal_force_n": float(d["normal_force_n"]),
                "contact_points": int(d["contact_points"]),
            }
        )

    per_box = {}
    for box_id in boxes_by_id:
        load_from_above = sum(
            float(d["normal_force_n"])
            for _, _, d in graph.in_edges(box_id, data=True)
        )
        support_force_below = sum(
            float(d["normal_force_n"])
            for _, _, d in graph.out_edges(box_id, data=True)
        )
        per_box[box_id] = {
            "load_from_above_n": load_from_above,
            "support_force_below_n": support_force_below,
        }

    return {
        "nodes": list(graph.nodes()),
        "edges": edges,
        "per_box": per_box,
    }
