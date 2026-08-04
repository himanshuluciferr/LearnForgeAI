"""Live tests for review-agent. Opt in with `pytest -m live`. Seven real model calls.

The number this file exists to protect is what genuinely good chapters score. The review
step is only worth its cost if that number sits above PASSING_REVIEW_SCORE — otherwise
every course pays for two rewrite rounds it does not need, and the bar is wrong rather
than the chapters. Measured at 92 for both chapters when this was written.

That is why the chapters here are written live rather than hand-stubbed: a short fixture
scores in the seventies and would only ever tell us about the fixture.
"""

import pytest
import pytest_asyncio

from backend.agents.chapter import write_chapters
from backend.agents.review import review_chapter, review_course, review_whole_course
from backend.config.settings import get_settings
from backend.workflow.state import PASSING_REVIEW_SCORE, Chapter
from tests.agents.test_practice_live import CURRICULUM, REQUEST

pytestmark = [pytest.mark.live, pytest.mark.asyncio(loop_scope="module")]

THIN = Chapter(
    number=1,
    title="Rebasing a branch onto main",
    body_markdown=(
        "## Rebasing\n\nRebasing is an important Git feature. It is used by many teams. "
        "To rebase, you use the rebase command. This is very useful and you should "
        "practise it often."
    ),
    key_points=["Rebasing is important.", "Use the rebase command."],
    exercises=["Try rebasing."],
)


def skip_without_endpoint() -> None:
    if not get_settings().foundry_project_endpoint:
        pytest.skip("FOUNDRY_PROJECT_ENDPOINT is not set")


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def chapters():
    """Real chapter-agent output, so the bar is measured against what we actually ship."""
    skip_without_endpoint()

    return await write_chapters(REQUEST, CURRICULUM, [])


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def result(chapters):
    return await review_course(REQUEST, CURRICULUM, chapters)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def course_verdict(chapters):
    """The whole-course pass alone, because ReviewResult flattens both sources together."""
    return await review_whole_course(REQUEST, CURRICULUM, chapters)


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def thin_verdict():
    skip_without_endpoint()

    return await review_chapter(REQUEST, THIN)


async def test_the_score_is_a_real_percentage(result):
    assert 0 <= result.score <= 100


async def test_our_own_chapters_clear_the_bar(result):
    """If this fails the bar is wrong, not the chapters, and every course pays for it."""
    assert result.score >= PASSING_REVIEW_SCORE, result.issues


async def test_our_own_chapters_are_not_sent_back_to_be_rewritten(result):
    assert result.regenerate_chapters == [], result.chapter_issues


async def test_a_hollow_chapter_is_caught(thin_verdict):
    assert thin_verdict.score < PASSING_REVIEW_SCORE


async def test_a_rejection_says_what_to_fix(thin_verdict):
    """An issue a rewrite cannot act on is worse than no issue at all."""
    assert thin_verdict.issues
    assert all(len(issue.split()) >= 5 for issue in thin_verdict.issues), thin_verdict.issues


async def test_a_hollow_chapter_scores_below_a_real_one(thin_verdict, result):
    assert thin_verdict.score < result.score


async def test_the_course_pass_does_not_judge_text_it_was_never_shown(course_verdict):
    """It sees titles and key points only, so any claim about the prose is invented."""
    banned = ("never explains", "does not show", "fails to mention", "never mentions")
    offenders = [
        issue for issue in course_verdict.issues if any(text in issue.lower() for text in banned)
    ]

    assert offenders == [], offenders
