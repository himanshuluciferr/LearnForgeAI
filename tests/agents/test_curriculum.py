"""Offline tests for curriculum-agent: pacing maths, prompt building, tidying, wiring."""

import pytest

from backend.agents import curriculum as curriculum_module
from backend.agents.curriculum import (
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
    IdentityStatus,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    SubjectAnalysis,
    TechnicalSubjectType,
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


def make_subject(**overrides) -> SubjectAnalysis:
    return SubjectAnalysis(
        **{
            "identity_status": IdentityStatus.CONFIRMED,
            "canonical_name": "Azure AI Search",
            "subject_type": TechnicalSubjectType.SERVICE,
            "description": "A managed search service.",
            "scope": [f"area {n}" for n in range(10)],
            "prerequisites": ["REST basics"],
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
    "areas,expected",
    [
        (0, MIN_CHAPTERS),  # nothing established still needs a real course
        (3, MIN_CHAPTERS),
        (10, 10),
        (15, 15),
        (40, MAX_CHAPTERS),  # a huge subject must not explode the chapter writer
    ],
)
def test_chapter_count_follows_the_areas_that_were_found_and_is_clamped(areas, expected):
    """It used to divide a model-supplied `estimated_hours`, measured swinging 40/120/40 on one
    subject — a 3x difference in course length from noise."""
    assert plan_chapter_count(make_subject(scope=[f"a{n}" for n in range(areas)])) == expected


def test_prompt_states_the_computed_count_rather_than_asking_for_a_guess():
    prompt = build_prompt(make_request(), make_subject(), [])

    assert "Produce exactly 10 chapters." in prompt


def test_prompt_carries_pacing_and_language():
    prompt = build_prompt(make_request(language="hi", daily_minutes=60), make_subject(), [])

    assert "60 minutes each" in prompt
    assert "Course language: hi" in prompt


def test_prompt_carries_what_the_documents_said_the_subject_is():
    prompt = build_prompt(make_request(), make_subject(), [])

    assert "What it is: A managed search service." in prompt


def test_prompt_tells_the_model_not_to_teach_the_prerequisites():
    prompt = build_prompt(make_request(), make_subject(), [])

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
    prompt = build_prompt(make_request(experience=level), make_subject(), [])

    assert "Where to start: The learner already uses" in prompt
    assert "Chapter 1 must start past all of that." in prompt


def test_sources_are_listed_for_grounding():
    sources = [
        ResearchSource(
            title="Azure AI Search docs",
            url="https://learn.microsoft.com/azure/search/",
            kind=ResourceKind.DOCS,
            text="s",
        )
    ]

    listed = format_sources(sources)

    assert "Azure AI Search docs" in listed
    assert "https://learn.microsoft.com/azure/search/" in listed
    assert "docs" in listed


def test_planning_is_given_titles_rather_than_the_whole_page():
    """The text goes to the chapter writer, which is where it is actually read."""
    source = ResearchSource(
        title="t", url="https://x.example/a", kind=ResourceKind.DOCS, text="secret body text"
    )

    assert "secret body text" not in format_sources([source])


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
        await plan_curriculum(make_request(), make_subject(), [])


@pytest.mark.asyncio
async def test_executor_stores_the_plan_and_forwards_state(monkeypatch):
    async def fake_plan(request, analysis, sources):
        return make_curriculum(4)

    monkeypatch.setattr(curriculum_module, "plan_curriculum", fake_plan)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.subject = make_subject()
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
    state.subject = make_subject()

    await CurriculumExecutor(id=WorkflowStep.CURRICULUM).run(state, CapturingContext())

    assert captured == [[]]


def test_four_nodes_report_thirty_percent():
    completed = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
    ]

    assert progress_percent(completed) == 30
