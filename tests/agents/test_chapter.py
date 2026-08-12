"""Offline tests for chapter-agent: continuity context, length, concurrency, wiring."""

import asyncio

import pytest

from backend.agents import chapter as chapter_module
from backend.agents.fanout import MAX_ATTEMPTS
from backend.agents.chapter import (
    MAX_CONCURRENT_CHAPTERS,
    MAX_WORDS,
    MIN_WORDS,
    WORDS_PER_SESSION_MINUTE,
    ChapterExecutor,
    assemble,
    build_prompt,
    coming_later,
    covered_so_far,
    format_sources,
    render_body,
    rewrite_chapters,
    splice,
    target_words,
    write_chapter,
    write_chapters,
)
from backend.workflow.state import (
    Chapter,
    ChapterDraft,
    ChapterOutline,
    ChapterSection,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    ReviewResult,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value: ChapterDraft) -> None:
        self.value = value


class StubAgent:
    """Records how many calls are in flight at once, so concurrency can be asserted."""

    def __init__(self, draft: ChapterDraft | None = None, delay: float = 0.0, fail_on: str = "") -> None:
        self.draft = draft if draft is not None else make_draft()
        self.delay = delay
        self.fail_on = fail_on
        self.prompts: list[str] = []
        self.in_flight = 0
        self.peak = 0

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if self.fail_on and self.fail_on in prompt:
                raise RuntimeError("model call failed")
            return StubResponse(self.draft)
        finally:
            self.in_flight -= 1


def make_draft(**overrides) -> ChapterDraft:
    return ChapterDraft(
        **{
            "sections": [ChapterSection(heading="Something", markdown="Body text.")],
            "key_points": ["a point"],
            "exercises": ["do a thing"],
            **overrides,
        }
    )


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


def make_curriculum(count: int) -> Curriculum:
    return Curriculum(
        title="Search course",
        summary="s",
        chapters=[
            ChapterOutline(
                number=n, title=f"Chapter topic {n}", objectives=[f"do thing {n}", f"build {n}"]
            )
            for n in range(1, count + 1)
        ],
    )


def use_stub(monkeypatch, agent: StubAgent) -> StubAgent:
    monkeypatch.setattr(chapter_module, "get_chapter_agent", lambda: agent)
    return agent


# --- length ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "daily_minutes,expected",
    [
        (5, MIN_WORDS),  # a five-minute learner still needs a whole chapter
        (30, 750),
        (60, 1500),
        (480, MAX_WORDS),  # a long sitting must not produce an unreadable wall of text
    ],
)
def test_chapter_length_follows_the_daily_sitting_and_is_clamped(daily_minutes, expected):
    assert target_words(daily_minutes) == expected


def test_length_matches_the_stated_words_per_minute():
    assert target_words(40) == 40 * WORDS_PER_SESSION_MINUTE


def test_prompt_states_the_computed_length_rather_than_asking_for_a_guess():
    prompt = build_prompt(make_request(daily_minutes=60), make_curriculum(3), make_curriculum(3).chapters[0], [])

    assert "Target length: about 1500 words." in prompt


# --- continuity between independent calls ------------------------------------------


def test_the_first_chapter_is_told_nothing_has_been_taught_yet():
    curriculum = make_curriculum(4)

    guidance = covered_so_far(curriculum, curriculum.chapters[0])

    assert "This is the first chapter" in guidance


def test_a_later_chapter_is_told_exactly_what_earlier_ones_taught():
    curriculum = make_curriculum(4)

    guidance = covered_so_far(curriculum, curriculum.chapters[2])

    assert "Ch 1 Chapter topic 1" in guidance
    assert "Ch 2 Chapter topic 2" in guidance
    assert "do thing 1" in guidance  # objectives, not just titles
    assert "Ch 3" not in guidance  # never itself
    assert "Ch 4" not in guidance  # never the future


def test_the_last_chapter_is_told_to_close_the_course():
    curriculum = make_curriculum(4)

    guidance = coming_later(curriculum, curriculum.chapters[-1])

    assert "final chapter" in guidance


def test_a_middle_chapter_is_told_what_to_leave_alone():
    curriculum = make_curriculum(4)

    guidance = coming_later(curriculum, curriculum.chapters[1])

    assert "Ch 3 Chapter topic 3" in guidance
    assert "Ch 4 Chapter topic 4" in guidance
    assert "Ch 1" not in guidance


