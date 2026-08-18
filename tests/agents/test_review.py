"""Offline tests for review-agent: scoring, the rewrite list, prompts, wiring."""

import asyncio

import pytest

from backend.agents import review as review_module
from backend.agents.review import (
    MAX_CONCURRENT_REVIEWS,
    ReviewExecutor,
    build_chapter_prompt,
    build_course_prompt,
    build_result,
    clamp_score,
    collect_issues,
    overall_score,
    review_course,
)
from backend.workflow.state import (
    MAX_REVISIONS,
    PASSING_REVIEW_SCORE,
    Chapter,
    ChapterOutline,
    ChapterVerdict,
    CourseState,
    CourseVerdict,
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
    def __init__(self, value) -> None:
        self.value = value


class StubAgent:
    """Records how many calls are in flight at once, so concurrency can be asserted."""

    def __init__(self, value, delay: float = 0.0):
        self.value = value
        self.delay = delay
        self.prompts: list[str] = []
        self.in_flight = 0
        self.peak = 0

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            return StubResponse(self.value)
        finally:
            self.in_flight -= 1


class ScriptedAgent:
    """Returns a different verdict per chapter, keyed by the number in the prompt."""

    def __init__(self, by_number: dict[int, ChapterVerdict]):
        self.by_number = by_number
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        for number, verdict in self.by_number.items():
            if f"Chapter {number}:" in prompt:
                return StubResponse(verdict)
        raise AssertionError(f"no scripted verdict for prompt: {prompt[:80]}")


def make_request(**overrides) -> LearningRequest:
    return LearningRequest(
        **{
            "is_learning_request": True,
            "skill": "Git rebase",
            "experience": ExperienceLevel.BEGINNER,
            "goal": "keep history clean",
            "daily_minutes": 30,
            **overrides,
        }
    )


def make_chapter(number: int = 1) -> Chapter:
    return Chapter(
        number=number,
        title=f"Chapter topic {number}",
        body_markdown=f"## Section\n\nProse belonging to chapter {number}.",
        key_points=[f"key point {number}a", f"key point {number}b"],
        exercises=["run git rebase -i"],
    )


def make_curriculum(count: int = 3) -> Curriculum:
    return Curriculum(
        title="Rebase course",
        summary="A course about rebasing.",
        chapters=[
            ChapterOutline(number=n, title=f"Chapter topic {n}", objectives=["do a thing"])
            for n in range(1, count + 1)
        ],
    )


def verdict(score: int, issues: list[str] | None = None) -> ChapterVerdict:
    return ChapterVerdict(score=score, issues=issues if issues is not None else [])


def use_stubs(monkeypatch, chapter_agent, course_agent=None):
    monkeypatch.setattr(review_module, "get_chapter_review_agent", lambda: chapter_agent)
    monkeypatch.setattr(
        review_module,
        "get_course_review_agent",
        lambda: course_agent if course_agent is not None else StubAgent(CourseVerdict()),
    )
    return chapter_agent


# --- scores we can trust ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"), [(-20, 0), (0, 0), (55, 55), (100, 100), (140, 100)]
)
def test_a_score_outside_the_range_is_pulled_back_in(raw, expected):
    assert clamp_score(raw) == expected


def test_the_course_score_is_the_average_of_its_chapters():
    assert overall_score([verdict(90), verdict(80), verdict(70)]) == 80


def test_no_chapters_scores_zero_rather_than_dividing_by_zero():
    assert overall_score([]) == 0


# --- the rewrite list is computed, never asked for ----------------------------------


def test_only_chapters_below_the_bar_are_queued_for_rewrite():
    numbered = [(1, verdict(95)), (2, verdict(60)), (3, verdict(40))]

    result = build_result(CourseVerdict(), numbered)

    assert result.regenerate_chapters == [2, 3]


def test_a_chapter_exactly_on_the_bar_is_good_enough():
    numbered = [(1, verdict(PASSING_REVIEW_SCORE))]

    assert build_result(CourseVerdict(), numbered).regenerate_chapters == []


def test_a_sound_course_asks_for_no_rewrites():
    numbered = [(1, verdict(95)), (2, verdict(92))]

    result = build_result(CourseVerdict(), numbered)

    assert result.regenerate_chapters == []
    assert result.chapter_issues == {}


