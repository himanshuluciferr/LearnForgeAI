"""Offline tests for research-agent: prompt building, the verify/rank pipeline, wiring."""

import pytest

from backend.agents import research as research_module
from backend.agents.research import MAX_SOURCES, ResearchExecutor, build_prompt, gather_sources
from backend.workflow.state import (
    CourseState,
    ExperienceLevel,
    LearningRequest,
    ResearchBundle,
    ResearchSource,
    ResourceKind,
    SubjectAnalysis,
    TechnicalSubjectType,
    IdentityStatus,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


class StubResponse:
    def __init__(self, value: ResearchBundle) -> None:
        self.value = value


class StubAgent:
    def __init__(self, bundle: ResearchBundle) -> None:
        self.bundle = bundle
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        return StubResponse(self.bundle)


def make_request() -> LearningRequest:
    return LearningRequest(
        is_learning_request=True,
        skill="Azure AI Search",
        experience=ExperienceLevel.BEGINNER,
        goal="add search to our intranet",
    )


def make_subject() -> SubjectAnalysis:
    return SubjectAnalysis(
        identity_status=IdentityStatus.CONFIRMED,
        canonical_name="Azure AI Search",
        subject_type=TechnicalSubjectType.SERVICE,
        description="A managed search service.",
        scope=["indexes", "skillsets"],
        prerequisites=["REST basics", "An Azure subscription"],
    )


def make_source(url: str, kind: ResourceKind = ResourceKind.DOCS) -> ResearchSource:
    return ResearchSource(title="t", url=url, kind=kind, summary="s")


def test_prompt_carries_both_upstream_agents_output():
    prompt = build_prompt(make_request(), make_subject())

    assert "Azure AI Search" in prompt
    assert "A managed search service." in prompt
    assert "indexes" in prompt
    assert "beginner" in prompt  # the learner's level
    assert "add search to our intranet" in prompt
    assert "REST basics" in prompt


def test_prompt_says_none_rather_than_leaving_prerequisites_blank():
    analysis = make_subject()
    analysis.prerequisites = []

    assert "none" in build_prompt(make_request(), analysis)


@pytest.mark.asyncio
async def test_dead_links_are_dropped_and_survivors_are_ranked(monkeypatch):
    bundle = ResearchBundle(
        sources=[
            make_source("https://blog.example.com/post", ResourceKind.BLOG),
            make_source("https://dead.example.com/gone"),
            make_source("https://learn.microsoft.com/azure/search/", ResourceKind.DOCS),
        ]
    )
    monkeypatch.setattr(research_module, "get_research_agent", lambda: StubAgent(bundle))

    async def fake_verify(sources):
        return [s for s in sources if "dead" not in s.url]

    monkeypatch.setattr(research_module, "verify_sources", fake_verify)

    sources = await gather_sources(make_request(), make_subject())

    assert [s.url for s in sources] == [
        "https://learn.microsoft.com/azure/search/",  # docs outrank blogs
        "https://blog.example.com/post",
    ]


@pytest.mark.asyncio
async def test_the_model_cannot_make_us_fetch_more_than_the_cap(monkeypatch):
    bundle = ResearchBundle(sources=[make_source(f"https://x{i}.dev") for i in range(50)])
    monkeypatch.setattr(research_module, "get_research_agent", lambda: StubAgent(bundle))

    seen: list[int] = []

    async def fake_verify(sources):
        seen.append(len(sources))
        return sources

    monkeypatch.setattr(research_module, "verify_sources", fake_verify)

    await gather_sources(make_request(), make_subject())

    assert seen == [MAX_SOURCES]


@pytest.mark.asyncio
async def test_executor_stores_sources_and_forwards_state(monkeypatch):
    async def fake_gather(request, analysis):
        return [make_source("https://learn.microsoft.com/azure/search/")]

    monkeypatch.setattr(research_module, "gather_sources", fake_gather)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.subject = make_subject()
    ctx = CapturingContext()

    await ResearchExecutor(id=WorkflowStep.RESEARCH).run(state, ctx)

    assert len(state.research) == 1
    assert state.completed_steps == [WorkflowStep.RESEARCH]
    assert ctx.messages == [state]


@pytest.mark.asyncio
async def test_finding_nothing_still_completes_the_step(monkeypatch):
    async def fake_gather(request, analysis):
        return []

    monkeypatch.setattr(research_module, "gather_sources", fake_gather)

    state = CourseState(job_id="j", user_id="u", prompt="p")
    state.request = make_request()
    state.subject = make_subject()

    await ResearchExecutor(id=WorkflowStep.RESEARCH).run(state, CapturingContext())

    assert state.research == []
    assert WorkflowStep.RESEARCH in state.completed_steps


def test_three_nodes_report_twenty_percent():
    completed = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.RESEARCH,
    ]

    assert progress_percent(completed) == 20
