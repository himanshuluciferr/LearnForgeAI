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

from backend.agents import chapter as chapter_agent  # noqa: E402
from backend.agents import curriculum as curriculum_agent  # noqa: E402
from backend.agents import practice as practice_agent  # noqa: E402
from backend.agents import project as project_agent  # noqa: E402
from backend.agents import quiz as quiz_agent  # noqa: E402
from backend.agents import research as research_agent  # noqa: E402
from backend.agents import review as review_agent  # noqa: E402
from backend.agents.chapter import CHARS_PER_TOPIC  # noqa: E402
from backend.agents.practice import WORDS_PER_SOLUTION  # noqa: E402
from backend.models.course import StoredCourse  # noqa: E402
from backend.skills.exporter.skill import render_course  # noqa: E402
from backend.skills.passages.skill import head_of, passages_for, render, terms  # noqa: E402
from backend.workflow.state import MAX_REVISIONS  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

BASE = "http://127.0.0.1:8000"
USER = "e2e-publisher-user"

# Overridable because git rebase is something the model knows cold, so a grounded course and a
# recalled one look identical. Pass a subject it does not know to tell them apart.
DEFAULT_PROMPT = "Teach me git rebase, 20 minutes a day, beginner"

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
    """Whether selection picks BETTER text than head-truncation, at the SAME volume.

    The baseline is `head_of` at the topic's own budget, not the first 4,000 chars of every
    source. That earlier baseline grew with the number of sources while the budget did not,
    so once retrieval reached 20 sources it compared 80,000 chars against 8,000 and reported
    a fall that was arithmetic rather than quality.

    The acceptance test is not the review score, which swings +/-5 between identical runs.
    """
    sources, chapters = state.research, state.chapters
    if not sources or not state.curriculum:
        return

    head = render(head_of(sources, CHARS_PER_TOPIC))
    # Terms this budget of head-truncation cannot reach, so only selection can show them.
    below_fold = terms("\n".join(source.text for source in sources)) - terms(head)
    written = {chapter.number: chapter for chapter in chapters}

    print(
        f"\nselection, both at {CHARS_PER_TOPIC:,} chars "
        f"(head baseline {len(head):,} chars; {len(below_fold):,} terms lie beyond it)"
    )
    old_total = new_total = reached = used = counted = 0
    for outline in state.curriculum.chapters:
        chapter = written.get(outline.number)
        for topic in outline.topics:
            query = " ".join([topic.title, *topic.objectives])
            wanted = terms(query)
            selected = passages_for(sources, query, CHARS_PER_TOPIC)

            old_hit = len(wanted & terms(head)) / len(wanted) if wanted else 0.0
            new_hit = len(wanted & terms(selected)) / len(wanted) if wanted else 0.0
            old_total, new_total, counted = old_total + old_hit, new_total + new_hit, counted + 1

            new_terms = below_fold & terms(selected)
            in_chapter = new_terms & terms(chapter.body_markdown) if chapter else set()
            reached, used = reached + len(new_terms), used + len(in_chapter)

            print(
                f"  {topic.title[:46]:<48} topic terms {old_hit:>4.0%} -> {new_hit:>4.0%}  "
                f"{len(selected):>7,} chars  beyond-head terms: {len(new_terms):>4} shown, "
                f"{len(in_chapter):>3} used",
                flush=True,
            )

    if not counted:
        return
    print(
        f"  mean topic-term coverage {old_total / counted:.0%} -> {new_total / counted:.0%} "
        f"at equal volume; {used} of {reached} beyond-head terms reached the written chapters",
        flush=True,
    )


HEAD_CHARS = 1_400
TAIL_CHARS = 400


def show(label: str, text: str) -> None:
    """Prompts run to tens of thousands of characters, so the middle is elided."""
    body = text.strip()
    if len(body) > HEAD_CHARS + TAIL_CHARS:
        omitted = len(body) - HEAD_CHARS - TAIL_CHARS
        body = f"{body[:HEAD_CHARS]}\n\n[... {omitted:,} chars elided ...]\n\n{body[-TAIL_CHARS:]}"
    print(f"\n  --- {label} ({len(text):,} chars) ---", flush=True)
    print("  " + body.replace("\n", "\n  "), flush=True)