def test_issues_are_kept_per_chapter_so_a_rewrite_knows_what_was_wrong():
    numbered = [(1, verdict(50, ["no worked example"])), (2, verdict(95))]

    result = build_result(CourseVerdict(), numbered)

    assert result.chapter_issues == {1: ["no worked example"]}


def test_course_issues_come_first_and_chapter_issues_say_which_chapter():
    course = CourseVerdict(issues=["chapter 3 needs a term defined in chapter 5"])
    numbered = [(1, verdict(50, ["thin"])), (2, verdict(50, ["vague"]))]

    assert collect_issues(course, numbered) == [
        "chapter 3 needs a term defined in chapter 5",
        "Chapter 1: thin",
        "Chapter 2: vague",
    ]


# --- an unsupported claim is a fault whatever the chapter scores --------------------


def grounded(score: int, claims: list[str] | None = None) -> ChapterVerdict:
    return ChapterVerdict(score=score, issues=[], unsupported_claims=claims or [])


def test_a_well_taught_chapter_is_still_rewritten_when_it_invents_an_api():
    """The failure this exists to catch: a fabricated method explained beautifully. A course
    that taught `agent_framework.workflows`, which does not exist, scored 82."""
    numbered = [(1, grounded(95, ["from agent_framework.workflows import Workflow"]))]

    result = build_result(CourseVerdict(issues=[]), numbered)

    assert result.regenerate_chapters == [1]


def test_a_sound_grounded_chapter_is_left_alone():
    numbered = [(1, grounded(PASSING_REVIEW_SCORE))]

    assert build_result(CourseVerdict(issues=[]), numbered).regenerate_chapters == []


def test_the_claim_reaches_the_rewrite_so_it_is_not_written_again():
    """Without it the rewrite is a fresh sample of the same prompt and invents the same API."""
    numbered = [(1, grounded(95, ["ctx.request_info() pauses the run"]))]

    issues = build_result(CourseVerdict(issues=[]), numbered).chapter_issues[1]

    assert any("ctx.request_info() pauses the run" in issue for issue in issues)
    assert any(issue.startswith("Not supported by the sources:") for issue in issues)


def test_unsupported_claims_come_before_teaching_gaps():
    """A chapter teaching something untrue is wrong; how well it teaches it matters less."""
    verdict = ChapterVerdict(score=60, issues=["vague example"], unsupported_claims=["invented"])

    issues = build_result(CourseVerdict(issues=[]), [(1, verdict)]).chapter_issues[1]

    assert issues[0].startswith("Not supported by the sources:")


def test_the_score_still_reports_teaching_alone():
    """Truth is carried by the claim list, so grounding must not move the score - the number
    already swings +/-5 between identical runs."""
    numbered = [(1, grounded(95, ["invented"])), (2, grounded(95))]

    assert build_result(CourseVerdict(issues=[]), numbered).score == 95


# --- what each prompt is allowed to see ---------------------------------------------

def test_the_chapter_reviewer_reads_the_actual_prose():
    prompt = build_chapter_prompt(make_request(), make_chapter(2), [])

    assert "Prose belonging to chapter 2." in prompt


def test_the_reviewer_is_shown_the_sources_the_chapter_was_written_from():
    """Without them it can only judge how well the chapter teaches, never whether what it
    teaches is true - which is how a course of invented API scored 82."""
    source = ResearchSource(
        title="Agent Framework",
        url="https://learn.microsoft.com/agent-framework/",
        kind=ResourceKind.MICROSOFT_LEARN,
        text="An agent is built with WorkflowBuilder and an Executor subclass.",
    )

    prompt = build_chapter_prompt(make_request(), make_chapter(2), [source])

    assert "WorkflowBuilder and an Executor subclass" in prompt


def test_with_no_sources_the_reviewer_is_told_not_to_report_anything_unsupported():
    """Empty research is a failure upstream, but a reviewer left guessing would call an
    entire honest chapter invented."""
    prompt = build_chapter_prompt(make_request(), make_chapter(1), [])

    assert "nothing to support it with" in prompt


def test_the_course_reviewer_gets_the_shape_not_the_prose():
    """Every chapter body in one prompt would not fit, and is judged per chapter anyway."""
    chapters = [make_chapter(n) for n in range(1, 4)]

    prompt = build_course_prompt(make_request(), make_curriculum(3), chapters)

    assert "Prose belonging to" not in prompt
    assert "key point 1a" in prompt
    assert "Ch 3 Chapter topic 3" in prompt


