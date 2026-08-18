"""Offline tests for chapter-agent: continuity context, length, concurrency, wiring."""

import asyncio

import pytest

from backend.agents import chapter as chapter_module
from backend.agents.fanout import MAX_ATTEMPTS
from backend.agents.chapter import (
    CHARS_PER_TOPIC,
    MAX_CONCURRENT_CHAPTERS,
    MAX_TOPIC_WORDS,
    MIN_TOPIC_WORDS,
    ChapterExecutor,
    assemble_topic,
    build_prompt,
    build_wrap_prompt,
    coming_later,
    covered_so_far,
    format_sources,
    plan_jobs,
    render_body,
    render_topic,
    rewrite_chapters,
    splice,
    target_words,
    write_chapters,
    write_topic,
)
from backend.workflow.state import (
    Chapter,
    ChapterDiagram,
    ChapterDraft,
    ChapterOutline,
    CourseState,
    Curriculum,
    DiagramEdge,
    DiagramKind,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    ReviewResult,
    Topic,
    TopicDraft,
    TopicOutline,
    WorkflowStep,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value) -> None:
        self.value = value


class StubAgent:
    """Records how many calls are in flight at once, so concurrency can be asserted."""

    def __init__(self, draft=None, delay: float = 0.0, fail_on: str = "") -> None:
        self.draft = draft if draft is not None else make_draft()
        self.delay = delay
        self.fail_on = fail_on
        self.prompts: list[str] = []
        self.in_flight = 0
        self.peak = 0

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(self.delay)
            if self.fail_on and self.fail_on in prompt:
                raise RuntimeError("model call failed")
            return StubResponse(self.draft)
        finally:
            self.in_flight -= 1


def make_draft(**overrides) -> TopicDraft:
    return TopicDraft(
        **{
            "what_it_is": "A session holds one conversation.",
            "why_it_matters": "Without it every turn starts from nothing.",
            "how_to_use": "Call `create_session()` and pass it to each run.",
            **overrides,
        }
    )


def make_wrap(**overrides) -> ChapterDraft:
    return ChapterDraft(
        **{"key_points": ["a point"], "exercises": ["do a thing"], **overrides}
    )


def make_request(**overrides) -> LearningRequest:
    return LearningRequest(
        **{
            "is_learning_request": True,
            "skill": "Azure AI Search",
            "experience": ExperienceLevel.BEGINNER,
            "goal": "add search to our intranet",
            "daily_minutes": 30,
            **overrides,
        }
    )


def make_curriculum(count: int, topics: int = 2) -> Curriculum:
    return Curriculum(
        title="Search course",
        summary="s",
        chapters=[
            ChapterOutline(
                number=n,
                title=f"Chapter topic {n}",
                objectives=[f"do thing {n}", f"build {n}"],
                topics=[
                    TopicOutline(title=f"Topic {n}.{m}", objectives=[f"use {n}.{m}"])
                    for m in range(1, topics + 1)
                ],
            )
            for n in range(1, count + 1)
        ],
    )


def make_topic_outline(title: str, *objectives: str) -> TopicOutline:
    return TopicOutline(title=title, objectives=list(objectives))


def use_stub(monkeypatch, agent: StubAgent) -> StubAgent:
    monkeypatch.setattr(chapter_module, "get_chapter_agent", lambda: agent)
    monkeypatch.setattr(
        chapter_module, "get_wrap_agent", lambda: StubAgent(draft=make_wrap())
    )
    return agent


# --- length ------------------------------------------------------------------------


def test_topic_length_follows_what_it_promised_to_cover():
    """`target_words` used to be one number for every chapter no matter the subject, so a
    large area and a small one came out the same size."""
    assert target_words(["one"]) < target_words(["one", "two", "three"])


def test_topic_length_is_clamped_at_both_ends():
    assert target_words([]) == MIN_TOPIC_WORDS
    assert target_words([f"objective {n}" for n in range(50)]) == MAX_TOPIC_WORDS


def test_prompt_states_the_computed_length_rather_than_asking_for_a_guess():
    curriculum = make_curriculum(3)
    topic = curriculum.chapters[0].topics[0]

    prompt = build_prompt(make_request(), curriculum, curriculum.chapters[0], topic, 1, [])

    assert f"Target length: about {target_words(topic.objectives)} words" in prompt


