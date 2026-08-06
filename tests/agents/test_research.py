"""Offline tests for research-agent: search, selection, the verify/rank pipeline, wiring."""

import pytest

from backend.agents import research as research_module
from backend.agents.research import (
    MAX_SOURCES,
    ResearchExecutor,
    build_prompt,
    collect,
    confirm_on_topic,
    gather_sources,
)
from backend.services.web_search import SearchHit
from backend.workflow.state import (
    CourseState,
    ExperienceLevel,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    SkillAnalysis,
    SourcePick,
    SourceSelection,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value: SourceSelection) -> None:
        self.value = value


class StubAgent:
    def __init__(self, selection: SourceSelection) -> None:
        self.selection = selection
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        return StubResponse(self.selection)


def make_hit(url: str, title: str = "t", kind: ResourceKind = ResourceKind.DOCS) -> SearchHit:
    return SearchHit(title=title, url=url, snippet="s", kind=kind)


def picking(*indexes: int, kind: ResourceKind = ResourceKind.DOCS) -> SourceSelection:
    return SourceSelection(
        picks=[SourcePick(index=index, kind=kind, summary="s") for index in indexes]
    )


def make_request() -> LearningRequest:
    return LearningRequest(
        is_learning_request=True,
        skill="Azure AI Search",
        experience=ExperienceLevel.BEGINNER,
        goal="add search to our intranet",
    )


def make_analysis() -> SkillAnalysis:
    return SkillAnalysis(
        category="Cloud",
        difficulty=ExperienceLevel.INTERMEDIATE,
        estimated_hours=60,
        prerequisites=["REST basics", "An Azure subscription"],
        career_paths=["Search Engineer"],
    )


def make_source(
    url: str, kind: ResourceKind = ResourceKind.DOCS, mentions_skill: bool = True
) -> ResearchSource:
    return ResearchSource(
        title="t", url=url, kind=kind, summary="s", mentions_skill=mentions_skill
    )


def test_prompt_carries_the_upstream_output_and_the_pages_found():
    hits = [make_hit("https://learn.microsoft.com/azure/search/", "Azure AI Search docs")]

    prompt = build_prompt(make_request(), make_analysis(), hits)

    assert "Azure AI Search" in prompt
    assert "intermediate" in prompt  # the skill's difficulty
    assert "beginner" in prompt  # the learner's level
    assert "add search to our intranet" in prompt
    assert "REST basics" in prompt
    assert "60" in prompt
    assert "0. " in prompt  # the page's number, which is what the model answers with
    assert "https://learn.microsoft.com/azure/search/" in prompt


def test_the_prompt_never_shows_the_category_the_model_may_have_corrupted():
    analysis = make_analysis()
    analysis.category = "Conversational AI / Bot Development"

    assert "Bot Development" not in build_prompt(make_request(), analysis, [])


def test_prompt_says_none_rather_than_leaving_prerequisites_blank():
    analysis = make_analysis()
    analysis.prerequisites = []

    assert "none" in build_prompt(make_request(), analysis, [])


# --- choosing from real pages ---


def test_a_pick_takes_its_url_from_the_search_result_not_the_model():
    hits = [make_hit("https://learn.microsoft.com/azure/search/", "Azure AI Search docs")]

    sources = collect(hits, picking(0, kind=ResourceKind.MICROSOFT_LEARN))

    assert sources[0].url == "https://learn.microsoft.com/azure/search/"
    assert sources[0].title == "Azure AI Search docs"
    assert sources[0].kind == ResourceKind.MICROSOFT_LEARN


@pytest.mark.parametrize("index", [1, -1, 99])
def test_a_number_outside_the_list_is_ignored_rather_than_trusted(index):
    assert collect([make_hit("https://a.dev")], picking(index)) == []


def test_the_same_page_chosen_twice_is_only_kept_once():
    hits = [make_hit("https://a.dev"), make_hit("https://b.dev")]

    assert [s.url for s in collect(hits, picking(0, 1, 0))] == ["https://a.dev", "https://b.dev"]


def test_choosing_nothing_is_allowed():
    assert collect([make_hit("https://a.dev")], picking()) == []


def test_the_model_cannot_make_us_fetch_more_than_the_cap():
    hits = [make_hit(f"https://x{i}.dev") for i in range(50)]

    assert len(collect(hits, picking(*range(50)))) == MAX_SOURCES


# --- the pipeline around it ---


@pytest.mark.asyncio
async def test_search_runs_before_the_model_and_asks_for_the_skill_by_name(monkeypatch):
    queries: list[str] = []

    async def fake_search(query):
        queries.append(query)
        return [make_hit("https://learn.microsoft.com/azure/search/")]

    async def fake_verify(sources, skill):
        return sources

    monkeypatch.setattr(research_module, "search_web", fake_search)
    monkeypatch.setattr(research_module, "get_research_agent", lambda: StubAgent(picking(0)))
    monkeypatch.setattr(research_module, "verify_sources", fake_verify)

    await gather_sources(make_request(), make_analysis())

    assert queries == ["Azure AI Search"]


