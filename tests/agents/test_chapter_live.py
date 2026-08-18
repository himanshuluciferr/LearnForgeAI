"""Live tests for chapter-agent. Opt in with `pytest -m live`. Three real model calls."""

import pytest
import pytest_asyncio

from backend.agents.chapter import render_topic, target_words, write_chapters
from backend.config.settings import get_settings
from backend.workflow.state import (
    ChapterOutline,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    TopicOutline,
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
            topics=[
                TopicOutline(
                    title="Search services and their endpoints",
                    objectives=["provision a search service"],
                ),
                TopicOutline(
                    title="API keys and authenticated requests",
                    objectives=["call the service with an API key"],
                ),
            ],
        ),
        ChapterOutline(
            number=2,
            title="Design an index for intranet documents",
            objectives=["define field types", "choose searchable and filterable fields"],
            topics=[
                TopicOutline(
                    title="Field types in an index schema",
                    objectives=["define field types"],
                ),
                TopicOutline(
                    title="Searchable, filterable and facetable attributes",
                    objectives=["choose searchable and filterable fields"],
                ),
            ],
        ),
    ],
)
SOURCES = [
    ResearchSource(
        title="Azure AI Search documentation",
        url="https://learn.microsoft.com/azure/search/",
        kind=ResourceKind.DOCS,
        text="Official product documentation covering indexes, indexers and queries.",
    )
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chapters():
    """One live run of the whole step, shared by every assertion below."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await write_chapters(REQUEST, CURRICULUM, SOURCES)


async def test_every_planned_chapter_comes_back_in_order(chapters):
    assert [chapter.number for chapter in chapters] == [1, 2]


async def test_titles_are_the_plans_not_the_models(chapters):
    assert [chapter.title for chapter in chapters] == [c.title for c in CURRICULUM.chapters]


async def test_every_planned_topic_comes_back_numbered(chapters):
    assert [topic.label for topic in chapters[0].topics] == ["1.1", "1.2"]
    assert [topic.title for topic in chapters[0].topics] == [
        t.title for t in CURRICULUM.chapters[0].topics
    ]


async def test_every_topic_carries_the_parts_a_reader_looks_for(chapters):
    topics = [topic for chapter in chapters for topic in chapter.topics]

    assert all(topic.what_it_is.strip() for topic in topics)
    assert all(topic.why_it_matters.strip() for topic in topics)
    assert all(topic.how_to_use.strip() for topic in topics)


async def test_why_it_matters_is_not_a_second_definition(chapters):
    """It exists to state the problem, so repeating the definition wastes the slot."""
    for topic in (t for chapter in chapters for t in chapter.topics):
        assert topic.why_it_matters.strip() != topic.what_it_is.strip()


async def test_bodies_are_split_into_named_topics(chapters):
    # Headings are rendered by us from the plan, so this asserts every topic reached the page.
    assert all(
        chapter.body_markdown.count("## ") == len(chapter.topics) for chapter in chapters
    )


async def test_topic_headings_name_a_subject_rather_than_a_role(chapters):
    """Measured: left to invent its own headings the writer named them after its own job —
    'What you'll be able to do (and what goes wrong without this)'."""
    headings = [
        line.removeprefix("## ").strip().lower()
        for chapter in chapters
        for line in chapter.body_markdown.splitlines()
        if line.startswith("## ")
    ]

    assert not any(
        phrase in heading
        for heading in headings
        for phrase in ("what you'll", "step-by-step", "in plain terms", "introduction")
    ), headings


async def test_a_technical_chapter_shows_real_code_or_commands(chapters):
    assert any("```" in chapter.body_markdown for chapter in chapters)


async def test_each_chapter_has_revisable_points_and_real_exercises(chapters):
    assert all(3 <= len(chapter.key_points) <= 6 for chapter in chapters)
    assert all(2 <= len(chapter.exercises) <= 4 for chapter in chapters)


async def test_length_stays_near_the_target(chapters):
    # Generous band: the point is to catch a one-paragraph stub or a runaway essay.
    for chapter, outline in zip(chapters, CURRICULUM.chapters):
        for topic, planned in zip(chapter.topics, outline.topics):
            target = target_words(planned.objectives)
            count = len(render_topic(topic).split())
            assert target * 0.4 <= count <= target * 3, (topic.label, count, target)


async def test_chapters_are_not_copies_of_each_other(chapters):
    bodies = {chapter.body_markdown.strip() for chapter in chapters}

    assert len(bodies) == len(chapters)
