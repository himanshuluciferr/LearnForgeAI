"""Live tests for skill-analysis-agent. Opt in with `pytest -m live`."""

import pytest

from backend.agents.skill_analysis import analyse_skill
from backend.config.settings import get_settings
from backend.workflow.state import ExperienceLevel, LearningRequest

pytestmark = [pytest.mark.live, pytest.mark.asyncio]


@pytest.fixture(autouse=True)
def require_endpoint():
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")


async def test_sizes_a_hard_skill_realistically():
    analysis = await analyse_skill(
        LearningRequest(
            is_learning_request=True,
            skill="Kubernetes operators",
            experience=ExperienceLevel.INTERMEDIATE,
            goal="write my own operator",
        )
    )

    assert analysis.difficulty == ExperienceLevel.ADVANCED
    assert analysis.estimated_hours > 0
    assert analysis.prerequisites
    assert analysis.career_paths


async def test_difficulty_describes_the_skill_not_the_learner():
    """A beginner asking about a hard topic must not make the topic 'beginner'."""
    analysis = await analyse_skill(
        LearningRequest(
            is_learning_request=True,
            skill="Kubernetes",
            experience=ExperienceLevel.BEGINNER,
            goal="run production workloads",
        )
    )

    assert analysis.difficulty != ExperienceLevel.BEGINNER


async def test_an_easy_skill_needs_no_prerequisites():
    analysis = await analyse_skill(
        LearningRequest(
            is_learning_request=True,
            skill="Markdown",
            experience=ExperienceLevel.BEGINNER,
            goal="write good README files",
        )
    )

    assert analysis.prerequisites == []
    assert analysis.estimated_hours <= 20
