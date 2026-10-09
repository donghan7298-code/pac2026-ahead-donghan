from pathlib import Path
import pytest
from pac_planning.demo import scene_from_file
from pac_planning import PlannerConfig, PlacementPlanner
from pac_planning.reference_backend import ReferenceBackend


@pytest.fixture
def scene():
    return scene_from_file(
        Path(__file__).parents[1] / "test_data/scenario_001_basic.json"
    )


@pytest.fixture
def planner(scene):
    context = scene[3]
    config = PlannerConfig(horizon=2, scenario_count=7)
    backend = ReferenceBackend(context, config)
    return PlacementPlanner(
        context=context,
        config=config,
        generate_candidates=backend.generate_candidates,
        validate_constraints=backend.validate_constraints,
    )