def test_prompt_places_the_chapter_in_the_course():
    curriculum = make_curriculum(7)

    prompt = build_prompt(make_request(), curriculum, curriculum.chapters[3], [])

    assert "Write chapter 4 of 7: Chapter topic 4" in prompt
    assert "Course: Search course" in prompt
    assert "- do thing 4" in prompt


def test_prompt_carries_the_course_language():
    curriculum = make_curriculum(2)

    prompt = build_prompt(make_request(language="hi"), curriculum, curriculum.chapters[0], [])

    assert "Course language: hi" in prompt


def test_sources_reach_the_writer_with_their_summaries():
    sources = [
        ResearchSource(
            title="Azure AI Search docs",
            url="https://learn.microsoft.com/azure/search/",
            kind=ResourceKind.DOCS,
            summary="Official product documentation.",
        )
    ]

    listed = format_sources(sources)

    assert "Azure AI Search docs" in listed
    assert "https://learn.microsoft.com/azure/search/" in listed
    assert "Official product documentation." in listed


def test_no_sources_becomes_an_instruction_to_stay_conservative():
    listed = format_sources([])

    assert "None were verified" in listed


# --- assembly ----------------------------------------------------------------------


def test_number_and_title_come_from_the_plan_not_the_draft():
    outline = ChapterOutline(number=3, title="Index schema design", objectives=["build an index"])

    chapter = assemble(outline, make_draft())

    assert chapter.number == 3
    assert chapter.title == "Index schema design"


def test_headings_are_rendered_by_us_not_asked_for():
    """Live output came back as flat prose with no headings at all, so structure is ours."""
    body = render_body(
        [
            ChapterSection(heading="Defining the fields", markdown="First part."),
            ChapterSection(heading="Running it", markdown="Second part."),
        ]
    )

    assert body == "## Defining the fields\n\nFirst part.\n\n## Running it\n\nSecond part."


@pytest.mark.asyncio
async def test_a_chapter_with_no_content_is_an_error(monkeypatch):
    curriculum = make_curriculum(1)
    empty = make_draft(sections=[ChapterSection(heading="Something", markdown="   ")])
    use_stub(monkeypatch, StubAgent(draft=empty))

    with pytest.raises(ValueError, match="empty body for chapter 1"):
        await write_chapter(make_request(), curriculum, curriculum.chapters[0], [])


# --- writing the whole course ------------------------------------------------------


@pytest.mark.asyncio
async def test_every_chapter_is_written_once_and_kept_in_order(monkeypatch):
    curriculum = make_curriculum(6)
    agent = use_stub(monkeypatch, StubAgent())

    chapters = await write_chapters(make_request(), curriculum, [])

    assert len(agent.prompts) == 6
    assert [chapter.number for chapter in chapters] == [1, 2, 3, 4, 5, 6]
    assert [chapter.title for chapter in chapters] == [c.title for c in curriculum.chapters]


@pytest.mark.asyncio
async def test_chapters_are_written_in_parallel_but_bounded(monkeypatch):
    """Serial writing makes a long course unbearable; unbounded writing trips the rate limit."""
    curriculum = make_curriculum(12)
    agent = use_stub(monkeypatch, StubAgent(delay=0.01))

    await write_chapters(make_request(), curriculum, [])

    assert agent.peak == MAX_CONCURRENT_CHAPTERS


@pytest.mark.asyncio
async def test_one_failed_chapter_fails_the_step_and_names_it(monkeypatch):
    """A course silently missing chapter 3 still reads as finished, which is worse than failing."""
    curriculum = make_curriculum(5)
    use_stub(monkeypatch, StubAgent(fail_on="Write chapter 3 of"))

    with pytest.raises(ValueError, match=r"failed on chapters \[3\]"):
        await write_chapters(make_request(), curriculum, [])


@pytest.mark.asyncio
async def test_a_failure_does_not_cancel_the_other_chapters(monkeypatch):
    curriculum = make_curriculum(5)
    agent = use_stub(monkeypatch, StubAgent(fail_on="Write chapter 3 of"))

    with pytest.raises(ValueError):
        await write_chapters(make_request(), curriculum, [])

    # Four chapters once each, plus chapter 3 exhausting its retries.
    assert len(agent.prompts) == 4 + MAX_ATTEMPTS


