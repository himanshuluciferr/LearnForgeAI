"""Live tests for research-agent. Opt in with `pytest -m live` — these hit the real model."""

import pytest
import pytest_asyncio

from backend.agents.research import MAX_SOURCES, gather_sources
from backend.config.settings import get_settings
from backend.workflow.state import ExperienceLevel, LearningRequest, ResourceKind, SkillAnalysis

# loop_scope matches the fixture scope so one live call can be shared by every test.
pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

PRIMARY = {ResourceKind.DOCS, ResourceKind.MICROSOFT_LEARN}


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def sources():
    """One live call shared by every assertion below — research is the slowest step."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await gather_sources(
        LearningRequest(
            is_learning_request=True,
            skill="Azure AI Search",
            experience=ExperienceLevel.BEGINNER,
            goal="add search to our intranet",
        ),
        SkillAnalysis(
            category="Cloud",
            difficulty=ExperienceLevel.INTERMEDIATE,
            estimated_hours=60,
            prerequisites=["REST basics"],
            career_paths=["Search Engineer"],
        ),
    )


async def test_returns_a_usable_number_of_sources(sources):
    assert 1 <= len(sources) <= MAX_SOURCES


async def test_every_surviving_url_is_https_and_real(sources):
    # verify_sources already fetched each one, so reaching here means they all answered.
    assert all(source.url.startswith("https://") for source in sources)


async def test_at_least_one_primary_source_is_included(sources):
    assert any(source.kind in PRIMARY for source in sources)


async def test_sources_arrive_best_first(sources):
    scores = [source.rank_score for source in sources]

    assert scores == sorted(scores, reverse=True)


async def test_every_source_explains_itself(sources):
    assert all(source.title and len(source.summary) > 20 for source in sources)


async def test_the_pages_are_about_the_skill_that_was_asked_for(sources):
    assert any(source.mentions_skill for source in sources)
