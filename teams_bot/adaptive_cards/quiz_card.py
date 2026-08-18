"""Quiz cards: one question at a time, and the result once it is marked."""

from __future__ import annotations

from typing import Any

from teams_bot.adaptive_cards import action, card, text


def question(
    quiz: dict[str, Any], index: int = 0, answers: dict[str, int] | None = None
) -> dict[str, Any]:
    """One question per card. A whole quiz on one card scrolls the question out of sight.

    Every button carries the answers given so far, because each press is a separate turn with
    nothing remembered between them. Without it only the final answer would reach the marking
    and every earlier one would count as unanswered.

    The option's INDEX travels in the data, never its text: the backend marks by index, and
    matching a button's label back to an option is a second chance to get it wrong.
    """
    questions = quiz.get("questions") or []
    asked = questions[index]
    so_far = dict(answers or {})
    return card(
        text(f"Question {index + 1} of {len(questions)}", weight="bolder"),
        text(asked["question"], size="medium"),
        actions=[
            action(
                option,
                {
                    "command": "answer",
                    "course_id": quiz.get("course_id"),
                    "chapter": quiz.get("chapter_number"),
                    "index": index,
                    "answers": {**so_far, str(asked["number"]): position},
                },
            )
            for position, option in enumerate(asked["options"])
        ],
    )


def result(marked: dict[str, Any]) -> dict[str, Any]:
    lines = [
        f"{'✓' if answer['correct'] else '✗'} Question {answer['number']} — "
        f"{answer.get('explanation', '')}"
        for answer in marked.get("answers") or []
    ]
    return card(
        text(f"{marked['correct']} of {marked['total']} correct", size="large", weight="bolder"),
        text(f"{marked['percent']}%"),
        text("\n\n".join(lines)),
    )
