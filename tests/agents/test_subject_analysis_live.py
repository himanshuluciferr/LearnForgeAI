"""Live tests for subject-analysis-agent. Opt in with `pytest -m live` — these search and read.

The notebook measured this design over 13 subjects x 3 runs: 10 correct and stable, at 1.16
searches and 1.92 fetches per run. These pin the cases that decide whether the node is worth
having at all.
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from backend.agents.subject_analysis import investigate
from backend.config.settings import get_settings
from backend.workflow.state import IdentityStatus

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]


@pytest.fixture(autouse=True)
def require_endpoint():
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def agent_framework():
    """One investigation shared by several assertions — it costs a search and two fetches."""
    return await investigate("Microsoft Agent Framework")


async def test_the_subject_that_became_bot_framework_is_identified(agent_framework):
    """The original bug: a 20-chapter Bot Framework course, then a 13-chapter Clippy one.
    The model had never heard of Microsoft Agent Framework and returned its nearest neighbour."""
    analysis, documents, _ = agent_framework

    assert analysis.identity_status is IdentityStatus.CONFIRMED
    assert documents, "a confirmed identity must rest on pages we actually read"


async def test_the_identity_is_cited_to_documents_we_supplied(agent_framework):
    """Evidence carries a document number, so a URL the model could mistype cannot appear."""
    analysis, documents, _ = agent_framework

    assert analysis.evidence
    for item in analysis.evidence:
        assert 1 <= item.document_index <= len(documents)


async def test_the_sources_are_first_party(agent_framework):
    _, documents, _ = agent_framework
    read = " ".join(document.url for document in documents)

    assert "learn.microsoft.com" in read or "github.com/microsoft" in read


async def test_it_is_recognised_as_agent_software_not_a_desktop_assistant(agent_framework):
    """Plan-first without retrieval produced chapters on COM/ActiveX and lip-sync — the 1990s
    Microsoft Agent. The scope has to come from the documents instead."""
    analysis, _, _ = agent_framework
    scope = " ".join(analysis.scope).lower()

    assert "agent" in scope or "workflow" in scope
    assert "activex" not in scope and "lip-sync" not in scope


async def test_the_normal_path_is_cheap(agent_framework):
    """Measured: 11 of 13 subjects finished in one search and two fetches."""
    _, _, trace = agent_framework

    assert len(trace.searches) <= 2
    assert len(trace.fetched_urls) <= 4


async def test_an_invented_subject_is_refused():
    """Search still offers plotagon.com and broctagon.com, so a full result list is not proof
    that the subject exists."""
    analysis, _, trace = await investigate("Blorptagon SDK")

    assert analysis.identity_status is not IdentityStatus.CONFIRMED
    # Whatever the verdict, we must be able to show that we looked.
    assert trace.searches


async def test_a_first_party_site_neither_of_our_apis_can_reach():
    """rust-lang.org is invisible to the Learn and GitHub adapters, so this only works if
    generic discovery runs first."""
    analysis, documents, _ = await investigate("Rust")

    assert analysis.identity_status is IdentityStatus.CONFIRMED
    assert documents