# --- wiring ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_stores_the_chapters_and_forwards_state(monkeypatch):
    async def fake_write(request, curriculum, sources):
        return [Chapter(number=1, title="t", body_markdown="b")]

    monkeypatch.setattr(chapter_module, "write_chapters", fake_write)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.curriculum = make_curriculum(1)
    ctx = CapturingContext()

    await ChapterExecutor(id=WorkflowStep.CHAPTER).run(state, ctx)

    assert len(state.chapters) == 1
    assert WorkflowStep.CHAPTER in state.completed_steps
    assert ctx.messages == [state]


# --- regeneration ------------------------------------------------------------------


def written(number: int, body: str = "original") -> Chapter:
    return Chapter(number=number, title=f"Chapter topic {number}", body_markdown=body)


def test_splice_replaces_only_what_was_rewritten():
    existing = [written(1), written(2), written(3)]

    result = splice(existing, [written(2, "rewritten")])

    assert [chapter.body_markdown for chapter in result] == ["original", "rewritten", "original"]


def test_splice_keeps_the_chapters_in_order():
    existing = [written(1), written(2), written(3)]

    result = splice(existing, [written(3, "c"), written(1, "a")])

    assert [chapter.number for chapter in result] == [1, 2, 3]


@pytest.mark.asyncio
async def test_only_the_flagged_chapters_are_rewritten(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())
    review = ReviewResult(score=60, regenerate_chapters=[2])

    await rewrite_chapters(make_request(), make_curriculum(4), [], review)

    assert len(agent.prompts) == 1
    assert "Write chapter 2 of" in agent.prompts[0]


@pytest.mark.asyncio
async def test_a_rewrite_is_told_what_the_reviewer_objected_to(monkeypatch):
    """Without the issues the rewrite is a fresh sample of the same prompt."""
    agent = use_stub(monkeypatch, StubAgent())
    review = ReviewResult(
        score=60, regenerate_chapters=[1], chapter_issues={1: ["no worked example"]}
    )

    await rewrite_chapters(make_request(), make_curriculum(2), [], review)

    assert "no worked example" in agent.prompts[0]


@pytest.mark.asyncio
async def test_a_first_draft_is_never_told_it_was_rejected(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())

    await write_chapters(make_request(), make_curriculum(1), [])

    assert "rejected" not in agent.prompts[0]


@pytest.mark.asyncio
async def test_the_executor_rewrites_instead_of_starting_over(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())
    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.curriculum = make_curriculum(3)
    state.chapters = [written(1), written(2), written(3)]
    state.review = ReviewResult(score=60, regenerate_chapters=[2])

    await ChapterExecutor(id=WorkflowStep.CHAPTER).run(state, CapturingContext())

    assert len(agent.prompts) == 1
    assert [chapter.number for chapter in state.chapters] == [1, 2, 3]
    assert state.chapters[0].body_markdown == "original"


@pytest.mark.asyncio
async def test_a_rewrite_counts_against_the_revision_cap(monkeypatch):
    """Counted where the work happens, so the cap counts rewrites actually performed."""
    use_stub(monkeypatch, StubAgent())
    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.curriculum = make_curriculum(2)
    state.chapters = [written(1), written(2)]
    state.review = ReviewResult(score=60, regenerate_chapters=[1])

    await ChapterExecutor(id=WorkflowStep.CHAPTER).run(state, CapturingContext())

    assert state.revision_count == 1


@pytest.mark.asyncio
async def test_a_passing_review_does_not_make_the_executor_rewrite(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())
    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.curriculum = make_curriculum(2)
    state.chapters = [written(1), written(2)]
    state.review = ReviewResult(score=95, regenerate_chapters=[])

    await ChapterExecutor(id=WorkflowStep.CHAPTER).run(state, CapturingContext())

    assert len(agent.prompts) == 2
    assert state.revision_count == 0


def test_progress_reaches_sixty_percent_once_chapters_are_written():
    completed = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.SKILL_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
        WorkflowStep.CHAPTER,
    ]

    assert progress_percent(completed) == 60
