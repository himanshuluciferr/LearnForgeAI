"""Offline tests for practice-agent: task counts, anti-overlap, concurrency, wiring."""

import asyncio

import pytest

from backend.agents import practice as practice_module
from backend.agents.practice import (
    MAX_CONCURRENT_SETS,
    MAX_TASKS,
    MIN_TASKS,
    PracticeExecutor,
    attach,
    build_prompt,
    format_exercises,
    plan_task_count,
    set_practice,
    write_practice_set,
)
from backend.workflow.state import (
    Chapter,
    ChapterOutline,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    PracticeItem,
    PracticeKind,
    PracticeSet,
    PracticeTask,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value: PracticeSet) -> None:
        self.value = value


class StubAgent:
    """Records how many calls are in flight at once, so concurrency can be asserted."""

    def __init__(
        self, practice: PracticeSet | None = None, delay: float = 0.0, fail_on: str = ""
    ) -> None:
        self.practice = practice if practice is not None else make_set()
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
            return StubResponse(self.practice)
        finally:
            self.in_flight -= 1


def make_set(count: int = 2) -> PracticeSet:
    return PracticeSet(
        tasks=[
            PracticeTask(
                kind=PracticeKind.BUILD, prompt=f"task {n}", solution=f"worked answer {n}"
            )
            for n in range(1, count + 1)
        ]
    )


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


def make_outline(number: int = 1, objectives: int = 3) -> ChapterOutline:
    return ChapterOutline(
        number=number,
        title=f"Chapter topic {number}",
        objectives=[f"objective {n}" for n in range(1, objectives + 1)],
    )


def make_chapter(number: int = 1, exercises: list[str] | None = None) -> Chapter:
    return Chapter(
        number=number,
        title=f"Chapter topic {number}",
        body_markdown="## Section\n\nBody text.",
        key_points=["a key point"],
        exercises=exercises if exercises is not None else ["run git rebase -i"],
    )


def make_course(count: int) -> tuple[Curriculum, list[Chapter]]:
    outlines = [make_outline(n) for n in range(1, count + 1)]
    curriculum = Curriculum(title="t", summary="s", chapters=outlines)
    return curriculum, [make_chapter(n) for n in range(1, count + 1)]


def use_stub(monkeypatch, agent: StubAgent) -> StubAgent:
    monkeypatch.setattr(practice_module, "get_practice_agent", lambda: agent)
    return agent


# --- the boundary against chapter exercises and quizzes -----------------------------


def test_practice_always_ships_a_solution():
    """The boundary against chapter exercises lives in the type, not in the prompt."""
    with pytest.raises(ValueError):
        PracticeItem(chapter_number=1, kind=PracticeKind.RECALL, prompt="p")  # type: ignore[call-arg]


def test_there_is_no_multiple_choice_practice_kind():
    """Machine-marked questions belong to quiz-agent, so the kind cannot be expressed here."""
    kinds = {kind.value for kind in PracticeKind}

    assert kinds == {"recall", "apply", "build", "diagnose"}


def test_the_chapters_own_exercises_are_declared_off_limits():
    chapter = make_chapter(exercises=["rebase onto main", "abort a rebase"])

    listed = format_exercises(chapter)

    assert "Do not repeat or rephrase" in listed
    assert "rebase onto main" in listed
    assert "abort a rebase" in listed


def test_a_chapter_without_exercises_leaves_everything_open():
    listed = format_exercises(make_chapter(exercises=[]))

    assert "nothing is off limits" in listed


def test_the_prompt_carries_the_exclusion_list():
    prompt = build_prompt(make_request(), make_outline(), make_chapter(exercises=["do X"]))

    assert "Do not repeat or rephrase these:" in prompt
    assert "- do X" in prompt


# --- counts ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "objectives,expected",
    [
        (0, MIN_TASKS),  # a chapter that promised nothing still gets practised
        (1, MIN_TASKS),
        (3, 3),
        (9, MAX_TASKS),  # an over-eager outline must not multiply the cost
    ],
)
def test_task_count_follows_the_objectives_and_is_clamped(objectives, expected):
    assert plan_task_count([f"o{n}" for n in range(objectives)]) == expected


def test_prompt_states_the_computed_count_rather_than_asking_for_a_guess():
    prompt = build_prompt(make_request(), make_outline(objectives=3), make_chapter())

    assert "Produce exactly 3 tasks." in prompt


def test_prompt_grounds_the_tasks_in_what_was_actually_written():
    chapter = make_chapter()

    prompt = build_prompt(make_request(), make_outline(), chapter)

    assert "- objective 1" in prompt  # what the chapter promised
    assert "- a key point" in prompt  # what it distilled
    assert chapter.body_markdown in prompt  # what the learner actually read


def test_prompt_carries_the_course_language():
    prompt = build_prompt(make_request(language="hi"), make_outline(), make_chapter())

    assert "Course language: hi" in prompt


# --- assembly ----------------------------------------------------------------------


def test_the_chapter_number_is_ours_not_the_models():
    items = attach(7, make_set(2))

    assert [item.chapter_number for item in items] == [7, 7]
    assert [item.prompt for item in items] == ["task 1", "task 2"]


@pytest.mark.asyncio
async def test_a_chapter_with_no_tasks_is_an_error(monkeypatch):
    use_stub(monkeypatch, StubAgent(practice=PracticeSet(tasks=[])))

    with pytest.raises(ValueError, match="no tasks for chapter 1"):
        await write_practice_set(make_request(), make_outline(), make_chapter())


# --- across the whole course -------------------------------------------------------


@pytest.mark.asyncio
async def test_every_chapter_gets_practice_tagged_to_it(monkeypatch):
    curriculum, chapters = make_course(4)
    agent = use_stub(monkeypatch, StubAgent())

    items = await set_practice(make_request(), curriculum, chapters)

    assert len(agent.prompts) == 4
    assert [item.chapter_number for item in items] == [1, 1, 2, 2, 3, 3, 4, 4]


@pytest.mark.asyncio
async def test_practice_is_written_in_parallel_but_bounded(monkeypatch):
    curriculum, chapters = make_course(12)
    agent = use_stub(monkeypatch, StubAgent(delay=0.01))

    await set_practice(make_request(), curriculum, chapters)

    assert agent.peak == MAX_CONCURRENT_SETS


@pytest.mark.asyncio
async def test_one_failed_chapter_fails_the_step_and_names_it(monkeypatch):
    curriculum, chapters = make_course(5)
    use_stub(monkeypatch, StubAgent(fail_on="Chapter 3:"))

    with pytest.raises(ValueError, match=r"failed on chapters \[3\]"):
        await set_practice(make_request(), curriculum, chapters)


# --- wiring ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_stores_the_practice_and_forwards_state(monkeypatch):
    async def fake_set(request, curriculum, chapters):
        return [
            PracticeItem(chapter_number=1, kind=PracticeKind.RECALL, prompt="p", solution="s")
        ]

    monkeypatch.setattr(practice_module, "set_practice", fake_set)

    curriculum, chapters = make_course(1)
    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.curriculum = curriculum
    state.chapters = chapters
    ctx = CapturingContext()

    await PracticeExecutor(id=WorkflowStep.PRACTICE).run(state, ctx)

    assert len(state.practice) == 1
    assert WorkflowStep.PRACTICE in state.completed_steps
    assert ctx.messages == [state]


def test_progress_reaches_sixty_eight_percent_once_practice_is_set():
    completed = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.SKILL_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
        WorkflowStep.CHAPTER,
        WorkflowStep.PRACTICE,
    ]

    assert progress_percent(completed) == 68
