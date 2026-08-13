"""Live tests for research-agent. Opt in with `pytest -m live` — these hit the real model."""

import pytest
import pytest_asyncio

from backend.agents.chapter import format_sources
from backend.agents.research import MAX_SOURCES, gather_sources
from backend.config.settings import get_settings
from backend.services.page_fetch import MIN_WORDS
from backend.workflow.state import (
    ChapterOutline,
    ExperienceLevel,
    IdentityStatus,
    LearningRequest,
    ResourceKind,
    SubjectAnalysis,
    TechnicalSubjectType,
)

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
        SubjectAnalysis(
            identity_status=IdentityStatus.CONFIRMED,
            canonical_name="Azure AI Search",
            subject_type=TechnicalSubjectType.SERVICE,
            description="A managed search service on Azure.",
            scope=["indexes", "indexers", "skillsets"],
            prerequisites=["REST basics"],
        ),
    )


async def test_returns_a_usable_number_of_sources(sources):
    assert 1 <= len(sources) <= MAX_SOURCES


async def test_every_source_carries_the_text_of_a_page_we_read(sources):
    """The acceptance criterion for the whole node. Until this held, `summary` was written by
    the model that proposed the URL, so a chapter's "source" was its own recollection."""
    assert all(source.text for source in sources)
    assert all(source.words > MIN_WORDS for source in sources)


async def test_the_text_is_the_page_rather_than_its_title(sources):
    """A landing page yields a couple of hundred words of navigation; a page worth writing
    from yields real prose."""
    assert max(source.words for source in sources) > 400


async def test_every_surviving_url_is_https_and_real(sources):
    # Each one was fetched to get its text, so reaching here means they all answered.
    assert all(source.url.startswith("https://") for source in sources)


async def test_at_least_one_primary_source_is_included(sources):
    assert any(source.kind in PRIMARY for source in sources)


async def test_sources_arrive_best_first(sources):
    scores = [source.rank_score for source in sources]

    assert scores == sorted(scores, reverse=True)


async def test_the_writer_receives_text_chosen_for_its_own_chapter(sources):
    """Follows the text one step further, into the prompt chapter-agent actually sees — and
    checks it is the part about that chapter, not the top of every page."""
    outline = ChapterOutline(
        number=4,
        title="Skillsets and AI enrichment",
        objectives=["attach a skillset to an indexer"],
    )

    prompt = format_sources(sources, outline)

    assert len(prompt) > 1_000
    assert "skillset" in prompt.lower()
