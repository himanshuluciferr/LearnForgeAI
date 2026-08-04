"""End-to-end smoke test against a running server. Not part of the pytest suite.

Start the app first (`python -m uvicorn backend.main:app`), then run this. It exercises the
one thing no test can: the app configured by the real .env, which picks BlobArtifactStore
and Cosmos, while every pytest run forces the local stores. Takes several minutes because
it generates a real course.
"""

import asyncio
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"
USER = "e2e-publisher-user"


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as api:
        accepted = await api.post(
            "/courses",
            json={"user_id": USER, "prompt": "Teach me git rebase, 20 minutes a day, beginner"},
        )
        accepted.raise_for_status()
        job_id = accepted.json()["job_id"]
        print(f"job {job_id}", flush=True)

        started = time.monotonic()
        last = None
        while True:
            progress = (await api.get(f"/courses/{job_id}/progress", params={"user_id": USER})).json()
            if (progress["step"], progress["percent"]) != last:
                last = (progress["step"], progress["percent"])
                print(f"  [{progress['step']}] {progress['percent']}%", flush=True)
            if progress["status"] in ("completed", "failed", "rejected"):
                break
            await asyncio.sleep(5)

        print(f"\nstatus={progress['status']} after {time.monotonic() - started:.0f}s", flush=True)
        if progress.get("error"):
            print("error:", progress["error"], flush=True)
            return

        course = (
            await api.get(f"/courses/{progress['course_id']}", params={"user_id": USER})
        ).json()

    state = course["state"]
    published = state["published"]
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


asyncio.run(main())