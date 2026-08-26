"""Tests for the Teams bot.

Everything here runs without an adapter, a bot registration or a live backend: the handlers
take plain dicts and return a Reply, and the cards are pure functions.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from teams_bot.adaptive_cards import course_card, progress_card, quiz_card
from teams_bot.backend_client import BackendClient
from teams_bot.commands import Intent, read
from teams_bot.handlers import card_action_handler, message_handler
from teams_bot.identity import learner_id

USER = "aad-object-id"
COURSE = "c1"


def client_for(handler) -> BackendClient:
    transport = httpx.MockTransport(handler)
    return BackendClient(
        base_url="http://backend", client=httpx.AsyncClient(transport=transport)
    )


def routed(routes: dict[str, object]):
    """Answers by path, and records what was asked so a test can read it back."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        for path, payload in routes.items():
            if request.url.path == path:
                return httpx.Response(200, json=payload)
        return httpx.Response(404, json={"detail": "no route"})

    return handler, seen


# --- reading a message ---------------------------------------------------------------


@pytest.mark.parametrize(
    "said, expected",
    [
        ("teach me kubernetes operators", Intent.TEACH),
        ("Teach me git rebase", Intent.TEACH),
        ("progress", Intent.PROGRESS),
        ("how am I doing", Intent.PROGRESS),
        ("quiz me", Intent.QUIZ),
        ("help", Intent.HELP),
        ("hi", Intent.HELP),
        ("", Intent.HELP),
        (None, Intent.HELP),
    ],
)
def test_a_message_is_read_as_an_intent(said, expected):
    assert read(said).intent is expected


def test_an_unrecognised_message_is_treated_as_a_course_request():
    """requirement-agent already refuses anything that is not one, with a better message than
    a keyword list could write."""
    assert read("Kubernetes operators").intent is Intent.TEACH


def test_the_teams_mention_is_stripped_before_reading():
    """In a channel every message arrives prefixed with the bot's mention markup."""
    command = read("<at>LearnForge</at> teach me rust")

    assert command.intent is Intent.TEACH and command.text == "teach me rust"


def test_a_chapter_number_is_picked_out_of_the_message():
    assert read("quiz me on chapter 3").chapter == 3


def test_a_message_with_no_chapter_has_none():
    assert read("quiz me").chapter is None


# --- who the learner is --------------------------------------------------------------


def test_the_aad_object_id_is_preferred():
    """It is stable for the same person across conversations; the channel id is not, and using
    it would hand the same person a fresh set of courses in every chat."""
    activity = SimpleNamespace(from_property=SimpleNamespace(aad_object_id="aad", id="channel"))

    assert learner_id(activity) == "aad"


def test_the_channel_id_is_the_fallback():
    activity = SimpleNamespace(from_property=SimpleNamespace(aad_object_id=None, id="channel"))

    assert learner_id(activity) == "channel"


# --- cards ---------------------------------------------------------------------------


def test_a_quiz_card_never_shows_which_option_is_right():
    paper = {
        "course_id": COURSE,
        "chapter_number": 1,
        "questions": [{"number": 1, "question": "q?", "options": ["a", "b", "c"]}],
    }

    rendered = json.dumps(quiz_card.question(paper))

    assert "correct" not in rendered


def test_a_quiz_button_carries_the_option_index_not_its_text():
    """The backend marks by index, and matching a label back to an option is a second chance
    to get it wrong."""
    paper = {
        "course_id": COURSE,
        "chapter_number": 1,
        "questions": [{"number": 1, "question": "q?", "options": ["a", "b"]}],
    }

    actions = quiz_card.question(paper)["actions"]

    assert actions[1]["data"]["answers"] == {"1": 1}


def test_each_question_carries_the_answers_already_given():
    """Each press is a separate turn with nothing remembered between them; without this only
    the last answer would reach the marking."""
    paper = {
        "course_id": COURSE,
        "questions": [
            {"number": 1, "question": "q1", "options": ["a", "b"]},
            {"number": 2, "question": "q2", "options": ["a", "b"]},
        ],
    }

    actions = quiz_card.question(paper, 1, {"1": 0})["actions"]

    assert actions[1]["data"]["answers"] == {"1": 0, "2": 1}


