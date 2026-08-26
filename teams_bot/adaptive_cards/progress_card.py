"""Cards for a job being generated and for a course being worked through."""

from __future__ import annotations

from typing import Any

from teams_bot.adaptive_cards import action, card, facts, link, text

BAR_WIDTH = 20


def bar(percent: int) -> str:
    """Drawn here rather than asked for. Teams has no progress bar element, and a percentage
    on its own reads as a number rather than as distance travelled."""
    filled = round(BAR_WIDTH * max(0, min(100, percent)) / 100)
    return f"{'█' * filled}{'░' * (BAR_WIDTH - filled)}  {percent}%"


def started(job: dict[str, Any], message: str) -> dict[str, Any]:
    return card(
        text("Course started", size="large", weight="bolder"),
        text(message),
        actions=[action("Check progress", {"command": "progress", "job_id": job.get("job_id")})],
    )


def generating(job: dict[str, Any]) -> dict[str, Any]:
    """`detail` is deliberately not shown. While the run waits it holds the confirmation
    prompt — "Shall I start?" — which on this card has no yes to press and reads as a question
    the learner is being ignored about."""
    step = (job.get("step") or "starting").replace("-", " ")
    return card(
        text("Building your course", size="large", weight="bolder"),
        text(bar(job.get("percent", 0))),
        text(f"Now: {step}"),
        actions=[action("Check again", {"command": "progress", "job_id": job.get("job_id")})],
    )


def subject_confirmation(job: dict[str, Any]) -> dict[str, Any]:
    """The learner sees the subject we identified before the expensive half of the run.

    Ranking skew is invisible to every automated check: a search for a name one vendor
    dominates returns documents that genuinely all describe one subject.
    """
    sources = job.get("subject_sources") or []
    return card(
        text("Is this the right subject?", size="large", weight="bolder"),
        text(job.get("subject_name") or "", weight="bolder"),
        text(job.get("subject_description") or ""),
        facts([("Read from", url) for url in sources[:3]]),
        actions=[
            action("Yes, build it", {"command": "confirm", "job_id": job.get("job_id")}),
            action("No, start over", {"command": "cancel", "job_id": job.get("job_id")}),
        ],
    )


def choice(job: dict[str, Any]) -> dict[str, Any]:
    """Several skills were named and choosing quietly is the one thing we must not do."""
    options = job.get("options") or []
    return card(
        text("Which would you like to learn?", size="large", weight="bolder"),
        text(job.get("detail") or ""),
        actions=[
            action(option, {"command": "choose", "job_id": job.get("job_id"), "choice": option})
            for option in options
        ],
    )


def course_progress(progress: dict[str, Any]) -> dict[str, Any]:
    chapters = progress.get("chapters") or []
    lines = []
    for chapter in chapters:
        mark = "✓" if chapter.get("read") else "○"
        score = chapter.get("best_quiz_percent")
        suffix = f"  ·  quiz {score}%" if score is not None else ""
        lines.append(f"{mark} {chapter['number']}. {chapter['title']}{suffix}")

    next_chapter = progress.get("next_chapter")
    actions = [link("Read the course", progress.get("markdown_url"))]
    if next_chapter is not None:
        actions.append(
            action(
                f"Mark chapter {next_chapter} read",
                {
                    "command": "read",
                    "course_id": progress.get("course_id"),
                    "chapter": next_chapter,
                },
            )
        )
        actions.append(
            action(
                f"Quiz me on chapter {next_chapter}",
                {
                    "command": "quiz",
                    "course_id": progress.get("course_id"),
                    "chapter": next_chapter,
                },
            )
        )
    return card(
        text(progress.get("title") or "Your course", size="large", weight="bolder"),
        text(bar(progress.get("percent", 0))),
        text("\n\n".join(lines)),
        actions=[found for found in actions if found],
    )
