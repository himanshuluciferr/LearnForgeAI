"""Indexes courses that already exist.

Generation indexes a course as it finishes, so this is only for the ones stored before the
index did. Idempotent: a course's passages are dropped and rewritten, and the key is derived
from the course and url, so running it twice leaves the same index.

    python scripts/backfill_index.py            # every stored course
    python scripts/backfill_index.py --user u1  # one learner's
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.stdout.reconfigure(encoding="utf-8")

from backend.services.ai_search import search_enabled, vectors_enabled  # noqa: E402
from backend.services.cosmos import COURSES, close_cosmos, get_container  # noqa: E402
from backend.services.course_index import documents, index_course  # noqa: E402
from backend.services.course_store import load  # noqa: E402
from backend.workflow.state import CourseState  # noqa: E402


async def stored(user: str | None) -> list[tuple[str, str, CourseState]]:
    container = get_container(COURSES)
    query = "SELECT * FROM c ORDER BY c.created_at DESC"
    found = []
    async for doc in container.query_items(
        query=query, **({"partition_key": user} if user else {})
    ):
        # Through the store's loader, which repairs courses written by earlier versions.
        # Validating the state here instead skipped four courses the app itself can read.
        course = load(dict(doc))
        if course is None:
            print(f"  skipped {doc.get('id', '?')[:8]}: does not load", flush=True)
            continue
        found.append((course.id, course.user_id, course.state))
    return found


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="only this learner's courses")
    parser.add_argument("--dry-run", action="store_true", help="count passages, write nothing")
    args = parser.parse_args()

    if not search_enabled():
        print("SEARCH_ENDPOINT is not set, so there is no index to fill.", flush=True)
        return

    print(f"vectors: {'on' if vectors_enabled() else 'off (keyword only)'}\n", flush=True)
    courses = await stored(args.user)
    await close_cosmos()

    total = 0
    for course_id, user_id, state in courses:
        title = state.curriculum.title if state.curriculum else "(untitled)"
        if args.dry_run:
            count = len(documents(course_id, user_id, state))
            print(f"  would index {count:>4} passages  {title[:48]}", flush=True)
        else:
            count = await index_course(course_id, user_id, state)
            print(f"  indexed {count:>4} passages  {title[:48]}", flush=True)
        total += count

    print(f"\n{'would index' if args.dry_run else 'indexed'} {total} passages "
          f"across {len(courses)} courses", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