def report_agents(state) -> None:
    """The exact prompt each agent was given, and what it returned.

    Nothing is instrumented to produce this: every `build_prompt` is a pure function and all
    its arguments are stored on the course, so the prompts are rebuilt rather than recorded.
    Fan-out nodes show one representative call, since the others differ only in their unit.
    """
    request, subject, curriculum = state.request, state.subject, state.curriculum
    print("\n" + "=" * 78, flush=True)
    print("AGENT INPUTS AND OUTPUTS", flush=True)
    print("=" * 78, flush=True)

    print("\n[1] requirement-agent", flush=True)
    show("INPUT: the learner's raw message", state.prompt)
    show("OUTPUT: LearningRequest", request.model_dump_json(indent=2) if request else "none")

    print("\n[2] subject-analysis-agent", flush=True)
    show("INPUT: the skill node 1 extracted", str(request.skill) if request else "none")
    if subject:
        show("OUTPUT: SubjectAnalysis", subject.model_dump_json(indent=2))
    show(
        "TRACE: what it searched and read",
        "\n".join(
            [f"searches: {state.subject_trace.searches}"]
            + [f"fetched:  {url}" for url in state.subject_trace.fetched_urls]
            + [f"note:     {note}" for note in state.subject_trace.notes]
        ),
    )

    print("\n[3] research-agent", flush=True)
    if subject:
        show(
            "INPUT: queries planned from the subject",
            "\n".join(research_agent.plan_queries(subject, research_agent.MAX_LEARN_QUERIES)),
        )
    show(
        "OUTPUT: sources kept",
        "\n".join(
            f"{source.words:6d}w [{source.kind}] {source.title[:60]} {source.url}"
            for source in state.research
        ),
    )

    if not (request and subject and curriculum):
        return

    print("\n[4] curriculum-agent", flush=True)
    show("INPUT", curriculum_agent.build_prompt(request, subject, state.research))
    show(
        "OUTPUT: Curriculum",
        f"{curriculum.title}\n{curriculum.summary}\n\n"
        + "\n".join(
            f"Ch{outline.number} {outline.title}\n"
            + "\n".join(f"    {outline.number}.{i} {t.title}" for i, t in enumerate(outline.topics, 1))
            for outline in curriculum.chapters
        ),
    )

    outline = curriculum.chapters[0]
    if outline.topics:
        print(f"\n[5] chapter-agent — one call per topic, {sum(len(c.topics) for c in curriculum.chapters)} total", flush=True)
        show(
            f"INPUT: topic {outline.number}.1",
            chapter_agent.build_prompt(request, curriculum, outline, outline.topics[0], 1, state.research),
        )
    if state.chapters and state.chapters[0].topics:
        topic = state.chapters[0].topics[0]
        show(f"OUTPUT: topic {topic.label}", chapter_agent.render_topic(topic))

    written = state.chapters[0] if state.chapters else None
    if written:
        print("\n[6] review-agent — one call per chapter plus a syllabus pass", flush=True)
        show(
            "INPUT: chapter 1",
            review_agent.build_chapter_prompt(request, written, state.research),
        )
        if state.review:
            show("OUTPUT: ReviewResult", state.review.model_dump_json(indent=2))

        print("\n[7] practice-agent — one call per chapter", flush=True)
        show("INPUT: chapter 1", practice_agent.build_prompt(request, outline, written))
        show(
            "OUTPUT: tasks for chapter 1",
            "\n\n".join(
                f"[{item.kind}] {item.prompt}\nSOLUTION: {item.solution}"
                for item in state.practice
                if item.chapter_number == written.number
            ),
        )

    print("\n[8] project-agent — one call for all three", flush=True)
    show("INPUT", project_agent.build_prompt(request, subject, curriculum))
    show(
        "OUTPUT: projects",
        "\n".join(f"{p.level}: {p.title} — {p.summary[:100]}" for p in state.projects),
    )

    if written:
        print("\n[9] quiz-agent — one call per chapter plus a final", flush=True)
        show("INPUT: chapter 1", quiz_agent.build_chapter_prompt(request, written))
    if state.quizzes:
        first = state.quizzes[0]
        show(
            f"OUTPUT: {first.scope}",
            "\n\n".join(
                f"{q.question}\n"
                + "\n".join(f"  {i}) {opt}" for i, opt in enumerate(q.options))
                + f"\n  correct={q.correct_index} — {q.explanation}"
                for q in first.questions
            ),
        )

    print("\n[10] publisher — deterministic, no model", flush=True)
    show(
        "OUTPUT: the published document",
        f"{len(render_course(state)):,} chars\n{state.published.markdown_url if state.published else ''}",
    )