def test_the_progress_bar_is_drawn_here_because_teams_has_none():
    assert progress_card.bar(50).endswith("50%")
    assert progress_card.bar(0).count("█") == 0
    assert progress_card.bar(100).count("░") == 0


def test_the_building_card_does_not_repeat_the_confirmation_question():
    """While a run waits, `detail` holds "I'll build a course on X. Shall I start?" — a
    question this card has no yes to press, so it reads as being ignored."""
    job = {
        "job_id": "j1",
        "status": "running",
        "percent": 10,
        "step": "subject-analysis",
        "detail": "I'll build a course on Operator pattern. Shall I start?",
    }

    assert "Shall I start" not in json.dumps(progress_card.generating(job))


def test_a_finished_course_can_be_opened_and_read():
    """The whole point of generating it. Everything else on the card is about the course
    rather than the course itself."""
    summary = {
        "course_id": COURSE,
        "title": "T",
        "percent": 0,
        "next_chapter": 1,
        "markdown_url": "https://blob/course.md?sig=x",
        "chapters": [{"number": 1, "title": "One", "read": False, "best_quiz_percent": None}],
    }

    actions = progress_card.course_progress(summary)["actions"]

    assert actions[0] == {
        "type": "Action.OpenUrl",
        "title": "Read the course",
        "url": "https://blob/course.md?sig=x",
    }


def test_a_course_with_no_document_yet_offers_no_dead_button():
    summary = {
        "course_id": COURSE,
        "title": "T",
        "percent": 0,
        "next_chapter": 1,
        "markdown_url": None,
        "chapters": [{"number": 1, "title": "One", "read": False, "best_quiz_percent": None}],
    }

    titles = [a["title"] for a in progress_card.course_progress(summary)["actions"]]

    assert "Read the course" not in titles


def test_the_ready_card_leads_with_reading_it():
    actions = course_card.ready(
        COURSE, {"title": "T", "chapters_total": 3, "markdown_url": "https://blob/c.md?sig=x"}
    )["actions"]

    assert actions[0]["type"] == "Action.OpenUrl"


def test_a_finished_course_offers_the_next_thing_to_do():
    summary = {
        "course_id": COURSE,
        "title": "T",
        "percent": 0,
        "next_chapter": 1,
        "chapters": [{"number": 1, "title": "One", "read": False, "best_quiz_percent": None}],
    }

    titles = [a["title"] for a in progress_card.course_progress(summary)["actions"]]

    assert any("Mark chapter 1" in t for t in titles) and any("Quiz me" in t for t in titles)


def test_a_completed_course_offers_nothing_to_read_next():
    summary = {
        "course_id": COURSE,
        "title": "T",
        "percent": 100,
        "next_chapter": None,
        "chapters": [{"number": 1, "title": "One", "read": True, "best_quiz_percent": 80}],
    }

    assert "actions" not in progress_card.course_progress(summary)


def test_the_choice_card_offers_exactly_the_options_the_job_recorded():
    job = {"job_id": "j", "detail": "which?", "options": ["React", "Vue"]}

    actions = progress_card.choice(job)["actions"]

    assert [a["title"] for a in actions] == ["React", "Vue"]
    assert actions[0]["data"] == {"command": "choose", "job_id": "j", "choice": "React"}


def test_the_ready_card_points_at_the_course_it_is_about():
    actions = course_card.ready(COURSE, {"title": "T", "chapters_total": 3})["actions"]

    assert all(a["data"]["course_id"] == COURSE for a in actions)


# --- messages end to end --------------------------------------------------------------


@pytest.mark.asyncio
async def test_help_needs_no_backend_at_all():
    handler, seen = routed({})

    reply = await message_handler.handle("help", USER, client_for(handler))

    assert "teach me" in reply.text and seen == []


