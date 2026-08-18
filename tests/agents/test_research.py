"""Offline tests for research-agent: query planning, selection, fetching, wiring.

The contract this node now has to keep is that a source carries the page text. Before it did
not, and every chapter was written from model memory with a citation attached.
"""

import pytest

from backend.agents import research as research_module
from backend.agents.research import (
    MAX_QUERIES,
    MAX_SOURCES,
    ResearchExecutor,
    build_prompt,
    classify,
    gather_sources,
    is_microsoft_subject,
    merge,
    plan_queries,
    read_documentation,
    written_for,
)
from backend.services.web_search import SearchHit
from backend.workflow.state import (
    CourseState,
    ExperienceLevel,
    IdentityStatus,
    LearningRequest,
    ResearchSource,
    ResourceKind,
    SourceDocument,
    SourceSelection,
    SubjectAnalysis,
    TechnicalSubjectType,
    WorkflowStep,
    progress_percent,
)


class CapturingContext:
    def __init__(self) -> None:
        self.messages: list[CourseState] = []

    async def send_message(self, message: CourseState) -> None:
        self.messages.append(message)


def make_request(**overrides) -> LearningRequest:
    return LearningRequest(
        **{
            "is_learning_request": True,
            "skill": "Azure AI Search",
            "experience": ExperienceLevel.BEGINNER,
            "goal": "add search to our intranet",
            **overrides,
        }
    )


def make_subject(**overrides) -> SubjectAnalysis:
    return SubjectAnalysis(
        **{
            "identity_status": IdentityStatus.CONFIRMED,
            "canonical_name": "Azure AI Search",
            "subject_type": TechnicalSubjectType.SERVICE,
            "description": "A managed search service.",
            "scope": ["indexes", "skillsets", "vector search", "scoring profiles"],
            "prerequisites": ["REST basics", "An Azure subscription"],
            **overrides,
        }
    )


def hit(url: str, title: str = "t") -> SearchHit:
    return SearchHit(title=title, url=url, snippet="s")


def document(url: str, text: str = "body " * 100) -> SourceDocument:
    return SourceDocument(title="t", url=url, text=text)


def make_source(url: str, kind: ResourceKind = ResourceKind.DOCS) -> ResearchSource:
    return ResearchSource(title="t", url=url, kind=kind, text="s")


def wire(monkeypatch, *, hits, picks, documents):
    """Replaces the search, the model call and the fetch, leaving the node's own logic real."""
    searched: list[str] = []
    fetched: list[list[str]] = []

    async def fake_search(query, domains=None):
        searched.append(query)
        return list(hits)

    async def fake_select(request, subject, found):
        return SourceSelection(picks=list(picks))

    async def fake_fetch(selected, limit):
        fetched.append([item.url for item in selected])
        return list(documents)

    monkeypatch.setattr(research_module, "search_web", fake_search)
    monkeypatch.setattr(research_module, "select_sources", fake_select)
    monkeypatch.setattr(research_module, "fetch_documents", fake_fetch)
    return searched, fetched


# --- query planning ---


def test_queries_start_from_the_subject_then_cover_its_areas():
    """The areas came from pages node 2 read, so the queries are grounded rather than guessed."""
    assert plan_queries(make_subject()) == [
        "Azure AI Search",
        "Azure AI Search indexes",
        "Azure AI Search skillsets",
        "Azure AI Search vector search",
    ]


def test_the_number_of_searches_is_capped_however_many_areas_were_found():
    """Each search is a billed call of a few tens of seconds."""
    wide = make_subject(scope=[f"area {n}" for n in range(30)])

    assert len(plan_queries(wide)) == MAX_QUERIES


def test_a_subject_with_no_areas_is_still_searched_for():
    assert plan_queries(make_subject(scope=[])) == ["Azure AI Search"]


# --- classification ---


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://learn.microsoft.com/azure/search/", ResourceKind.MICROSOFT_LEARN),
        ("https://github.com/microsoft/agent-framework", ResourceKind.GITHUB),
        ("https://www.youtube.com/watch?v=x", ResourceKind.VIDEO),
        ("https://medium.com/@someone/post", ResourceKind.BLOG),
        ("https://doc.rust-lang.org/book/", ResourceKind.DOCS),
    ],
)
def test_kind_is_read_off_the_host_rather_than_asked_for(url, expected):
    assert classify(url) is expected


# --- the pipeline ---


