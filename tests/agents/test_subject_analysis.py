"""Offline tests for subject-analysis-agent: the loop, the budgets and the invariant.

The model chooses the strategy here, but code performs every search and fetch. These tests
pin that split, because an agent that searches and judges in one turn cannot be audited: one
reported that Rust does not exist on a run in three, with no sources, which is
indistinguishable from never having searched at all.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.agents import subject_analysis as subject_mod
from backend.agents.subject_analysis import (
    MAX_SEARCHES,
    SubjectAnalysisExecutor,
    investigate,
    is_identified,
    number_documents,
    pick,
    run_search,
)
from backend.services.web_search import SearchHit
from backend.workflow.state import (
    STEP_WEIGHTS,
    CourseState,
    IdentityStatus,
    LearningRequest,
    SearchPlan,
    SourceDocument,
    SourceKind,
    SubjectAnalysis,
    SubjectEvidence,
    SubjectTrace,
    TargetedSearch,
    TechnicalSubjectType,
    WorkflowStep,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


def hit(url: str, title: str = "t") -> SearchHit:
    return SearchHit(title=title, url=url, snippet="s")


def document(url: str) -> SourceDocument:
    return SourceDocument(title="t", url=url, text="body " * 100)


def analysis(status: IdentityStatus = IdentityStatus.CONFIRMED) -> SubjectAnalysis:
    return SubjectAnalysis(
        identity_status=status,
        canonical_name="Microsoft Agent Framework",
        subject_type=TechnicalSubjectType.SOFTWARE_FRAMEWORK,
    )


def wire(monkeypatch, *, hits, plans, documents, verdict=None):
    """Replaces the two model calls and both I/O calls, leaving the loop itself real."""
    searched: list[tuple[str, list[str] | None]] = []
    fetched: list[list[str]] = []
    remaining = list(plans)

    async def fake_search(query, domains=None):
        searched.append((query, list(domains) if domains else None))
        return list(hits)

    async def fake_plan(subject, found):
        return remaining.pop(0) if remaining else SearchPlan()

    async def fake_fetch(selected, limit):
        fetched.append([item.url for item in selected])
        return list(documents)

    async def fake_analyse(subject, docs):
        return verdict or analysis()

    monkeypatch.setattr(subject_mod, "search_web", fake_search)
    monkeypatch.setattr(subject_mod, "plan_next", fake_plan)
    monkeypatch.setattr(subject_mod, "fetch_documents", fake_fetch)
    monkeypatch.setattr(subject_mod, "analyse_documents", fake_analyse)
    return searched, fetched


# --- selecting sources ---


def test_sources_are_chosen_by_number_not_by_url():
    """A URL the model retypes is a URL it can mistype, so it never gets to name one."""
    hits = [hit("https://a.example"), hit("https://b.example"), hit("https://c.example")]

    assert [item.url for item in pick(hits, [3, 1], budget=5)] == [
        "https://c.example",
        "https://a.example",
    ]


def test_a_number_outside_the_list_is_dropped_rather_than_wrapped():
    """A silent modulo would hand back a source the model never chose."""
    assert pick([hit("https://a.example")], [7, 0, -1], budget=5) == []


def test_the_same_number_twice_is_still_one_source():
    assert len(pick([hit("https://a.example")], [1, 1], budget=5)) == 1


def test_selection_stops_at_the_budget():
    hits = [hit(f"https://{n}.example") for n in range(6)]

    assert len(pick(hits, [1, 2, 3, 4, 5], budget=2)) == 2


# --- the search budget ---


@pytest.mark.asyncio
async def test_every_search_is_recorded_before_it_is_made(monkeypatch):
    """The trace is what lets a refusal be told apart from a run that never looked."""

    async def fake_search(query, domains=None):
        return [hit("https://a.example")]

    monkeypatch.setattr(subject_mod, "search_web", fake_search)
    trace = SubjectTrace()

    await run_search("rust", None, trace)

    assert trace.searches == ["'rust' domains=any"]


@pytest.mark.asyncio
async def test_the_budget_refuses_a_further_search(monkeypatch):
    async def fake_search(query, domains=None):
        return [hit("https://a")]

    monkeypatch.setattr(subject_mod, "search_web", fake_search)
    trace = SubjectTrace(searches=[f"q{n}" for n in range(MAX_SEARCHES)])

    assert await run_search("rust", None, trace) == []
    assert "search budget spent" in trace.notes[0]


@pytest.mark.asyncio
async def test_a_repeated_query_is_not_run_twice(monkeypatch):
    """Measured: the planner re-issued a byte-identical query as its targeted search."""
    calls: list[str] = []

    async def fake_search(query, domains=None):
        calls.append(query)
        return []

    monkeypatch.setattr(subject_mod, "search_web", fake_search)
    trace = SubjectTrace()

    await run_search("rust", None, trace)
    await run_search("rust", None, trace)

    assert calls == ["rust"]


# --- the loop ---


@pytest.mark.asyncio
async def test_the_normal_path_costs_one_search_and_no_more(monkeypatch):
    """Measured over 13 subjects: 11 finished in exactly one search and two fetches."""
    searched, fetched = wire(
        monkeypatch,
        hits=[hit("https://learn.microsoft.com/x"), hit("https://github.com/y")],
        plans=[SearchPlan(fetch=[1, 2])],
        documents=[document("https://learn.microsoft.com/x"), document("https://github.com/y")],
    )

    result, documents, trace = await investigate("Microsoft Agent Framework")

    assert len(searched) == 1
    assert fetched == [["https://learn.microsoft.com/x", "https://github.com/y"]]
    assert result.identity_status is IdentityStatus.CONFIRMED
    assert len(documents) == 2 and len(trace.fetched_urls) == 2


@pytest.mark.asyncio
async def test_a_targeted_search_only_happens_because_the_plan_asked(monkeypatch):
    searched, _ = wire(
        monkeypatch,
        hits=[hit("https://rust-lang.org/x")],
        plans=[
            SearchPlan(
                targeted_searches=[
                    TargetedSearch(
                        query="rust docs", domains=["rust-lang.org"], reason="first-party missing"
                    )
                ],
            ),
            SearchPlan(fetch=[1]),
        ],
        documents=[document("https://rust-lang.org/x")],
    )

    await investigate("Rust")

    assert searched == [("Rust", None), ("rust docs", ["rust-lang.org"])]


@pytest.mark.asyncio
async def test_a_plan_that_selects_nothing_is_not_second_guessed(monkeypatch):
    """An empty selection is the planner saying nothing here settles it. Reading the top hit
    anyway confirmed GUITAR, a GUI testing framework, for a learner who asked about the
    instrument — the fallback turned a refusal into a confirmation."""
    _, fetched = wire(
        monkeypatch,
        hits=[hit("https://a.example"), hit("https://b.example")],
        plans=[SearchPlan(fetch=[])],
        documents=[document("https://a.example")],
    )

    result, documents, trace = await investigate("Guitar")

    assert fetched == []
    assert result.identity_status is IdentityStatus.INSUFFICIENT_EVIDENCE
    assert documents == []
    assert any("settles the identity" in note for note in trace.notes)


@pytest.mark.asyncio
async def test_snippets_alone_can_never_confirm_a_subject(monkeypatch):
    """Snippets are strong enough to FIND a subject and far too weak to identify one, so a run
    with no readable page never reaches the analyser at all."""
    analysed: list[str] = []

    async def must_not_run(subject, docs):
        analysed.append(subject)
        raise AssertionError("the analyser must not judge a run with no documents")

    wire(
        monkeypatch,
        hits=[hit("https://npmjs.com/")],
        plans=[SearchPlan(fetch=[1])],
        documents=[],
    )
    monkeypatch.setattr(subject_mod, "analyse_documents", must_not_run)

    result, documents, trace = await investigate("Blorptagon SDK")

    assert result.identity_status is IdentityStatus.INSUFFICIENT_EVIDENCE
    assert analysed == [] and documents == []
    assert any("not put to the model" in note for note in trace.notes)


@pytest.mark.asyncio
async def test_a_search_that_finds_nothing_asks_rather_than_failing(monkeypatch):
    """Zero hits is an evidence problem, not a crash: a failed job reads as our bug, when the
    honest answer is that we could not establish the subject."""
    wire(monkeypatch, hits=[], plans=[], documents=[])

    result, documents, trace = await investigate("x")

    assert result.identity_status is IdentityStatus.INSUFFICIENT_EVIDENCE
    assert documents == [] and trace.searches


def test_documents_are_numbered_from_one_so_evidence_can_cite_them():
    text = number_documents([document("https://a.example"), document("https://b.example")])

    assert text.startswith("[1] ") and "[2] " in text


def test_evidence_records_how_authoritative_its_document_was():
    """Recorded, not gated: a "confirmed needs a first-party source" rule is a threshold, and
    we cannot measure whether it would refuse OAuth 2.0 until the input to it exists."""
    item = SubjectEvidence(
        document_index=1,
        source_kind=SourceKind.FIRST_PARTY_DOCUMENTATION,
        supporting_claim="c",
    )

    assert item.source_kind is SourceKind.FIRST_PARTY_DOCUMENTATION


def test_evidence_cannot_be_recorded_without_a_source_kind():
    with pytest.raises(ValidationError):
        SubjectEvidence(document_index=1, supporting_claim="c")


def test_the_planner_is_never_asked_what_the_subject_is():
    """It sees snippets, which find a subject but cannot identify one. `assessment` and
    `looks_ambiguous` invited exactly that judgement and nothing ever read them."""
    assert set(SearchPlan.model_fields) == {"fetch", "targeted_searches"}


# --- the invariant ---


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (IdentityStatus.CONFIRMED, True),
        (IdentityStatus.AMBIGUOUS, False),
        (IdentityStatus.UNRECOGNISED, False),
        (IdentityStatus.INSUFFICIENT_EVIDENCE, False),
    ],
)
def test_only_a_confirmed_subject_may_reach_the_course(status, expected):
    state = CourseState(job_id="j", user_id="u", prompt="p", subject=analysis(status))

    assert is_identified(state) is expected


def test_a_subject_that_was_never_analysed_is_not_identified():
    state = CourseState(job_id="j", user_id="u", prompt="p")

    assert not is_identified(state)


@pytest.mark.asyncio
async def test_the_executor_stores_the_analysis_the_sources_and_the_trace(monkeypatch):
    trace = SubjectTrace(searches=["one"], fetched_urls=["https://a.example"])

    async def fake_investigate(subject: str):
        assert subject == "Microsoft Agent Framework"
        return analysis(), [document("https://a.example")], trace

    monkeypatch.setattr(subject_mod, "investigate", fake_investigate)
    state = CourseState(
        job_id="j",
        user_id="u",
        prompt="p",
        request=LearningRequest(is_learning_request=True, skill="Microsoft Agent Framework"),
    )
    ctx = CapturingContext()

    await SubjectAnalysisExecutor(id=WorkflowStep.SUBJECT_ANALYSIS).run(state, ctx)

    assert state.subject is not None and state.sources[0].url == "https://a.example"
    assert state.subject_trace is trace
    assert state.completed_steps == [WorkflowStep.SUBJECT_ANALYSIS]
    assert ctx.messages == [state]


def test_the_node_carries_weight_of_its_own():
    """It runs a real search, two page fetches and two model calls."""
    assert STEP_WEIGHTS[WorkflowStep.SUBJECT_ANALYSIS] > 0


def test_the_pipeline_total_is_unchanged_by_the_new_node():
    assert sum(STEP_WEIGHTS.values()) == 100
