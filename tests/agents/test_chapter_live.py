"""Live tests for chapter-agent. Opt in with `pytest -m live`. Three real model calls."""

import pytest
import pytest_asyncio

from backend.agents.chapter import target_words, write_chapters
from backend.config.settings import get_settings
from backend.workflow.state import (
    ChapterOutline,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    ResourceKind,
)

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

DAILY_MINUTES = 30

REQUEST = LearningRequest(
    is_learning_request=True,
    skill="Azure AI Search",
    experience=ExperienceLevel.BEGINNER,
    goal="add search to our intranet",
    daily_minutes=DAILY_MINUTES,
)
CURRICULUM = Curriculum(
    title="Build Intranet Search with Azure AI Search",
    summary="Stand up a working search experience over internal documents.",
    chapters=[
        ChapterOutline(
            number=1,
            title="Create a search service and connect to it",
            objectives=["provision a search service", "call it with an API key"],
        ),
        ChapterOutline(
            number=2,
            title="Design an index for intranet documents",
            objectives=["define field types", "choose searchable and filterable fields"],
        ),
        ChapterOutline(
            number=3,
            title="Load documents with an indexer",
            objectives=["configure a data source", "run and monitor an indexer"],
        ),
    ],
)
SOURCES = [
    ResearchSource(
        title="Azure AI Search documentation",
        url="https://learn.microsoft.com/azure/search/",
        kind=ResourceKind.DOCS,
        summary="Official product documentation covering indexes, indexers and queries.",
    )
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chapters():
    """One live run of the whole step, shared by every assertion below."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await write_chapters(REQUEST, CURRICULUM, SOURCES)


async def test_every_planned_chapter_comes_back_in_order(chapters):
    assert [chapter.number for chapter in chapters] == [1, 2, 3]


async def test_titles_are_the_plans_not_the_models(chapters):
    assert [chapter.title for chapter in chapters] == [c.title for c in CURRICULUM.chapters]


async def test_bodies_are_split_into_named_sections(chapters):
    # Headings are rendered by us, so this asserts the model returned several real sections.
    assert all(3 <= chapter.body_markdown.count("## ") <= 6 for chapter in chapters)


async def test_sections_are_not_generically_titled(chapters):
    headings = [
        line.removeprefix("## ").strip().lower()
        for chapter in chapters
        for line in chapter.body_markdown.splitlines()
        if line.startswith("## ")
    ]

    assert not {"introduction", "overview", "conclusion", "summary"} & set(headings), headings


async def test_a_technical_chapter_shows_real_code_or_commands(chapters):
    assert any("```" in chapter.body_markdown for chapter in chapters)


async def test_each_chapter_has_revisable_points_and_real_exercises(chapters):
    assert all(3 <= len(chapter.key_points) <= 6 for chapter in chapters)
    assert all(2 <= len(chapter.exercises) <= 4 for chapter in chapters)


async def test_length_stays_near_the_target(chapters):
    # Generous band: the point is to catch a one-paragraph stub or a runaway essay.
    target = target_words(DAILY_MINUTES)
    counts = [len(chapter.body_markdown.split()) for chapter in chapters]

    assert all(target * 0.4 <= count <= target * 3 for count in counts), counts


async def test_chapters_are_not_copies_of_each_other(chapters):
    bodies = {chapter.body_markdown.strip() for chapter in chapters}

    assert len(bodies) == len(chapters)