@pytest.mark.asyncio
async def test_a_source_carries_the_page_text(monkeypatch):
    """The whole point of the node: without this the pipeline is not retrieval-augmented."""
    wire(
        monkeypatch,
        hits=[hit("https://learn.microsoft.com/azure/search/")],
        picks=[1],
        documents=[document("https://learn.microsoft.com/azure/search/", "An index is a store.")],
    )

    sources = await gather_sources(make_request(), make_subject())

    assert [source.text for source in sources] == ["An index is a store."]
    assert sources[0].kind is ResourceKind.MICROSOFT_LEARN


@pytest.mark.asyncio
async def test_only_the_selected_results_are_fetched(monkeypatch):
    """Selection is by number into the list we supplied, so a mistyped URL is unrepresentable."""
    _, fetched = wire(
        monkeypatch,
        hits=[hit(f"https://x{n}.example") for n in range(5)],
        picks=[3, 1],
        documents=[document("https://x2.example")],
    )

    await gather_sources(make_request(), make_subject())

    assert fetched == [["https://x2.example", "https://x0.example"]]


@pytest.mark.asyncio
async def test_survivors_are_ranked_by_kind(monkeypatch):
    wire(
        monkeypatch,
        hits=[hit("https://a.example")],
        picks=[1],
        documents=[
            document("https://medium.com/post"),
            document("https://learn.microsoft.com/azure/search/"),
        ],
    )

    sources = await gather_sources(make_request(), make_subject())

    assert [source.url for source in sources] == [
        "https://learn.microsoft.com/azure/search/",
        "https://medium.com/post",
    ]


@pytest.mark.asyncio
async def test_the_model_cannot_make_us_fetch_more_than_the_cap(monkeypatch):
    _, fetched = wire(
        monkeypatch,
        hits=[hit(f"https://x{n}.example") for n in range(50)],
        picks=list(range(1, 51)),
        documents=[document("https://x0.example")],
    )

    await gather_sources(make_request(), make_subject())

    assert len(fetched[0]) == MAX_SOURCES


# --- empty is a failure, not a degraded pass ---


@pytest.mark.asyncio
async def test_finding_nothing_fails_rather_than_writing_from_memory(monkeypatch):
    """Falling back to "write from general knowledge" would make the grounding optional, which
    is the thing this node exists to stop."""
    wire(monkeypatch, hits=[], picks=[], documents=[])

    with pytest.raises(ValueError, match="found nothing"):
        await gather_sources(make_request(), make_subject())


@pytest.mark.asyncio
async def test_selecting_nothing_is_a_failure(monkeypatch):
    wire(monkeypatch, hits=[hit("https://a.example")], picks=[], documents=[])

    with pytest.raises(ValueError, match="selected none"):
        await gather_sources(make_request(), make_subject())


# --- the documentation provider is routed on evidence, not on a guess --------------


def test_a_subject_documented_on_microsoft_hosts_gets_the_documentation_provider():
    assert is_microsoft_subject([document("https://learn.microsoft.com/agent-framework/")])
    assert is_microsoft_subject([document("https://azure.microsoft.com/blog/x")])


def test_a_subject_documented_anywhere_else_does_not():
    """Learn answers an off-corpus question with its nearest neighbour rather than with
    nothing: measured, 'double-entry bookkeeping' comes back as Dynamics 365 finance pages.
    Asking it about a non-Microsoft subject would substitute a product for the subject."""
    assert not is_microsoft_subject([document("https://doc.rust-lang.org/book/")])
    assert not is_microsoft_subject([document("https://git-scm.com/docs/git-rebase")])


def test_no_evidence_means_no_documentation_call():
    assert not is_microsoft_subject([])


@pytest.mark.asyncio
async def test_the_documentation_provider_is_skipped_for_a_non_microsoft_subject(monkeypatch):
    async def fail(*args, **kwargs):
        raise AssertionError("Learn must not be asked about a non-Microsoft subject")

    monkeypatch.setattr(research_module, "read_learn_docs", fail)

    assert (
        await read_documentation(
            make_request(), make_subject(), [document("https://git-scm.com/docs")]
        )
        == []
    )


@pytest.mark.asyncio
async def test_documentation_pages_become_sources_with_their_text(monkeypatch):
    stub_documentation(
        monkeypatch, [document("https://learn.microsoft.com/agent-framework/", "real page text")]
    )

    sources = await read_documentation(
        make_request(), make_subject(), [document("https://learn.microsoft.com/agent-framework/")]
    )

    assert [source.text for source in sources] == ["real page text"]
    assert sources[0].kind == ResourceKind.MICROSOFT_LEARN


# --- one course, one programming language -------------------------------------------


def in_language(url: str, language: str) -> SourceDocument:
    return SourceDocument(title="t", url=url, text="body " * 100, language=language)


