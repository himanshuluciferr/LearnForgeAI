"""Offline tests for curriculum-agent: pacing maths, prompt building, tidying, wiring."""

import pytest

from backend.agents import curriculum as curriculum_module
from backend.agents.curriculum import (
    HOURS_PER_CHAPTER,
    MAX_CHAPTERS,
    MIN_CHAPTERS,
    CurriculumExecutor,
    build_prompt,
    format_sources,
    plan_chapter_count,
    plan_curriculum,
    starting_point,
    tidy,
)
from backend.workflow.state import (
    ChapterOutline,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    SkillAnalysis,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value: Curriculum) -> None:
        self.value = value


class StubAgent:
    def __init__(self, curriculum: Curriculum) -> None:
        self.curriculum = curriculum

    async def run(self, prompt: str) -> StubResponse:
        return StubResponse(self.curriculum)


def make_request(**overrides) -> LearningRequest:
    return LearningRequest(
        **{
            "is_learning_request": True,
            "skill": "Azure AI Search",
            "experience": ExperienceLevel.BEGINNER,
            "goal": "add search to our intranet",
            "daily_minutes": 30,
            **overrides,
        }
    )


def make_analysis(**overrides) -> SkillAnalysis:
    return SkillAnalysis(
        **{
            "category": "Cloud",
            "difficulty": ExperienceLevel.INTERMEDIATE,
            "estimated_hours": 60,
            "prerequisites": ["REST basics"],
            "career_paths": ["Search Engineer"],
            **overrides,
        }
    )


def make_curriculum(count: int, start: int = 1) -> Curriculum:
    return Curriculum(
        title="t",
        summary="s",
        chapters=[
            ChapterOutline(number=n, title=f"c{n}", objectives=["do a thing"])
            for n in range(start, start + count)
        ],
    )


@pytest.mark.parametrize(
    "hours,expected",
    [
        (1, MIN_CHAPTERS),  # tiny topic still needs a real course
        (12, MIN_CHAPTERS),
        (60, 10),
        (90, 15),
        (500, MAX_CHAPTERS),  # huge topic must not explode the chapter writer
    ],
)
def test_chapter_count_is_derived_from_hours_and_clamped(hours, expected):
    assert plan_chapter_count(hours) == expected


def test_chapter_count_matches_the_stated_hours_per_chapter():
    assert plan_chapter_count(HOURS_PER_CHAPTER * 8) == 8


def test_prompt_states_the_computed_count_rather_than_asking_for_a_guess():
    prompt = build_prompt(make_request(), make_analysis(estimated_hours=60), [])

    assert "Produce exactly 10 chapters." in prompt


def test_prompt_carries_pacing_and_language():
    prompt = build_prompt(make_request(language="hi", daily_minutes=60), make_analysis(), [])

    assert "60 hours" in prompt
    assert "60 days" in prompt  # 60h at 60 min/day
    assert "Course language: hi" in prompt


def test_prompt_tells_the_model_not_to_teach_the_prerequisites():
    prompt = build_prompt(make_request(), make_analysis(), [])

    assert "Assumed knowledge, do not teach: REST basics" in prompt


def test_a_beginner_may_be_introduced_to_the_skill():
    guidance = starting_point(make_request(experience=ExperienceLevel.BEGINNER))

    assert "Chapter 1 may introduce" in guidance


@pytest.mark.parametrize(
    "level", [ExperienceLevel.INTERMEDIATE, ExperienceLevel.ADVANCED]
)
def test_experienced_learners_are_told_to_skip_the_orientation_chapter(level):
    """A general 'adapt to the level' rule was ignored by the model; a computed, concrete
    instruction was not. This asserts the concrete instruction reaches the prompt."""
    prompt = build_prompt(make_request(experience=level), make_analysis(), [])

    assert "Where to start: The learner already uses" in prompt
    assert "Chapter 1 must start past all of that." in prompt


def test_sources_are_listed_for_grounding():
    sources = [
        ResearchSource(
            title="Azure AI Search docs",
            url="https://learn.microsoft.com/azure/search/",
            kind=ResourceKind.DOCS,
            summary="s",
        )
    ]

    listed = format_sources(sources)

    assert "Azure AI Search docs" in listed
    assert "https://learn.microsoft.com/azure/search/" in listed
    assert "docs" in listed


def test_empty_research_becomes_an_explicit_instruction():
    listed = format_sources([])

    assert "None were verified" in listed
    assert listed.strip() != ""


def test_numbering_is_ours_not_the_models():
    curriculum = make_curriculum(3, start=7)

    assert [c.number for c in tidy(curriculum).chapters] == [1, 2, 3]


def test_too_many_chapters_are_trimmed_to_the_cap():
    tidied = tidy(make_curriculum(40))

    assert len(tidied.chapters) == MAX_CHAPTERS
    assert [c.number for c in tidied.chapters] == list(range(1, MAX_CHAPTERS + 1))


@pytest.mark.asyncio
async def test_a_curriculum_with_no_chapters_is_an_error(monkeypatch):
    empty = Curriculum(title="t", summary="s", chapters=[])
    monkeypatch.setattr(curriculum_module, "get_curriculum_agent", lambda: StubAgent(empty))

    with pytest.raises(ValueError, match="no chapters"):
        await plan_curriculum(make_request(), make_analysis(), [])


@pytest.mark.asyncio
async def test_executor_stores_the_plan_and_forwards_state(monkeypatch):
    async def fake_plan(request, analysis, sources):
        return make_curriculum(4)

    monkeypatch.setattr(curriculum_module, "plan_curriculum", fake_plan)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.skill_analysis = make_analysis()
    ctx = CapturingContext()

    await CurriculumExecutor(id=WorkflowStep.CURRICULUM).run(state, ctx)

    assert state.curriculum is not None
    assert len(state.curriculum.chapters) == 4
    assert state.completed_steps == [WorkflowStep.CURRICULUM]
    assert ctx.messages == [state]


@pytest.mark.asyncio
async def test_planning_still_runs_when_research_found_nothing(monkeypatch):
    captured: list[list[ResearchSource]] = []

    async def fake_plan(request, analysis, sources):
        captured.append(sources)
        return make_curriculum(4)

    monkeypatch.setattr(curriculum_module, "plan_curriculum", fake_plan)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.skill_analysis = make_analysis()

    await CurriculumExecutor(id=WorkflowStep.CURRICULUM).run(state, CapturingContext())

    assert captured == [[]]


def test_four_nodes_report_thirty_percent():
    completed = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.SKILL_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
    ]

    assert progress_percent(completed) == 30
