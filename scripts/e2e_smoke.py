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

from backend.agents.chapter import CHARS_PER_SOURCE  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

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

    state = course["state"]
    published = state["published"]
    subject = state["subject"]
    print(
        f"subject={subject['canonical_name']} ({subject['subject_type']}) "
        f"identity-docs={len(state['sources'])} "
        f"searches={len(state['subject_trace']['searches'])} "
        f"scope={len(subject['scope'])}",
        flush=True,
    )

    # What the chapter writer is actually handed. The first run of this reported node 2's
    # identity evidence and never showed node 3's, which is the number the rebuild was about.
    research = state["research"]
    retrieved = sum(len(source["text"]) for source in research)
    shown = sum(min(CHARS_PER_SOURCE, len(source["text"])) for source in research)
    print(f"research: {len(research)} sources, {retrieved:,} chars retrieved", flush=True)
    for source in research:
        print(
            f"  {len(source['text'].split()):>6} words  [{source['kind']}] {source['url']}",
            flush=True,
        )
    if retrieved:
        print(
            f"  reaching each chapter prompt: {shown:,} chars "
            f"({100 * shown / retrieved:.0f}% of what was retrieved, from the top of each page)",
            flush=True,
        )

    print(
        f"chapters={len(state['chapters'])} practice={len(state['practice'])} "
        f"projects={len(state['projects'])} quizzes={len(state['quizzes'])} "
        f"score={state['review']['score']} revisions={state['revision_count']}",
        flush=True,
    )
    print("markdown_url:", published["markdown_url"][:100], "...", flush=True)

    # The whole point: is the stored link a real, readable, private course?
    async with httpx.AsyncClient(timeout=60) as web:
        signed = await web.get(published["markdown_url"])
        bare = await web.get(published["markdown_url"].split("?")[0])

    print(f"signed GET {signed.status_code}, {len(signed.text)} chars", flush=True)
    print(f"unsigned GET {bare.status_code}", flush=True)

    with open("probe_e2e.md", "w", encoding="utf-8") as handle:
        handle.write(signed.text)
    print("saved probe_e2e.md", flush=True)


# Guarded because without it, merely importing this module generates a whole course.
if __name__ == "__main__":
    asyncio.run(main())