# --- continuity between independent calls ------------------------------------------


def test_the_first_topic_of_the_course_is_told_nothing_has_been_taught_yet():
    curriculum = make_curriculum(4)

    guidance = covered_so_far(curriculum, curriculum.chapters[0], 1)

    assert "first topic of the course" in guidance


def test_a_later_topic_is_told_the_earlier_chapters_and_its_own_earlier_topics():
    curriculum = make_curriculum(4, topics=3)

    guidance = covered_so_far(curriculum, curriculum.chapters[2], 3)

    assert "- 1. Chapter topic 1" in guidance
    assert "- 2. Chapter topic 2" in guidance
    assert "3.1 Topic 3.1" in guidance
    assert "3.2 Topic 3.2" in guidance
    assert "3.3" not in guidance  # never itself
    assert "- 4." not in guidance  # never the future


def test_the_last_topic_is_told_to_close_the_course():
    curriculum = make_curriculum(4, topics=2)

    guidance = coming_later(curriculum, curriculum.chapters[-1], 2)

    assert "final topic" in guidance


def test_a_middle_topic_is_told_what_to_leave_alone():
    curriculum = make_curriculum(4, topics=3)

    guidance = coming_later(curriculum, curriculum.chapters[1], 1)

    assert "2.2 Topic 2.2" in guidance
    assert "2.3 Topic 2.3" in guidance
    assert "- 3. Chapter topic 3" in guidance
    assert "2.1" not in guidance


def test_prompt_places_the_topic_in_its_chapter():
    curriculum = make_curriculum(7, topics=2)

    prompt = build_prompt(
        make_request(),
        curriculum,
        curriculum.chapters[3],
        curriculum.chapters[3].topics[1],
        2,
        [],
    )

    assert "Chapter 4: Chapter topic 4" in prompt
    assert "Write topic 4.2: Topic 4.2" in prompt
    assert "Course: Search course" in prompt
    assert "- use 4.2" in prompt


def test_prompt_carries_the_course_language():
    curriculum = make_curriculum(2)

    prompt = build_prompt(
        make_request(language="hi"),
        curriculum,
        curriculum.chapters[0],
        curriculum.chapters[0].topics[0],
        1,
        [],
    )

    assert "Course language: hi" in prompt


def test_the_writer_is_given_the_retrieved_text_not_a_description_of_it():
    """The regression that matters: this used to hand over a summary the research model wrote
    about a page it never opened, so every chapter was written from memory with a citation."""
    sources = [
        ResearchSource(
            title="Azure AI Search docs",
            url="https://learn.microsoft.com/azure/search/",
            kind=ResourceKind.DOCS,
            text="An index is a persistent store of documents and their fields.",
        )
    ]

    listed = format_sources(sources, make_topic_outline("Creating an index"))

    assert "Azure AI Search docs" in listed
    assert "https://learn.microsoft.com/azure/search/" in listed
    assert "An index is a persistent store of documents and their fields." in listed


def test_the_topic_gets_the_part_of_the_page_about_its_own_subject():
    """Measured: the writer used to see the first 4,000 chars of every source, which for a
    reference page is its introduction — so the chapter on --rebase-merges never saw the
    --rebase-merges section, although we had fetched it."""
    filler = "orientation and getting started " * 400
    source = ResearchSource(
        title="git-rebase",
        url="https://git-scm.com/docs/git-rebase",
        kind=ResourceKind.DOCS,
        text=f"{filler} rebasing merges preserves topology with rebase-merges",
    )

    listed = format_sources([source], make_topic_outline("Rebasing merges and topology"))

    assert "rebasing merges preserves topology" in listed


def test_a_long_page_is_truncated_before_it_reaches_every_topic_prompt():
    """Each source rides in every topic's prompt, so its size multiplies by topic count."""
    source = ResearchSource(
        title="t", url="https://x.example/a", kind=ResourceKind.DOCS, text="word " * 20_000
    )

    listed = format_sources([source], make_topic_outline("Anything"))

    assert len(listed) <= CHARS_PER_TOPIC + 200


# --- assembly ----------------------------------------------------------------------