def report_evidence(state) -> None:
    topics = sum(len(chapter.topics) for chapter in state.chapters) or 1
    total = sum(len(source.text) for source in state.research)
    learn = [s for s in state.research if "learn.microsoft.com" in s.url]

    print(f"\nevidence: {total:,} chars over {len(state.research)} sources", flush=True)
    print(f"  documentation provider supplied {len(learn)} of them, "
          f"{sum(len(s.text) for s in learn):,} chars", flush=True)
    print(f"  {total // topics:,} chars per topic against a {CHARS_PER_TOPIC:,} budget "
          f"({'over' if total // topics >= CHARS_PER_TOPIC else 'UNDER'})", flush=True)


def report_composition(state) -> None:
    """How much of the document actually teaches.

    Measured over four stored runs, chapter prose was 20-28% of the published document and the
    worked answers alone outweighed it every time. "Too much to read" is mostly scaffolding,
    so the split is worth watching on every run rather than rediscovering it from a reader.
    """
    document = len(render_course(state))
    prose = sum(len(chapter.body_markdown) for chapter in state.chapters)
    prompts = sum(len(item.prompt) for item in state.practice)
    answers = sum(len(item.solution) for item in state.practice)
    quiz = sum(
        len(question.question + "".join(question.options) + (question.explanation or ""))
        for quiz_set in state.quizzes
        for question in quiz_set.questions
    )

    print(f"\ncomposition: {document:,} chars published", flush=True)
    for label, size in (
        ("chapter prose", prose),
        ("practice prompts", prompts),
        ("practice answers", answers),
        ("quiz text", quiz),
    ):
        print(f"  {label:<18} {size:>8,}  {size / max(document, 1):>5.1%}", flush=True)
    if state.practice:
        # Words, not chars: the budget is stated in words, and code-heavy answers are far more
        # char-dense than prose. Tracking chars showed a solution "doubling" across four runs
        # that was in fact sitting at 61% of its budget.
        counts = [len(item.solution.split()) for item in state.practice]
        over = sum(1 for words in counts if words > WORDS_PER_SOLUTION)
        print(
            f"  mean solution {sum(counts) // len(counts)} words of a {WORDS_PER_SOLUTION} "
            f"budget across {len(counts)} tasks, {over} over",
            flush=True,
        )


def report_grounding(state) -> None:
    """What the reviewer refused to take on trust, and whether rewriting moved it."""
    if state.review is None:
        return
    claims = [
        claim for chapter in state.review.unsupported_claims.values() for claim in chapter
    ]
    print("\n--- grounding ---", flush=True)
    print(f"  revisions taken      {state.revision_count} of {MAX_REVISIONS}", flush=True)
    for index, round_ in enumerate(state.review_rounds, start=1):
        print(f"  pass {index}: score {round_.score}, {round_.unsupported} unsupported claims, "
              f"rewriting {round_.rewriting or 'nothing'}", flush=True)
    print(f"  claims left          {len(claims)} across "
          f"{len(state.review.unsupported_claims)} of {len(state.chapters)} chapters", flush=True)
    for claim in claims[:8]:
        print(f"    {claim[:150]}", flush=True)
    broken = [fault for chapter in state.review.broken_imports.values() for fault in chapter]
    print(f"  broken imports       {len(broken)} across "
          f"{len(state.review.broken_imports)} of {len(state.chapters)} chapters", flush=True)
    for fault in broken:
        print(f"    {fault[:150]}", flush=True)


async def main():
    prompt = " ".join(sys.argv[1:]) or DEFAULT_PROMPT
    print(f"prompt: {prompt}", flush=True)
    async with httpx.AsyncClient(base_url=BASE, timeout=30) as api:
        accepted = await api.post(
            "/courses",
            json={"user_id": USER, "prompt": prompt},
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

    report_evidence(state)
    report_selection(state)
    report_composition(state)
    report_grounding(state)
    report_agents(state)

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