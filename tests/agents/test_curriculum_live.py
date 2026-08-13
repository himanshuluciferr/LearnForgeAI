"""Live tests for curriculum-agent. Opt in with `pytest -m live`."""

import pytest
import pytest_asyncio

from backend.agents.curriculum import plan_chapter_count, plan_curriculum
from backend.config.settings import get_settings
from backend.workflow.state import (
    ExperienceLevel,
    IdentityStatus,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    SubjectAnalysis,
    TechnicalSubjectType,
)

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

REQUEST = LearningRequest(
    is_learning_request=True,
    skill="Azure AI Search",
    experience=ExperienceLevel.BEGINNER,
    goal="add search to our intranet",
    daily_minutes=30,
)
SUBJECT = SubjectAnalysis(
    identity_status=IdentityStatus.CONFIRMED,
    canonical_name="Azure AI Search",
    subject_type=TechnicalSubjectType.SERVICE,
    description="A managed search service on Azure.",
    scope=[
        "indexes",
        "indexers",
        "skillsets",
        "analyzers",
        "scoring profiles",
        "vector search",
        "semantic ranking",
        "security",
    ],
    prerequisites=["REST basics", "An Azure subscription"],
)
SOURCES = [
    ResearchSource(
        title="Azure AI Search documentation",
        url="https://learn.microsoft.com/azure/search/",
        kind=ResourceKind.DOCS,
        text="Official product documentation.",
    )
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def curriculum():
    """One live call shared by every assertion below."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await plan_curriculum(REQUEST, SUBJECT, SOURCES)


async def test_chapter_count_follows_the_number_we_asked_for(curriculum):
    # The model is told an exact count; allow a little drift but not a different course.
    expected = plan_chapter_count(SUBJECT, SOURCES)

    assert abs(len(curriculum.chapters) - expected) <= 2


async def test_chapters_are_numbered_one_to_n(curriculum):
    numbers = [chapter.number for chapter in curriculum.chapters]

    assert numbers == list(range(1, len(numbers) + 1))


async def test_no_chapter_repeats_another(curriculum):
    titles = [chapter.title.strip().lower() for chapter in curriculum.chapters]

    assert len(set(titles)) == len(titles)


async def test_every_chapter_has_checkable_objectives(curriculum):
    assert all(2 <= len(chapter.objectives) <= 4 for chapter in curriculum.chapters)


async def test_the_course_is_named_after_the_skill(curriculum):
    assert "search" in curriculum.title.lower()
    assert len(curriculum.summary) > 40


async def test_prerequisites_are_not_taught_as_chapters(curriculum):
    titles = " ".join(chapter.title.lower() for chapter in curriculum.chapters)

    assert "rest basics" not in titles