@pytest.mark.asyncio
async def test_finding_no_pages_does_not_send_the_model_an_empty_list(monkeypatch):
    async def fake_search(query):
        return []

    called = False

    def agent():
        nonlocal called
        called = True
        return StubAgent(picking())

    monkeypatch.setattr(research_module, "search_web", fake_search)
    monkeypatch.setattr(research_module, "get_research_agent", agent)

    assert await gather_sources(make_request(), make_analysis()) == []
    assert called is False


@pytest.mark.asyncio
async def test_dead_links_are_dropped_and_survivors_are_ranked(monkeypatch):
    hits = [
        make_hit("https://blog.example.com/post"),
        make_hit("https://dead.example.com/gone"),
        make_hit("https://learn.microsoft.com/azure/search/"),
    ]

    async def fake_search(query):
        return hits

    selection = SourceSelection(
        picks=[
            SourcePick(index=0, kind=ResourceKind.BLOG, summary="s"),
            SourcePick(index=1, kind=ResourceKind.DOCS, summary="s"),
            SourcePick(index=2, kind=ResourceKind.DOCS, summary="s"),
        ]
    )
    monkeypatch.setattr(research_module, "search_web", fake_search)
    monkeypatch.setattr(research_module, "get_research_agent", lambda: StubAgent(selection))

    async def fake_verify(sources, skill):
        return [s for s in sources if "dead" not in s.url]

    monkeypatch.setattr(research_module, "verify_sources", fake_verify)

    sources = await gather_sources(make_request(), make_analysis())

    assert [s.url for s in sources] == [
        "https://learn.microsoft.com/azure/search/",  # docs outrank blogs
        "https://blog.example.com/post",
    ]


@pytest.mark.asyncio
async def test_verification_is_asked_about_the_skill_the_learner_named(monkeypatch):
    async def fake_search(query):
        return [make_hit("https://x.dev")]

    monkeypatch.setattr(research_module, "search_web", fake_search)
    monkeypatch.setattr(research_module, "get_research_agent", lambda: StubAgent(picking(0)))

    asked: list[str] = []

    async def fake_verify(sources, skill):
        asked.append(skill)
        return sources

    monkeypatch.setattr(research_module, "verify_sources", fake_verify)

    await gather_sources(make_request(), make_analysis())

    # Not analysis.category, which is the field the model corrupts when it does not know the skill.
    assert asked == ["Azure AI Search"]


# --- refusing to research the wrong subject ---


def test_sources_that_name_the_skill_are_accepted():
    sources = [
        make_source("https://a.dev", mentions_skill=False),
        make_source("https://b.dev", mentions_skill=True),
    ]

    confirm_on_topic("Azure AI Search", sources)


def test_verifying_nothing_at_all_stops_the_run():
    with pytest.raises(ValueError, match="from the model's own memory"):
        confirm_on_topic("Microsoft Agent Framework", [])


def test_sources_that_never_name_the_skill_stop_the_run():
    sources = [
        make_source("https://learn.microsoft.com/azure/bot-service/", mentions_skill=False),
        make_source("https://github.com/microsoft/botframework-sdk", mentions_skill=False),
    ]

    with pytest.raises(ValueError, match="wrong subject"):
        confirm_on_topic("Microsoft Agent Framework", sources)


def test_the_refusal_says_how_many_sources_and_what_they_were_about():
    source = make_source("https://learn.microsoft.com/azure/bot-service/", mentions_skill=False)
    source.title = "Azure Bot Service documentation"

    with pytest.raises(ValueError) as raised:
        confirm_on_topic("Microsoft Agent Framework", [source])

    assert "Microsoft Agent Framework" in str(raised.value)
    assert "Azure Bot Service documentation" in str(raised.value)


@pytest.mark.asyncio
async def test_executor_stores_sources_and_forwards_state(monkeypatch):
    async def fake_gather(request, analysis):
        return [make_source("https://learn.microsoft.com/azure/search/")]

    monkeypatch.setattr(research_module, "gather_sources", fake_gather)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.skill_analysis = make_analysis()
    ctx = CapturingContext()

    await ResearchExecutor(id=WorkflowStep.RESEARCH).run(state, ctx)

    assert len(state.research) == 1
    assert state.completed_steps == [WorkflowStep.RESEARCH]
    assert ctx.messages == [state]


@pytest.mark.asyncio
async def test_finding_nothing_fails_the_step_instead_of_guessing(monkeypatch):
    async def fake_gather(request, analysis):
        return []

    monkeypatch.setattr(research_module, "gather_sources", fake_gather)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.skill_analysis = make_analysis()
    executor = ResearchExecutor(id=WorkflowStep.RESEARCH)

    with pytest.raises(ValueError):
        await executor.run(state, CapturingContext())

    assert WorkflowStep.RESEARCH not in state.completed_steps


def test_three_nodes_report_twenty_percent():
    completed = [WorkflowStep.REQUIREMENT, WorkflowStep.SKILL_ANALYSIS, WorkflowStep.RESEARCH]

    assert progress_percent(completed) == 20
