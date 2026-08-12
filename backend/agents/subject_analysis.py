"""subject-analysis-agent — establishes what the requested subject actually is.

The semantic checkpoint. Node 1 captures what the learner asked for; this node decides whether
we know what that is, before any of the expensive half of the run is paid for.

Two model calls on the normal path — plan, then analyse — and code performs every search and
fetch in between. The model chooses the strategy; we execute it and record what happened, which
is what lets a refusal be told apart from a run that never looked.
"""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.services.page_fetch import fetch_documents
from backend.services.web_search import SearchHit, dedupe, search_web
from backend.workflow.state import (
    CourseState,
    IdentityStatus,
    SearchPlan,
    SourceDocument,
    SubjectAnalysis,
    SubjectTrace,
    TechnicalSubjectType,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

PLANNER_AGENT_NAME = "subject-planner-agent"
ANALYSER_AGENT_NAME = "subject-analysis-agent"

# Measured over 13 subjects: 11 finished in one search and two fetches. The budget exists for
# the negative case, which is the expensive one — an invented subject spent all three searches.
MAX_SEARCHES = 3
MAX_FETCHES = 5
CHARS_PER_DOCUMENT = 6_000


@lru_cache
def get_planner_agent() -> Agent:
    return get_chat_client().as_agent(
        name=PLANNER_AGENT_NAME,
        instructions=load_prompt("subject_planner"),
        default_options={"response_format": SearchPlan},
    )


@lru_cache
def get_analysis_agent() -> Agent:
    return get_chat_client().as_agent(
        name=ANALYSER_AGENT_NAME,
        instructions=load_prompt("subject_analysis"),
        default_options={"response_format": SubjectAnalysis},
    )


def number_hits(hits: list[SearchHit]) -> str:
    return "\n".join(
        f"[{number}] {hit.title}\n    {hit.url}\n    {hit.snippet[:180]}"
        for number, hit in enumerate(hits, start=1)
    )


def number_documents(documents: list[SourceDocument]) -> str:
    return "\n\n".join(
        f"[{number}] {document.title}\n{document.url}\n{document.text[:CHARS_PER_DOCUMENT]}"
        for number, document in enumerate(documents, start=1)
    )


async def plan_next(subject: str, hits: list[SearchHit]) -> SearchPlan:
    prompt = (
        f"The learner asked to learn: {subject}\n\n"
        f"A general web search returned {len(hits)} results:\n\n{number_hits(hits)}"
    )
    response = await get_planner_agent().run(prompt)
    return response.value


async def analyse_documents(subject: str, documents: list[SourceDocument]) -> SubjectAnalysis:
    prompt = (
        f"The learner asked to learn: {subject}\n\n"
        f"{len(documents)} documents were retrieved and read for that name:\n\n"
        f"{number_documents(documents)}"
    )
    response = await get_analysis_agent().run(prompt)
    return response.value


def pick(hits: list[SearchHit], numbers: list[int], budget: int) -> list[SearchHit]:
    """Indexes into the list we supplied, never URLs, so a mistyped URL is unrepresentable.
    Out-of-range numbers are dropped rather than wrapped: a silent modulo would hand back a
    source the model never chose."""
    chosen: list[SearchHit] = []
    for number in numbers:
        if 1 <= number <= len(hits) and hits[number - 1] not in chosen:
            chosen.append(hits[number - 1])
        if len(chosen) >= budget:
            break
    return chosen


def as_documents(hits: list[SearchHit]) -> list[SourceDocument]:
    """Search results demoted to thin documents, used only when no page could be read."""
    return [
        SourceDocument(title=hit.title, url=hit.url, text=f"{hit.title}. {hit.snippet}".strip())
        for hit in hits
    ]


def unreadable(subject: str, trace: SubjectTrace) -> SubjectAnalysis:
    """The verdict when nothing could be read, decided in code rather than asked for.

    Snippets are strong enough to FIND a subject and far too weak to CONFIRM one, so a run
    with no fetched page must not reach the analyser at all: asking a model to judge snippets
    and then policing its answer is two chances to be wrong where none are needed.
    """
    trace.notes.append("no page could be read, so the identity was not put to the model")
    logger.info("subject-analysis: %s had no readable evidence", subject)
    return SubjectAnalysis(
        identity_status=IdentityStatus.INSUFFICIENT_EVIDENCE,
        subject_type=TechnicalSubjectType.OTHER,
        description=f"Nothing readable could be retrieved for {subject!r}.",
    )


async def run_search(
    query: str, domains: list[str] | None, trace: SubjectTrace
) -> list[SearchHit]:
    label = f"{query!r} domains={domains or 'any'}"
    if len(trace.searches) >= MAX_SEARCHES:
        trace.notes.append(f"search budget spent, refused {label}")
        return []
    if label in trace.searches:
        trace.notes.append(f"already searched {label}")
        return []
    trace.searches.append(label)
    return await search_web(query, domains)


async def investigate(subject: str) -> tuple[SubjectAnalysis, list[SourceDocument], SubjectTrace]:
    trace = SubjectTrace()

    hits = await run_search(subject, None, trace)
    if not hits:
        return unreadable(subject, trace), [], trace

    plan = await plan_next(subject, hits)
    for targeted in plan.targeted_searches:
        extra = await run_search(targeted.query, targeted.domains or None, trace)
        hits = dedupe(hits + extra)
    if plan.targeted_searches:
        plan = await plan_next(subject, hits)

    selected = pick(hits, plan.fetch, MAX_FETCHES)
    # Measured: for "Guitar" the planner selected nothing, the fallback read the top hit anyway,
    # and the run confirmed GUITAR — a GUI testing framework — as the subject. An empty
    # selection is the planner saying no result here settles it, and overriding that turns a
    # refusal into a confirmation of whatever happened to rank first.
    if not selected:
        trace.notes.append("the plan selected no source, so nothing here settles the identity")
        return unreadable(subject, trace), [], trace

    documents = await fetch_documents(selected, limit=MAX_FETCHES)
    trace.fetched_urls.extend(document.url for document in documents)
    if not documents:
        return unreadable(subject, trace), [], trace

    return await analyse_documents(subject, documents), documents, trace


class SubjectAnalysisExecutor(Executor):
    """Graph node for subject-analysis-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.request.skill  # guaranteed by the edge
        analysis, documents, trace = await investigate(state.request.skill)
        state.subject = analysis
        state.sources = documents
        state.subject_trace = trace
        logger.info(
            "subject-analysis: %s -> %s (%d searches, %d documents)",
            state.request.skill,
            analysis.identity_status,
            len(trace.searches),
            len(trace.fetched_urls),
        )
        state.mark(WorkflowStep.SUBJECT_ANALYSIS)
        await ctx.send_message(state)


def is_identified(state: CourseState) -> bool:
    """The invariant: nothing downstream may run on a subject we could not establish."""
    return state.subject is not None and state.subject.identity_status is IdentityStatus.CONFIRMED