@pytest.mark.asyncio
async def test_teaching_starts_a_job_and_offers_a_button_rather_than_an_id():
    """The learner should not have to retype anything, and the run stops to ask which subject
    it found."""
    handler, seen = routed({"/courses": {"job_id": "j1", "status": "queued", "status_url": "/x"}})

    reply = await message_handler.handle("teach me rust", USER, client_for(handler))

    assert reply.card["actions"][0]["data"] == {"command": "progress", "job_id": "j1"}
    assert json.loads(seen[0].content)["prompt"] == "teach me rust"


@pytest.mark.asyncio
async def test_progress_with_nothing_at_all_says_so_rather_than_failing():
    handler, _ = routed({"/jobs": [], "/courses": []})

    reply = await message_handler.handle("progress", USER, client_for(handler))

    assert "no courses yet" in reply.text


@pytest.mark.asyncio
async def test_progress_answers_about_the_run_in_flight_before_any_finished_course():
    """A build is what changes minute to minute; a finished course is not going anywhere."""
    handler, _ = routed(
        {
            "/jobs": [{"job_id": "j1", "status": "running", "percent": 40, "step": "chapter"}],
            "/courses": [{"course_id": COURSE}],
        }
    )

    reply = await message_handler.handle("progress", USER, client_for(handler))

    assert "40%" in json.dumps(reply.card)


@pytest.mark.asyncio
async def test_a_finished_run_does_not_hold_up_the_course_progress():
    handler, _ = routed(
        {
            "/jobs": [{"job_id": "j1", "status": "completed", "course_id": COURSE}],
            "/courses": [{"course_id": COURSE, "title": "T", "chapters": 2}],
            f"/progress/{COURSE}": {
                "course_id": COURSE,
                "title": "T",
                "percent": 50,
                "next_chapter": 2,
                "chapters": [
                    {"number": 1, "title": "One", "read": True, "best_quiz_percent": None},
                    {"number": 2, "title": "Two", "read": False, "best_quiz_percent": None},
                ],
            },
        }
    )

    reply = await message_handler.handle("progress", USER, client_for(handler))

    assert reply.card is not None and "50%" in json.dumps(reply.card)


@pytest.mark.asyncio
async def test_quiz_me_picks_the_last_chapter_the_learner_read():
    """The quiz that helps is on the chapter just finished, not the one not yet started."""
    handler, seen = routed(
        {
            "/courses": [{"course_id": COURSE}],
            f"/progress/{COURSE}": {
                "course_id": COURSE,
                "title": "T",
                "percent": 50,
                "next_chapter": 3,
                "chapters": [
                    {"number": 1, "title": "One", "read": True, "best_quiz_percent": None},
                    {"number": 2, "title": "Two", "read": True, "best_quiz_percent": None},
                    {"number": 3, "title": "Three", "read": False, "best_quiz_percent": None},
                ],
            },
            f"/quiz/{COURSE}": {
                "course_id": COURSE,
                "chapter_number": 2,
                "questions": [{"number": 1, "question": "q", "options": ["a", "b"]}],
            },
        }
    )

    await message_handler.handle("quiz me", USER, client_for(handler))

    assert seen[-1].url.params["chapter"] == "2"


@pytest.mark.asyncio
async def test_an_explicit_chapter_beats_the_one_we_would_have_chosen():
    handler, seen = routed(
        {
            "/courses": [{"course_id": COURSE}],
            f"/quiz/{COURSE}": {
                "course_id": COURSE,
                "chapter_number": 1,
                "questions": [{"number": 1, "question": "q", "options": ["a", "b"]}],
            },
        }
    )

    await message_handler.handle("quiz me on chapter 1", USER, client_for(handler))

    assert seen[-1].url.params["chapter"] == "1"


# --- button presses --------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_job_still_running_shows_the_progress_bar():
    handler, _ = routed(
        {"/courses/j1/progress": {"job_id": "j1", "status": "running", "percent": 30, "step": "chapter"}}
    )

    reply = await card_action_handler.handle(
        {"command": "progress", "job_id": "j1"}, USER, client_for(handler)
    )

    assert "30%" in json.dumps(reply.card)


