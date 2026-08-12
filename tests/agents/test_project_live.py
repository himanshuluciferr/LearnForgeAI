"""Live tests for project-agent. Opt in with `pytest -m live`. One real model call."""

import pytest
import pytest_asyncio

from backend.agents.project import LEVELS, design_projects
from backend.config.settings import get_settings
from backend.workflow.state import (
    ChapterOutline,
    Curriculum,
    ExperienceLevel,
    IdentityStatus,
    LearningRequest,
    SubjectAnalysis,
    TechnicalSubjectType,
)

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

REQUEST = LearningRequest(
    is_learning_request=True,
    skill="Azure AI Search",
    experience=ExperienceLevel.BEGINNER,
    goal="add search to an internal document portal",
    daily_minutes=45,
)
SUBJECT = SubjectAnalysis(
    identity_status=IdentityStatus.CONFIRMED,
    canonical_name="Azure AI Search",
    subject_type=TechnicalSubjectType.SERVICE,
    description="A managed search service on Azure.",
    prerequisites=["basic REST"],
)
CURRICULUM = Curriculum(
    title="Azure AI Search from indexing to ranking",
    summary="Build, query and tune a search index over your own documents.",
    chapters=[
        ChapterOutline(
            number=1,
            title="Creating an index and loading documents",
            objectives=["create a search index", "upload documents to it"],
        ),
        ChapterOutline(
            number=2,
            title="Querying: full text, filters and facets",
            objectives=["write a filtered query", "add facets to a result set"],
        ),
        ChapterOutline(
            number=3,
            title="Vector and hybrid search",
            objectives=["add a vector field", "run a hybrid query"],
        ),
        ChapterOutline(
            number=4,
            title="Tuning relevance with scoring profiles",
            objectives=["add a scoring profile", "measure a relevance change"],
        ),
    ],
)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def projects():
    """One live run of the whole step, shared by every assertion below."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await design_projects(REQUEST, SUBJECT, CURRICULUM)


async def test_the_ramp_is_complete_and_in_order(projects):
    assert [project.level for project in projects] == list(LEVELS)


async def test_the_three_projects_are_different_things(projects):
    titles = {project.title.strip().lower() for project in projects}

    assert len(titles) == 3, titles


async def test_no_project_is_named_after_its_difficulty(projects):
    """'Beginner Project' means the model described the slot instead of designing for it."""
    banned = ("beginner", "intermediate", "advanced", "capstone", "project 1")
    offenders = [
        project.title for project in projects if any(t in project.title.lower() for t in banned)
    ]

    assert offenders == [], offenders


async def test_every_project_has_something_to_build(projects):
    for project in projects:
        assert 3 <= len(project.features) <= 8, project.features
        assert len(project.milestones) >= 3, project.milestones
        assert project.stretch_goals


async def test_every_project_ships_a_folder_tree(projects):
    for project in projects:
        assert "── " in project.folder_structure, project.folder_structure
        assert len(project.folder_structure.splitlines()) >= 3


async def test_the_tree_is_ours_so_it_is_never_prose(projects):
    """The model returns paths; anything conversational would mean it ignored the schema.

    Checking the line prefix was not enough: a real run produced
    `data/pdfs/ (place PDF files here)`, which is a note but still renders behind a `└──`.
    The name itself has to be a name.
    """
    for project in projects:
        for line in project.folder_structure.splitlines():
            assert line.strip().startswith(("├──", "└──", "│")), line
            name = line.split("── ")[-1]
            assert "(" not in name, line


async def test_the_projects_are_buildable_from_the_course(projects):
    """Between them they should touch most of the course, not just chapter one."""
    vocabulary = ("index", "quer", "vector", "hybrid", "scoring", "relevance", "facet", "filter")
    text = " ".join(
        " ".join([project.title, project.summary] + project.features) for project in projects
    ).lower()

    hit = [word for word in vocabulary if word in text]

    assert len(hit) >= 4, hit


async def test_each_project_reads_like_a_cv_line(projects):
    for project in projects:
        assert len(project.summary.split()) >= 8, project.summary


async def test_ambition_increases_across_the_ramp(projects):
    """A rough proxy: the last project should not be the smallest of the three."""
    weights = [len(project.features) + len(project.milestones) for project in projects]

    assert weights[-1] >= weights[0], weights
