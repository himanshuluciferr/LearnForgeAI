"""Offline tests for quiz-agent: option assembly, distractor hygiene, counts, wiring."""

import asyncio

import pytest

from backend.agents import quiz as quiz_module
from backend.agents.quiz import (
    FINAL_SCOPE,
    MAX_CONCURRENT_QUIZZES,
    MAX_DISTRACTORS,
    MAX_FINAL_QUESTIONS,
    MAX_QUESTIONS,
    MIN_DISTRACTORS,
    MIN_FINAL_QUESTIONS,
    MIN_QUESTIONS,
    QuizExecutor,
    assemble,
    build_chapter_prompt,
    build_final_prompt,
    build_quiz,
    build_quizzes,
    plan_final_count,
    plan_question_count,
    usable_distractors,
)
from backend.workflow.state import (
    Chapter,
    CourseState,
    ExperienceLevel,
    LearningRequest,
    QuizDraft,
    QuizSet,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value: QuizSet) -> None:
        self.value = value


class StubAgent:
    """Records how many calls are in flight at once, so concurrency can be asserted."""

    def __init__(self, quiz_set: QuizSet | None = None, delay: float = 0.0, fail_on: str = ""):
        self.quiz_set = quiz_set if quiz_set is not None else make_set()
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
            return StubResponse(self.quiz_set)
        finally:
            self.in_flight -= 1


def make_draft(n: int = 1, distractors: list[str] | None = None) -> QuizDraft:
    return QuizDraft(
        question=f"What does thing {n} do?",
        correct_answer=f"right answer {n}",
        distractors=distractors if distractors is not None else [f"wrong {n}a", f"wrong {n}b"],
        explanation=f"because {n}",
    )


def make_set(count: int = 3) -> QuizSet:
    return QuizSet(questions=[make_draft(n) for n in range(1, count + 1)])


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


def make_chapter(number: int = 1, key_points: int = 3) -> Chapter:
    return Chapter(
        number=number,
        title=f"Chapter topic {number}",
        body_markdown="## Section\n\nBody text about rebasing.",
        key_points=[f"key point {n}" for n in range(1, key_points + 1)],
        exercises=["run git rebase -i"],
    )


def use_stub(monkeypatch, agent: StubAgent) -> StubAgent:
    monkeypatch.setattr(quiz_module, "get_quiz_agent", lambda: agent)
    return agent


# --- the index is computed, never taken from the model -------------------------------


def test_the_answer_is_found_in_the_options_we_built():
    draft = make_draft()

    question = assemble(draft)

    assert question is not None
    assert question.options[question.correct_index] == draft.correct_answer


def test_every_option_the_model_wrote_survives_assembly():
    draft = make_draft(distractors=["wrong a", "wrong b", "wrong c"])

    question = assemble(draft)

    assert sorted(question.options) == sorted(["right answer 1", "wrong a", "wrong b", "wrong c"])


def test_there_is_no_way_to_supply_a_correct_index_to_the_model():
    """QuizDraft is what the model fills in, so an index must not appear on it."""
    assert "correct_index" not in QuizDraft.model_fields
    assert "correct_answer" in QuizDraft.model_fields


def test_the_shuffle_is_stable_for_the_same_question():
    first = assemble(make_draft())
    second = assemble(make_draft())

    assert first.options == second.options
    assert first.correct_index == second.correct_index


def test_the_answer_does_not_always_land_first():
    """Seeding on the question text is what breaks the model's habit of answering first."""
    drafts = [make_draft(n) for n in range(1, 25)]

    positions = {assemble(draft).correct_index for draft in drafts}

    assert len(positions) > 1, positions


# --- distractor hygiene ---------------------------------------------------------------


def test_a_distractor_repeating_the_answer_is_dropped():
    draft = make_draft(distractors=["right answer 1", "wrong a", "wrong b"])

    assert usable_distractors(draft) == ["wrong a", "wrong b"]


def test_duplicate_distractors_are_dropped_case_insensitively():
    draft = make_draft(distractors=["Wrong A", "wrong a  ", "wrong b"])

    assert usable_distractors(draft) == ["Wrong A", "wrong b"]


def test_distractors_are_capped():
    draft = make_draft(distractors=[f"wrong {n}" for n in range(10)])

    assert len(usable_distractors(draft)) == MAX_DISTRACTORS


def test_a_question_with_too_few_real_distractors_is_dropped():
    draft = make_draft(distractors=["right answer 1", "wrong a"])

    assert assemble(draft) is None


def test_a_question_without_an_answer_is_dropped():
    draft = make_draft()
    draft.correct_answer = "   "

    assert assemble(draft) is None


def test_a_blank_question_is_dropped():
    draft = make_draft()
    draft.question = ""

    assert assemble(draft) is None