@pytest.mark.asyncio
async def test_a_job_waiting_on_confirmation_asks_the_learner():
    handler, _ = routed(
        {
            "/courses/j1/progress": {
                "job_id": "j1",
                "status": "needs-confirmation",
                "subject_name": "Microsoft Agent Framework",
                "subject_description": "d",
                "subject_sources": ["https://learn.microsoft.com/agent-framework/"],
            }
        }
    )

    reply = await card_action_handler.handle(
        {"command": "progress", "job_id": "j1"}, USER, client_for(handler)
    )

    assert "Microsoft Agent Framework" in json.dumps(reply.card)


@pytest.mark.asyncio
async def test_a_rejected_job_says_why_rather_than_showing_a_bar():
    handler, _ = routed(
        {"/courses/j1/progress": {"job_id": "j1", "status": "rejected", "detail": "not a skill"}}
    )

    reply = await card_action_handler.handle(
        {"command": "progress", "job_id": "j1"}, USER, client_for(handler)
    )

    assert reply.card is None and reply.text == "not a skill"


@pytest.mark.asyncio
async def test_marking_a_chapter_read_shows_the_updated_progress():
    handler, seen = routed(
        {
            f"/progress/{COURSE}/chapters/1": {
                "course_id": COURSE,
                "title": "T",
                "percent": 100,
                "next_chapter": None,
                "chapters": [{"number": 1, "title": "One", "read": True, "best_quiz_percent": None}],
            }
        }
    )

    reply = await card_action_handler.handle(
        {"command": "read", "course_id": COURSE, "chapter": 1}, USER, client_for(handler)
    )

    assert seen[0].method == "PUT" and "100%" in json.dumps(reply.card)


@pytest.mark.asyncio
async def test_answering_a_middle_question_asks_the_next_one():
    paper = {
        "course_id": COURSE,
        "chapter_number": 1,
        "questions": [
            {"number": 1, "question": "q1", "options": ["a", "b"]},
            {"number": 2, "question": "q2", "options": ["a", "b"]},
        ],
    }
    handler, seen = routed({f"/quiz/{COURSE}": paper})

    reply = await card_action_handler.handle(
        {
            "command": "answer",
            "course_id": COURSE,
            "chapter": 1,
            "index": 0,
            "answers": {"1": 0},
        },
        USER,
        client_for(handler),
    )

    assert "q2" in json.dumps(reply.card)
    assert not any(r.method == "POST" for r in seen)


@pytest.mark.asyncio
async def test_the_last_answer_submits_every_answer_given():
    paper = {
        "course_id": COURSE,
        "chapter_number": 1,
        "questions": [
            {"number": 1, "question": "q1", "options": ["a", "b"]},
            {"number": 2, "question": "q2", "options": ["a", "b"]},
        ],
    }
    handler, seen = routed(
        {
            f"/quiz/{COURSE}": paper,
            f"/quiz/{COURSE}/answers": {
                "course_id": COURSE,
                "correct": 2,
                "total": 2,
                "percent": 100,
                "answers": [],
            },
        }
    )

    reply = await card_action_handler.handle(
        {
            "command": "answer",
            "course_id": COURSE,
            "chapter": 1,
            "index": 1,
            "answers": {"1": 0, "2": 1},
        },
        USER,
        client_for(handler),
    )

    submitted = json.loads([r for r in seen if r.method == "POST"][0].content)
    assert submitted["answers"] == {"1": 0, "2": 1}
    assert "100%" in json.dumps(reply.card)


@pytest.mark.asyncio
async def test_choosing_a_subject_confirms_it_with_the_backend():
    handler, seen = routed({"/courses/j1/confirm": {"job_id": "j1", "status": "running", "status_url": "/x"}})

    reply = await card_action_handler.handle(
        {"command": "choose", "job_id": "j1", "choice": "Vue"}, USER, client_for(handler)
    )

    assert json.loads(seen[0].content) == {"choice": "Vue"}
    assert "Vue" in reply.text


@pytest.mark.asyncio
async def test_an_unknown_button_is_answered_rather_than_crashing():
    handler, _ = routed({})

    reply = await card_action_handler.handle({"command": "nonsense"}, USER, client_for(handler))

    assert "did not recognise" in reply.text
