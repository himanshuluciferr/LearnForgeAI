"""Live tests for practice-agent. Opt in with `pytest -m live`. Two real model calls."""

import pytest
import pytest_asyncio

from backend.agents.practice import set_practice
from backend.config.settings import get_settings
from backend.workflow.state import (
    Chapter,
    ChapterOutline,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    PracticeKind,
)

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

REQUEST = LearningRequest(
    is_learning_request=True,
    skill="Git rebase",
    experience=ExperienceLevel.BEGINNER,
    goal="keep a clean commit history",
    daily_minutes=30,
)
CURRICULUM = Curriculum(
    title="Git rebase: clean history without losing work",
    summary="Rewriting history deliberately rather than by accident.",
    chapters=[
        ChapterOutline(
            number=1,
            title="Rebasing a branch onto main",
            objectives=["rebase a feature branch onto main", "read the rebase output"],
        ),
        ChapterOutline(
            number=2,
            title="Resolving conflicts during a rebase",
            objectives=["resolve a conflict mid-rebase", "abort a rebase safely"],
        ),
    ],
)
CHAPTERS = [
    Chapter(
        number=1,
        title="Rebasing a branch onto main",
        body_markdown=(
            "## Replaying your commits\n\n"
            "`git rebase main` takes the commits that exist only on your branch and replays "
            "them one at a time on top of `main`. The result is a straight line of history "
            "with no merge commit.\n\n"
            "```bash\ngit checkout feature\ngit fetch origin\ngit rebase origin/main\n```\n\n"
            "## Reading the output\n\n"
            "Each replayed commit gets a new hash, because a commit's hash covers its parent. "
            "That is why a rebased branch cannot be fast-forwarded onto its old self."
        ),
        key_points=[
            "Rebase replays commits; it does not move them.",
            "Every replayed commit gets a new hash.",
            "A rebase produces no merge commit.",
        ],
        exercises=[
            "Rebase a two-commit branch onto main and compare the hashes before and after.",
        ],
    ),
    Chapter(
        number=2,
        title="Resolving conflicts during a rebase",
        body_markdown=(
            "## When a replay stops\n\n"
            "If a replayed commit touches lines that changed on `main`, git stops and leaves "
            "conflict markers in the file. The rebase is paused, not failed.\n\n"
            "```bash\ngit status\n# fix the file, then\ngit add path/to/file\ngit rebase --continue\n```\n\n"
            "## Backing out\n\n"
            "`git rebase --abort` returns the branch to exactly where it was before the rebase "
            "started. Nothing is lost, which is why it is always safe to try."
        ),
        key_points=[
            "A conflict pauses the rebase at one commit, it does not end it.",
            "`git rebase --continue` resumes after staging the fix.",
            "`git rebase --abort` restores the branch exactly.",
        ],
        exercises=[
            "Create a conflicting change on main, rebase, and resolve it.",
        ],
    ),
]


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def practice():
    """One live run of the whole step, shared by every assertion below."""
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")

    return await set_practice(REQUEST, CURRICULUM, CHAPTERS)


async def test_every_chapter_gets_practice(practice):
    covered = {item.chapter_number for item in practice}

    assert covered == {1, 2}


async def test_every_task_ships_a_usable_solution(practice):
    # The type already forbids an empty solution; this catches a one-word non-answer.
    assert all(len(item.solution.split()) >= 5 for item in practice)


async def test_the_solution_is_not_a_restatement_of_the_task(practice):
    assert all(item.solution.strip() != item.prompt.strip() for item in practice)


async def test_kinds_are_varied_rather_than_all_recall(practice):
    kinds = {item.kind for item in practice}

    assert len(kinds) >= 2, kinds


async def test_no_task_is_a_disguised_multiple_choice_question(practice):
    """Multiple choice is quiz-agent's job; the prompt and the enum both forbid it here."""
    banned = ("which of the following", "select the correct", "a) ", "b) ", "choose one")
    offenders = [
        item.prompt for item in practice if any(text in item.prompt.lower() for text in banned)
    ]

    assert offenders == [], offenders


async def test_practice_does_not_repeat_the_chapters_own_exercises(practice):
    existing = {exercise.strip().lower() for chapter in CHAPTERS for exercise in chapter.exercises}
    prompts = {item.prompt.strip().lower() for item in practice}

    assert not existing & prompts


async def test_kinds_are_from_the_agreed_set(practice):
    assert all(item.kind in set(PracticeKind) for item in practice)
