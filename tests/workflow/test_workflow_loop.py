"""Runs the real graph with every model call stubbed.

The graph-shape tests assert what is declared; this asserts what actually happens when a
course is rewritten. The duplicate-tail bug that prompted these tests was invisible to
both the shape tests and the agent tests — only a run showed it.
"""

from __future__ import annotations

import pytest

from backend.agents import chapter as chapter_mod
from backend.agents import curriculum as curriculum_mod
from backend.agents import practice as practice_mod
from backend.agents import project as project_mod
from backend.agents import quiz as quiz_mod
from backend.agents import requirement as requirement_mod
from backend.agents import research as research_mod
from backend.agents import review as review_mod
from backend.agents import skill_analysis as skill_mod
from backend.workflow.state import (
    MAX_REVISIONS,
    Chapter,
    ChapterOutline,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    ReviewResult,
    SkillAnalysis,
    WorkflowStep,
)
from backend.workflow.workflow import build_workflow

CHAPTERS = [Chapter(number=n, title=f"t{n}", body_markdown="b") for n in (1, 2)]
CURRICULUM = Curriculum(
    title="c",
    summary="s",
    chapters=[ChapterOutline(number=n, title=f"t{n}", objectives=["a", "b"]) for n in (1, 2)],
)
REQUEST = LearningRequest(
    is_learning_request=True,
    skill="s",
    experience=ExperienceLevel.BEGINNER,
    goal="g",
    daily_minutes=30,
)
ANALYSIS = SkillAnalysis(
    category="Cloud", difficulty=ExperienceLevel.BEGINNER, estimated_hours=10
)


def returning(value):
    async def call(*args, **kwargs):
        return value

    return call


@pytest.fixture
def stub_agents(monkeypatch):
    """Every model call replaced, so a whole course runs in milliseconds."""
    monkeypatch.setattr(requirement_mod, "extract_requirement", returning(REQUEST))
    monkeypatch.setattr(skill_mod, "analyse_skill", returning(ANALYSIS))
    monkeypatch.setattr(research_mod, "gather_sources", returning([]))
    monkeypatch.setattr(curriculum_mod, "plan_curriculum", returning(CURRICULUM))
    monkeypatch.setattr(chapter_mod, "write_chapters", returning(CHAPTERS))
    monkeypatch.setattr(chapter_mod, "rewrite_chapters", returning(CHAPTERS[:1]))
    monkeypatch.setattr(practice_mod, "set_practice", returning([]))
    monkeypatch.setattr(project_mod, "design_projects", returning([]))
    monkeypatch.setattr(quiz_mod, "build_quizzes", returning([]))


def failing_reviews(count: int):
    """A reviewer that rejects the first `count` drafts, then accepts."""
    calls = 0

    async def review_course(request, curriculum, chapters):
        nonlocal calls
        calls += 1
        if calls <= count:
            return ReviewResult(score=40, regenerate_chapters=[1], chapter_issues={1: ["thin"]})
        return ReviewResult(score=90)

    return review_course


async def run_graph(monkeypatch, rejections: int) -> tuple[CourseState, list[str]]:
    monkeypatch.setattr(review_mod, "review_course", failing_reviews(rejections))
    state = CourseState(job_id="j", user_id="u", prompt="teach me x")
    visited: list[str] = []

    async for event in build_workflow().run(state, stream=True):
        if event.type == "executor_completed":
            visited.append(str(event.executor_id))

    return state, visited


@pytest.mark.asyncio
async def test_a_clean_course_runs_every_step_once(stub_agents, monkeypatch):
    state, visited = await run_graph(monkeypatch, rejections=0)

    assert visited == [str(step) for step in WorkflowStep if step != WorkflowStep.PUBLISHER]
    assert state.revision_count == 0


@pytest.mark.asyncio
async def test_a_rewrite_does_not_drag_the_rest_of_the_course_round_with_it(
    stub_agents, monkeypatch
):
    """The bug this file exists for: practice, project and quiz each ran twice."""
    _, visited = await run_graph(monkeypatch, rejections=2)

    assert visited.count(str(WorkflowStep.CHAPTER)) == 1 + MAX_REVISIONS
    assert visited.count(str(WorkflowStep.REVIEW)) == 1 + MAX_REVISIONS
    for step in (WorkflowStep.PRACTICE, WorkflowStep.PROJECT, WorkflowStep.QUIZ):
        assert visited.count(str(step)) == 1, visited


@pytest.mark.asyncio
async def test_a_course_the_reviewer_never_likes_still_finishes(stub_agents, monkeypatch):
    state, visited = await run_graph(monkeypatch, rejections=99)

    assert state.revision_count == MAX_REVISIONS
    assert visited.count(str(WorkflowStep.QUIZ)) == 1
    assert state.percent == 97


@pytest.mark.asyncio
async def test_the_exercises_are_built_from_chapters_the_reviewer_passed(
    stub_agents, monkeypatch
):
    _, visited = await run_graph(monkeypatch, rejections=1)

    assert visited.index(str(WorkflowStep.REVIEW)) < visited.index(str(WorkflowStep.PRACTICE))
