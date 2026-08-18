"""Offline tests for curriculum-agent: pacing maths, prompt building, tidying, wiring."""

import pytest

from backend.agents import curriculum as curriculum_module
from backend.agents.curriculum import (
    CHARS_PER_CHAPTER,
    MAX_CHAPTERS,
    MAX_TOPICS_PER_CHAPTER,
    MIN_CHAPTERS,
    MIN_TOPICS_PER_CHAPTER,
    CurriculumExecutor,
    build_prompt,
    format_sources,
    plan_chapter_count,
    plan_curriculum,
    plan_topic_count,
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
    TopicOutline,
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


def make_sources(chars: int, count: int = 3) -> list[ResearchSource]:
    """Chapter count is now capped by retrieved volume, so text length is part of the fixture."""
    return [
        ResearchSource(
            title=f"s{n}",
            url=f"https://example.com/{n}",
            kind=ResourceKind.DOCS,
            text="word " * (chars // count // 5),
        )
        for n in range(count)
    ]


def make_curriculum(count: int, start: int = 1, topics: int = 2) -> Curriculum:
    return Curriculum(
        title="t",
        summary="s",
        chapters=[
            ChapterOutline(
                number=n,
                title=f"c{n}",
                objectives=["do a thing"],
                topics=[
                    TopicOutline(title=f"t{n}.{m}", objectives=["use it"])
                    for m in range(1, topics + 1)
                ],
            )
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
    subject = make_subject(scope=[f"a{n}" for n in range(areas)])

    assert plan_chapter_count(subject, make_sources(chars=40 * CHARS_PER_CHAPTER)) == expected


def test_chapter_count_is_capped_by_how_much_text_was_actually_retrieved():
    """Measured: a Microsoft Agent Framework run planned 11 chapters over 75,073 chars, so every
    chapter shared the same thin material and the writer invented the API it could not read."""
    subject = make_subject(scope=[f"a{n}" for n in range(11)])

    plenty = plan_chapter_count(subject, make_sources(chars=11 * CHARS_PER_CHAPTER))
    thin = plan_chapter_count(subject, make_sources(chars=75_073))

    assert plenty == 11
    assert thin < plenty


def test_the_floor_still_holds_when_there_is_barely_any_evidence():
    """Fewer chapters than MIN_CHAPTERS is not a course. Empty research already fails upstream."""
    assert plan_chapter_count(make_subject(), make_sources(chars=10)) == MIN_CHAPTERS


def test_prompt_states_the_computed_count_rather_than_asking_for_a_guess():
    prompt = build_prompt(make_request(), make_subject(), make_sources(chars=40 * CHARS_PER_CHAPTER))

    assert "Produce exactly 10 chapters" in prompt


def test_prompt_carries_the_language():
    prompt = build_prompt(make_request(language="hi", daily_minutes=60), make_subject(), [])

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

    assert [c.number for c in tidy(curriculum, MAX_TOPICS_PER_CHAPTER).chapters] == [1, 2, 3]


def test_too_many_chapters_are_trimmed_to_the_cap():
    tidied = tidy(make_curriculum(40), MAX_TOPICS_PER_CHAPTER)

    assert len(tidied.chapters) == MAX_CHAPTERS
    assert [c.number for c in tidied.chapters] == list(range(1, MAX_CHAPTERS + 1))


def test_a_chapter_may_not_hold_more_topics_than_the_evidence_funds():
    """One writer call per topic, so the topic budget is what sets the cost of a course."""
    tidied = tidy(make_curriculum(2, topics=8), 3)

    assert [len(chapter.topics) for chapter in tidied.chapters] == [3, 3]


def test_a_chapter_with_fewer_topics_than_the_budget_is_left_alone():
    """The limit is a ceiling, not a quota — a thin area should stay short."""
    tidied = tidy(make_curriculum(2, topics=2), 6)

    assert [len(chapter.topics) for chapter in tidied.chapters] == [2, 2]


def test_topic_budget_follows_retrieved_volume_and_is_clamped():
    """Depth has to follow the evidence: `target_words` was one number for every chapter no
    matter the subject, so a large area and a small one came out the same length."""
    thin = plan_topic_count(5, make_sources(chars=75_073))
    plenty = plan_topic_count(5, make_sources(chars=400_000))

    assert thin == MIN_TOPICS_PER_CHAPTER
    assert plenty == MAX_TOPICS_PER_CHAPTER
    assert thin < plenty


def test_more_chapters_share_the_same_evidence_so_each_gets_fewer_topics():
    sources = make_sources(chars=200_000)

    assert plan_topic_count(5, sources) >= plan_topic_count(20, sources)


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