def test_a_chapter_that_was_never_written_is_shown_as_missing():
    prompt = build_course_prompt(make_request(), make_curriculum(3), [make_chapter(1)])

    assert "not written" in prompt


# --- running the review --------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_chapter_is_reviewed_and_the_course_once(monkeypatch):
    chapter_agent = StubAgent(verdict(95))
    course_agent = StubAgent(CourseVerdict())
    use_stubs(monkeypatch, chapter_agent, course_agent)

    await review_course(make_request(), make_curriculum(4), [make_chapter(n) for n in range(1, 5)])

    assert len(chapter_agent.prompts) == 4
    assert len(course_agent.prompts) == 1


@pytest.mark.asyncio
async def test_verdicts_stay_attached_to_the_chapter_they_judged(monkeypatch):
    """The fan-out returns input order, which is the only reason this zip is safe."""
    use_stubs(
        monkeypatch,
        ScriptedAgent({1: verdict(95), 2: verdict(30, ["thin"]), 3: verdict(95)}),
    )

    result = await review_course(
        make_request(), make_curriculum(3), [make_chapter(n) for n in range(1, 4)]
    )

    assert result.regenerate_chapters == [2]
    assert result.chapter_issues == {2: ["thin"]}


@pytest.mark.asyncio
async def test_reviews_run_in_parallel_but_bounded(monkeypatch):
    chapter_agent = StubAgent(verdict(95), delay=0.01)
    use_stubs(monkeypatch, chapter_agent)

    await review_course(
        make_request(), make_curriculum(9), [make_chapter(n) for n in range(1, 10)]
    )

    assert chapter_agent.peak == MAX_CONCURRENT_REVIEWS


@pytest.mark.asyncio
async def test_reviewing_nothing_is_a_failure_not_a_pass(monkeypatch):
    """An empty course scoring zero would be sent back to rewrite chapters that do not exist."""
    use_stubs(monkeypatch, StubAgent(verdict(95)))

    with pytest.raises(ValueError, match="no chapters"):
        await review_course(make_request(), make_curriculum(3), [])


@pytest.mark.asyncio
async def test_a_score_out_of_range_cannot_reach_the_result(monkeypatch):
    use_stubs(monkeypatch, StubAgent(verdict(500)))

    result = await review_course(make_request(), make_curriculum(1), [make_chapter(1)])

    assert result.score == 100


# --- the loop gate --------------------------------------------------------------------


def make_state(**overrides) -> CourseState:
    return CourseState(
        **{"job_id": "j1", "user_id": "u1", "prompt": "teach me rebasing", **overrides}
    )


def test_no_review_yet_means_no_regeneration():
    assert make_state().should_regenerate is False


def test_a_low_score_with_nothing_to_rewrite_does_not_loop():
    """The gate follows the work list, so a harsh average cannot trigger an empty rewrite."""
    state = make_state(review=ReviewResult(score=10, regenerate_chapters=[]))

    assert state.should_regenerate is False


def test_a_flagged_chapter_loops():
    state = make_state(review=ReviewResult(score=95, regenerate_chapters=[2]))

    assert state.should_regenerate is True


def test_the_loop_stops_once_the_revisions_are_spent():
    state = make_state(
        review=ReviewResult(score=10, regenerate_chapters=[1]), revision_count=MAX_REVISIONS
    )

    assert state.should_regenerate is False


# --- wiring ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_stores_the_review_and_marks_the_step(monkeypatch):
    use_stubs(monkeypatch, StubAgent(verdict(95)))
    state = make_state(
        request=make_request(), curriculum=make_curriculum(2), chapters=[make_chapter(1), make_chapter(2)]
    )
    ctx = CapturingContext()

    await ReviewExecutor(id=WorkflowStep.REVIEW).run(state, ctx)

    assert state.review is not None
    assert state.review.score == 95
    assert WorkflowStep.REVIEW in state.completed_steps
    assert ctx.messages == [state]


@pytest.mark.asyncio
async def test_review_moves_progress_on(monkeypatch):
    use_stubs(monkeypatch, StubAgent(verdict(95)))
    state = make_state(
        request=make_request(), curriculum=make_curriculum(1), chapters=[make_chapter(1)]
    )
    before = state.percent

    await ReviewExecutor(id=WorkflowStep.REVIEW).run(state, CapturingContext())

    assert state.percent == before + progress_percent([WorkflowStep.REVIEW])