def test_number_and_title_come_from_the_plan_not_the_draft():
    outline = ChapterOutline(number=3, title="Index schema design", objectives=["build an index"])

    topic = assemble_topic(outline, make_topic_outline("Analyzers"), 2, make_draft())

    assert topic.chapter_number == 3
    assert topic.number == 2
    assert topic.label == "3.2"
    assert topic.title == "Analyzers"


def test_every_topic_carries_the_same_parts_in_the_same_order():
    """Structure is rendered here rather than asked for, so a reader can find the same part
    of every topic in the same place."""
    body = render_topic(
        Topic(
            chapter_number=2,
            number=1,
            title="Sessions",
            what_it_is="A session holds one conversation.",
            why_it_matters="Every turn would otherwise start from nothing.",
            how_to_use="Call `create_session()`.",
        )
    )

    assert body == (
        "## 2.1 Sessions\n\n"
        "A session holds one conversation.\n\n"
        "**Why it matters.** Every turn would otherwise start from nothing.\n\n"
        "Call `create_session()`."
    )


def test_an_implementation_is_rendered_after_the_explanation():
    body = render_topic(
        Topic(
            chapter_number=1,
            number=1,
            title="Sessions",
            what_it_is="w",
            why_it_matters="y",
            how_to_use="h",
            implementation="```python\nsession = agent.create_session()\n```",
        )
    )

    assert body.endswith(
        "**Implementation**\n\n```python\nsession = agent.create_session()\n```"
    )


def test_a_topic_with_no_implementation_gets_no_empty_heading():
    """The sources may not show enough to write code that runs, and an empty promise under a
    heading is worse than no heading."""
    body = render_topic(
        Topic(
            chapter_number=1,
            number=1,
            title="t",
            what_it_is="w",
            why_it_matters="y",
            how_to_use="h",
            implementation="   ",
        )
    )

    assert "Implementation" not in body


def test_a_topic_diagram_is_drawn_above_the_prose():
    """Its job is to orient the reader, not to recap them."""
    body = render_topic(
        Topic(
            chapter_number=1,
            number=2,
            title="Agents",
            what_it_is="An agent calls a model.",
            why_it_matters="y",
            how_to_use="h",
            diagram=ChapterDiagram(
                kind=DiagramKind.FLOW,
                title="How a request reaches the model",
                nodes=["Agent", "Model Client"],
                edges=[DiagramEdge(source="Agent", target="Model Client")],
            ),
        )
    )

    assert body.index("```mermaid") < body.index("An agent calls a model.")


def test_the_body_is_every_topic_in_reading_order():
    body = render_body(
        [
            Topic(
                chapter_number=1, number=1, title="First",
                what_it_is="a", why_it_matters="b", how_to_use="c",
            ),
            Topic(
                chapter_number=1, number=2, title="Second",
                what_it_is="d", why_it_matters="e", how_to_use="f",
            ),
        ]
    )

    assert body.index("## 1.1 First") < body.index("## 1.2 Second")


def test_the_wrap_call_is_shown_the_topics_the_chapter_ended_up_with():
    outline = make_curriculum(1, topics=2).chapters[0]
    topics = [
        Topic(
            chapter_number=1, number=1, title="First",
            what_it_is="a", why_it_matters="b", how_to_use="c",
        )
    ]

    prompt = build_wrap_prompt(outline, topics)

    assert "1.1 First" in prompt
    assert "do thing 1" in prompt


@pytest.mark.asyncio
async def test_a_topic_with_no_content_is_an_error(monkeypatch):
    curriculum = make_curriculum(1)
    use_stub(monkeypatch, StubAgent(draft=make_draft(how_to_use="   ")))

    with pytest.raises(ValueError, match="empty topic 1.1"):
        await write_topic(
            make_request(),
            curriculum,
            curriculum.chapters[0],
            curriculum.chapters[0].topics[0],
            1,
            [],
        )


# --- writing the whole course ------------------------------------------------------


def test_every_topic_of_every_chapter_becomes_one_job():
    """Fanning out over chapters and again over topics would multiply the two limits."""
    jobs = plan_jobs(make_curriculum(3, topics=4).chapters)

    assert len(jobs) == 12
    assert [job.number for job in jobs[:4]] == [1, 1, 1, 1]
    assert [job.position for job in jobs[:4]] == [1, 2, 3, 4]


