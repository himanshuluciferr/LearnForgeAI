"""End-to-end smoke test against a running server. Not part of the pytest suite.

Start the app first (`python -m uvicorn backend.main:app`), then run this. It exercises the
one thing no test can: the app configured by the real .env, which picks BlobArtifactStore
and Cosmos, while every pytest run forces the local stores. Takes several minutes because
it generates a real course.

It also answers the subject confirmation itself, since a real run now stops to ask.
"""

import asyncio
import sys
import time
from pathlib import Path

import httpx

# Run as `python scripts/e2e_smoke.py`, which puts scripts/ on the path rather than the root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.agents.chapter import CHARS_PER_CHAPTER  # noqa: E402
from backend.models.course import StoredCourse  # noqa: E402
from backend.skills.passages.skill import passages_for, terms  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

# What the writer used to be handed, kept only so this run can be compared with runs 1 and 2.
OLD_CHARS_PER_SOURCE = 4_000

BASE = "http://127.0.0.1:8000"
USER = "e2e-publisher-user"

# Statuses the run will not leave on its own. `needs-choice` is one of them: the learner named
# several skills, or none, and is expected to ask again rather than be waited for.
TERMINAL = ("completed", "failed", "rejected", "needs-choice")


async def watch(api: httpx.AsyncClient, job_id: str) -> dict:
    """Polls to a terminal state, answering the confirmation gate on the way.

    Node 2 stops the run to show the learner which subject it identified, so a loop that only
    waited for completed/failed/rejected would poll forever.
    """
    started = time.monotonic()
    last = None
    confirmed = False
    while True:
        progress = (await api.get(f"/courses/{job_id}/progress", params={"user_id": USER})).json()
        marker = (progress["step"], progress["percent"], progress["status"])
        if marker != last:
            last = marker
            print(f"  [{progress['step']}] {progress['percent']}% {progress['status']}", flush=True)

        if progress["status"] == "needs-confirmation" and not confirmed:
            print(f"  subject: {progress['subject_name']}", flush=True)
            print(f"    {(progress['subject_description'] or '')[:140]}", flush=True)
            for url in progress["subject_sources"]:
                print(f"    read {url}", flush=True)
            # The one thing a human would do here, so the rest of the run can be exercised.
            answered = await api.post(f"/courses/{job_id}/confirm", params={"user_id": USER})
            answered.raise_for_status()
            confirmed = True
            print("  confirmed, generating", flush=True)
        elif progress["status"] in TERMINAL:
            print(f"\nstatus={progress['status']} after {time.monotonic() - started:.0f}s", flush=True)
            return progress

        await asyncio.sleep(5)


def report_selection(state) -> None:
    """Whether per-chapter selection beat the head-truncation it replaced, on this run's own data.

    The acceptance test is not the review score, which swings +/-5 between identical runs. It is
    whether text that head-truncation could never reach now reaches the chapter that needs it.
    """
    sources, chapters = state.research, state.chapters
    if not sources or not state.curriculum:
        return

    head = "\n".join(source.text[:OLD_CHARS_PER_SOURCE] for source in sources)
    # Terms living below the fold of every page, so the old code could not show them to anyone.
    below_fold = terms("\n".join(source.text for source in sources)) - terms(head)
    written = {chapter.number: chapter for chapter in chapters}

    print(f"\nselection ({len(below_fold):,} terms sit below the old {OLD_CHARS_PER_SOURCE:,}-char cut)")
    old_total = new_total = reached = used = 0
    for outline in state.curriculum.chapters:
        query = " ".join([outline.title, *outline.objectives])
        wanted = terms(query)
        selected = passages_for(sources, query, CHARS_PER_CHAPTER)

        old_hit = len(wanted & terms(head)) / len(wanted) if wanted else 0.0
        new_hit = len(wanted & terms(selected)) / len(wanted) if wanted else 0.0
        old_total, new_total = old_total + old_hit, new_total + new_hit

        chapter = written.get(outline.number)
        new_terms = below_fold & terms(selected)
        in_chapter = new_terms & terms(chapter.body_markdown) if chapter else set()
        reached, used = reached + len(new_terms), used + len(in_chapter)

        print(
            f"  {outline.title[:46]:<48} topic terms {old_hit:>4.0%} -> {new_hit:>4.0%}  "
            f"{len(selected):>7,} chars  below-fold terms: {len(new_terms):>4} shown, "
            f"{len(in_chapter):>3} used",
            flush=True,
        )

    count = len(state.curriculum.chapters)
    print(
        f"  mean topic-term coverage {old_total / count:.0%} -> {new_total / count:.0%}; "
        f"{used} of {reached} below-fold terms reached the written chapters",
        flush=True,
    )


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as api:
        accepted = await api.post(
            "/courses",
            json={"user_id": USER, "prompt": "Teach me git rebase, 20 minutes a day, beginner"},
        )
        accepted.raise_for_status()
        job_id = accepted.json()["job_id"]
        print(f"job {job_id}", flush=True)

        progress = await watch(api, job_id)
        if progress["status"] != "completed":
            print("detail:", progress.get("detail"), flush=True)
            if progress.get("error"):
                print("error:", progress["error"], flush=True)
            return

        course = (
            await api.get(f"/courses/{progress['course_id']}", params={"user_id": USER})
        ).json()

    state = StoredCourse.model_validate(course).state
    published = state.published
    subject = state.subject
    assert published is not None and subject is not None and state.curriculum is not None
    print(
        f"subject={subject.canonical_name} ({subject.subject_type}) "
        f"identity-docs={len(state.sources)} "
        f"searches={len(state.subject_trace.searches) if state.subject_trace else 0} "
        f"scope={len(subject.scope)}",
        flush=True,
    )

    # What the chapter writer is actually handed. The first run of this reported node 2's
    # identity evidence and never showed node 3's, which is the number the rebuild was about.
    research = state.research
    retrieved = sum(len(source.text) for source in research)
    print(f"research: {len(research)} sources, {retrieved:,} chars retrieved", flush=True)
    for source in research:
        print(f"  {source.words:>6} words  [{source.kind}] {source.url}", flush=True)

    report_selection(state)

    print(
        f"chapters={len(state.chapters)} practice={len(state.practice)} "
        f"projects={len(state.projects)} quizzes={len(state.quizzes)} "
        f"score={state.review.score if state.review else '-'} "
        f"revisions={state.revision_count}",
        flush=True,
    )
    print("markdown_url:", published.markdown_url[:100], "...", flush=True)

    # The whole point: is the stored link a real, readable, private course?
    async with httpx.AsyncClient(timeout=60) as web:
        signed = await web.get(published.markdown_url)
        bare = await web.get(published.markdown_url.split("?")[0])

    print(f"signed GET {signed.status_code}, {len(signed.text)} chars", flush=True)
    print(f"unsigned GET {bare.status_code}", flush=True)

    with open("probe_e2e.md", "w", encoding="utf-8") as handle:
        handle.write(signed.text)
    print("saved probe_e2e.md", flush=True)


# Guarded because without it, merely importing this module generates a whole course.
if __name__ == "__main__":
    asyncio.run(main())