def stub_documentation(monkeypatch, pages: list[SourceDocument], samples: list | None = None):
    """Both Learn calls must be replaced. Stubbing only one leaves the other reaching the real
    server from the offline suite, which shows up as a slow run rather than a failure."""

    async def fake_pages(queries, limit):
        return pages

    async def fake_samples(queries, language, *args, **kwargs):
        return samples or []

    monkeypatch.setattr(research_module, "read_learn_docs", fake_pages)
    monkeypatch.setattr(research_module, "read_code_samples", fake_samples)


def test_pages_written_for_another_language_are_dropped():
    """Measured: keeping both variants taught 27 C# examples and 7 Python ones in one course."""
    kept = written_for(
        [in_language("https://x/a", "csharp"), in_language("https://x/b", "python")], "python"
    )

    assert [page.url for page in kept] == ["https://x/b"]


def test_language_neutral_pages_are_always_kept():
    """A page with no variant suits any course, and dropping it would lose real evidence."""
    kept = written_for([document("https://x/a"), in_language("https://x/b", "csharp")], "python")

    assert [page.url for page in kept] == ["https://x/a"]


def test_the_learners_own_name_for_the_language_still_matches():
    kept = written_for([in_language("https://x/a", "csharp")], "C#")

    assert len(kept) == 1


@pytest.mark.asyncio
async def test_an_unstated_language_leaves_a_python_course(monkeypatch):
    stub_documentation(
        monkeypatch,
        [
            in_language("https://learn.microsoft.com/a", "csharp"),
            in_language("https://learn.microsoft.com/b", "python"),
        ],
    )

    sources = await read_documentation(
        make_request(), make_subject(), [document("https://learn.microsoft.com/x")]
    )

    assert [source.url for source in sources] == ["https://learn.microsoft.com/b"]


@pytest.mark.asyncio
async def test_a_stated_language_is_obeyed(monkeypatch):
    stub_documentation(
        monkeypatch,
        [
            in_language("https://learn.microsoft.com/a", "csharp"),
            in_language("https://learn.microsoft.com/b", "python"),
        ],
    )

    sources = await read_documentation(
        make_request(programming_language="csharp"),
        make_subject(),
        [document("https://learn.microsoft.com/x")],
    )

    assert [source.url for source in sources] == ["https://learn.microsoft.com/a"]


@pytest.mark.asyncio
async def test_code_samples_are_added_to_the_documentation_pages(monkeypatch):
    """Samples are filtered by language on the server, so they are the one part of the Learn
    corpus that cannot skew a course towards another language."""
    stub_documentation(
        monkeypatch,
        [in_language("https://learn.microsoft.com/a", "python")],
        samples=[in_language("https://learn.microsoft.com/sample", "python")],
    )

    sources = await read_documentation(
        make_request(), make_subject(), [document("https://learn.microsoft.com/x")]
    )

    assert [source.url for source in sources] == [
        "https://learn.microsoft.com/a",
        "https://learn.microsoft.com/sample",
    ]


def test_a_page_that_was_both_searched_for_and_documented_is_kept_once():
    searched = [make_source("https://learn.microsoft.com/agent-framework/")]
    documented = [make_source("https://learn.microsoft.com/agent-framework")]

    assert len(merge(searched, documented)) == 1


def test_merge_keeps_the_searched_sources_first_and_adds_the_rest():
    searched = [make_source("https://a.example")]
    documented = [make_source("https://learn.microsoft.com/b")]

    assert [source.url for source in merge(searched, documented)] == [
        "https://a.example",
        "https://learn.microsoft.com/b",
    ]


@pytest.mark.asyncio
async def test_reading_nothing_is_a_failure(monkeypatch):
    """Every selected page could 403 or be nav furniture; a course cannot be built on that."""
    wire(monkeypatch, hits=[hit("https://a.example")], picks=[1], documents=[])

    with pytest.raises(ValueError, match="could not read"):
        await gather_sources(make_request(), make_subject())


# --- prompt and wiring ---


def test_the_selection_prompt_carries_what_node_two_established():
    prompt = build_prompt(make_request(), make_subject(), [hit("https://a.example", "Result A")])

    assert "Azure AI Search" in prompt
    assert "A managed search service." in prompt
    assert "indexes" in prompt
    assert "beginner" in prompt
    assert "[1] Result A" in prompt


@pytest.mark.asyncio
async def test_executor_stores_sources_and_forwards_state(monkeypatch):
    async def fake_gather(request, subject, evidence=None):
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


def test_three_nodes_report_twenty_percent():
    completed = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.RESEARCH,
    ]

    assert progress_percent(completed) == 20