# --- degraded versus broken -----------------------------------------------------------


def test_one_bad_question_shortens_the_quiz_rather_than_failing_it():
    quiz_set = QuizSet(
        questions=[make_draft(1), make_draft(2, distractors=["only one"]), make_draft(3)]
    )

    quiz = build_quiz("Chapter 1: x", quiz_set)

    assert len(quiz.questions) == 2


def test_a_quiz_with_no_usable_questions_raises_and_names_the_scope():
    quiz_set = QuizSet(questions=[make_draft(1, distractors=["only one"])])

    with pytest.raises(ValueError, match="Chapter 4: rebasing"):
        build_quiz("Chapter 4: rebasing", quiz_set)


def test_the_scope_is_ours_not_the_models():
    quiz = build_quiz("Chapter 2: conflicts", make_set())

    assert quiz.scope == "Chapter 2: conflicts"


# --- counts are computed --------------------------------------------------------------


def test_question_count_follows_the_key_points_and_is_clamped():
    assert plan_question_count(make_chapter(key_points=1)) == MIN_QUESTIONS
    assert plan_question_count(make_chapter(key_points=4)) == 4
    assert plan_question_count(make_chapter(key_points=20)) == MAX_QUESTIONS


def test_final_count_follows_the_chapter_count_and_is_clamped():
    assert plan_final_count([make_chapter(n) for n in range(1, 3)]) == MIN_FINAL_QUESTIONS
    assert plan_final_count([make_chapter(n) for n in range(1, 8)]) == 7
    assert plan_final_count([make_chapter(n) for n in range(1, 40)]) == MAX_FINAL_QUESTIONS


def test_the_prompt_states_the_exact_question_count():
    chapter = make_chapter(key_points=4)

    assert "Write exactly 4 questions." in build_chapter_prompt(make_request(), chapter)


# --- prompts --------------------------------------------------------------------------


def test_the_chapter_prompt_carries_what_was_actually_written():
    chapter = make_chapter()

    prompt = build_chapter_prompt(make_request(), chapter)

    assert chapter.body_markdown in prompt
    assert "key point 1" in prompt


def test_the_final_prompt_spans_chapters_without_their_prose():
    chapters = [make_chapter(n) for n in range(1, 4)]

    prompt = build_final_prompt(make_request(), chapters)

    assert "Chapter 1: Chapter topic 1" in prompt
    assert "Chapter 3: Chapter topic 3" in prompt
    # The whole course would not fit in one call, so only the takeaways go in.
    assert chapters[0].body_markdown not in prompt


def test_the_prompt_carries_the_course_language():
    prompt = build_chapter_prompt(make_request(language="hi"), make_chapter())

    assert "Course language: hi" in prompt


# --- the step -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_every_chapter_gets_a_quiz_plus_one_final(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())
    chapters = [make_chapter(n) for n in range(1, 4)]

    quizzes = await build_quizzes(make_request(), chapters)

    assert [quiz.scope for quiz in quizzes] == [
        "Chapter 1: Chapter topic 1",
        "Chapter 2: Chapter topic 2",
        "Chapter 3: Chapter topic 3",
        FINAL_SCOPE,
    ]
    assert len(agent.prompts) == 4


@pytest.mark.asyncio
async def test_quizzes_are_written_in_parallel_but_bounded(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent(delay=0.01))
    chapters = [make_chapter(n) for n in range(1, 10)]

    await build_quizzes(make_request(), chapters)

    assert agent.peak == MAX_CONCURRENT_QUIZZES


@pytest.mark.asyncio
async def test_one_failed_chapter_fails_the_step_and_names_it(monkeypatch):
    use_stub(monkeypatch, StubAgent(fail_on="Chapter 2:"))
    chapters = [make_chapter(n) for n in range(1, 4)]

    with pytest.raises(ValueError, match=r"quiz-agent failed on chapters \[2\]"):
        await build_quizzes(make_request(), chapters)


@pytest.mark.asyncio
async def test_the_executor_marks_the_step_and_forwards_state(monkeypatch):
    use_stub(monkeypatch, StubAgent())
    state = CourseState(job_id="j", user_id="u", prompt="p", request=make_request())
    state.chapters = [make_chapter(1), make_chapter(2)]
    ctx = CapturingContext()

    await QuizExecutor(id=WorkflowStep.QUIZ).run(state, ctx)

    assert WorkflowStep.QUIZ in state.completed_steps
    assert len(state.quizzes) == 3
    assert ctx.messages == [state]


def test_progress_reaches_seventy_six_percent_once_the_quiz_is_marked():
    done = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.SKILL_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
        WorkflowStep.CHAPTER,
        WorkflowStep.PRACTICE,
        WorkflowStep.QUIZ,
    ]

    assert progress_percent(done) == 76