@pytest.mark.asyncio
async def test_every_topic_is_written_once_and_kept_in_order(monkeypatch):
    curriculum = make_curriculum(6, topics=2)
    agent = use_stub(monkeypatch, StubAgent())

    chapters = await write_chapters(make_request(), curriculum, [])

    assert len(agent.prompts) == 12
    assert [chapter.number for chapter in chapters] == [1, 2, 3, 4, 5, 6]
    assert [topic.label for topic in chapters[2].topics] == ["3.1", "3.2"]


@pytest.mark.asyncio
async def test_topics_are_written_in_parallel_but_bounded(monkeypatch):
    """Serial writing makes a long course unbearable; unbounded writing trips the rate limit."""
    curriculum = make_curriculum(6, topics=4)
    agent = use_stub(monkeypatch, StubAgent(delay=0.01))

    await write_chapters(make_request(), curriculum, [])

    assert agent.peak == MAX_CONCURRENT_CHAPTERS


@pytest.mark.asyncio
async def test_one_failed_topic_fails_the_step_and_names_its_chapter(monkeypatch):
    """A course silently missing part of chapter 3 still reads as finished."""
    curriculum = make_curriculum(5, topics=2)
    use_stub(monkeypatch, StubAgent(fail_on="Write topic 3.2"))

    with pytest.raises(ValueError, match=r"failed on chapters \[3\]"):
        await write_chapters(make_request(), curriculum, [])


@pytest.mark.asyncio
async def test_a_failure_does_not_cancel_the_other_topics(monkeypatch):
    curriculum = make_curriculum(5, topics=2)
    agent = use_stub(monkeypatch, StubAgent(fail_on="Write topic 3.2"))

    with pytest.raises(ValueError):
        await write_chapters(make_request(), curriculum, [])

    # Nine topics once each, plus topic 3.2 exhausting its retries.
    assert len(agent.prompts) == 9 + MAX_ATTEMPTS


# --- wiring ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_executor_stores_the_chapters_and_forwards_state(monkeypatch):
    async def fake_write(request, curriculum, sources):
        return [Chapter(number=1, title="t", body_markdown="b")]

    monkeypatch.setattr(chapter_module, "write_chapters", fake_write)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.curriculum = make_curriculum(1)
    ctx = CapturingContext()

    await ChapterExecutor(id=WorkflowStep.CHAPTER).run(state, ctx)

    assert len(state.chapters) == 1
    assert WorkflowStep.CHAPTER in state.completed_steps
    assert ctx.messages == [state]


# --- regeneration ------------------------------------------------------------------


def written(number: int, body: str = "original") -> Chapter:
    return Chapter(number=number, title=f"Chapter topic {number}", body_markdown=body)


def test_splice_replaces_only_what_was_rewritten():
    existing = [written(1), written(2), written(3)]

    result = splice(existing, [written(2, "rewritten")])

    assert [chapter.body_markdown for chapter in result] == ["original", "rewritten", "original"]


def test_splice_keeps_the_chapters_in_order():
    existing = [written(1), written(2), written(3)]

    result = splice(existing, [written(3, "c"), written(1, "a")])

    assert [chapter.number for chapter in result] == [1, 2, 3]


@pytest.mark.asyncio
async def test_only_the_flagged_chapters_are_rewritten(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())
    review = ReviewResult(score=60, regenerate_chapters=[2])

    await rewrite_chapters(make_request(), make_curriculum(4, topics=2), [], review)

    assert len(agent.prompts) == 2
    assert all("Write topic 2." in prompt for prompt in agent.prompts)


@pytest.mark.asyncio
async def test_a_rewrite_is_told_what_the_reviewer_objected_to(monkeypatch):
    """Without the issues the rewrite is a fresh sample of the same prompt."""
    agent = use_stub(monkeypatch, StubAgent())
    review = ReviewResult(
        score=60, regenerate_chapters=[1], chapter_issues={1: ["no worked example"]}
    )

    await rewrite_chapters(make_request(), make_curriculum(2), [], review)

    assert "no worked example" in agent.prompts[0]


@pytest.mark.asyncio
async def test_a_first_draft_is_never_told_it_was_rejected(monkeypatch):
    agent = use_stub(monkeypatch, StubAgent())

    await write_chapters(make_request(), make_curriculum(1), [])

    assert "rejected" not in agent.prompts[0]
