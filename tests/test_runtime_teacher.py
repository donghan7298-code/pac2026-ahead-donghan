"""Real team EMS / runtime label adapter, never the reference-backend mock."""

from dataclasses import replace

import pytest

pytest.importorskip("pac_runtime")
from pac_candidates import CandidateBackend
from pac_common import plain
from pac_planning import PlannerConfig
from pac_planning.team_training import RuntimeTeacherRanker


def test_runtime_teacher_preserves_snapshot_real_ems_and_complete_order(scene):
    _, box, state, context = scene
    before = plain(state)
    backend = CandidateBackend(context)
    valid = backend.candidate_set(box, state).valid
    teacher = RuntimeTeacherRanker(PlannerConfig(horizon=1, scenario_count=1), "S", "inventory")
    ordered = teacher(valid, box, state, backend)
    assert ordered and teacher.groups
    assert len(ordered) == len(teacher.groups[0]["rows"])
    assert all(r["geometry_source"] == "EMS_SUPPLIED" for r in teacher.groups[0]["rows"])
    assert teacher(valid, box, state, backend) == ordered
    assert len(teacher.groups) == 1 and plain(state) == before
    # Candidate source/config changes invalidate a continuing label collection.
    changed = CandidateBackend(context, replace(backend.config, cache_size=backend.config.cache_size + 1))
    with pytest.raises(ValueError, match="backend changed"):
        teacher(changed.candidate_set(box, state).valid, box, state, changed)
