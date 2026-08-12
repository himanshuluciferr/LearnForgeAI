"""Builds the course-generation workflow with WorkflowBuilder.

Agents and deterministic functions are registered as executors and connected by
edges: requirement -> skill-analysis -> research -> curriculum -> chapter -> review
-> practice -> project -> quiz -> publisher.

review sits directly after chapter and holds the only backward edge, so a rewrite loop
costs chapter calls alone.
"""

from __future__ import annotations

from agent_framework import Case, Default, Workflow, WorkflowBuilder

from backend.agents.chapter import ChapterExecutor
from backend.agents.curriculum import CurriculumExecutor
from backend.agents.practice import PracticeExecutor
from backend.agents.project import ProjectExecutor
from backend.agents.quiz import QuizExecutor
from backend.agents.requirement import RequirementExecutor
from backend.agents.research import ResearchExecutor
from backend.agents.review import ReviewExecutor
from backend.agents.skill_analysis import SkillAnalysisExecutor
from backend.agents.subject_analysis import SubjectAnalysisExecutor, is_identified
from backend.workflow.executors import (
    CLARIFY_ID,
    CONFIRM_SUBJECT_ID,
    REJECTED_ID,
    SUBJECT_CLARIFY_ID,
    ClarifyExecutor,
    ConfirmSubjectExecutor,
    PublisherExecutor,
    RejectedExecutor,
    SubjectClarifyExecutor,
)
from backend.workflow.state import CourseState, WorkflowStep


def _is_not_learning_request(state: CourseState) -> bool:
    return state.request is not None and not state.request.is_learning_request


def _needs_clarification(state: CourseState) -> bool:
    """One signal for every reason node 1 cannot hand a subject downstream.

    `missing_requirements` is what the agent itself reports; the other two are the same
    conclusion read off the data, so a model that fills the fields but forgets the flag
    still stops here instead of building a course on nothing.
    """
    request = state.request
    if request is None or not request.is_learning_request:
        return False
    return bool(request.missing_requirements) or len(request.alternatives) > 1 or not request.skill


def _subject_not_identified(state: CourseState) -> bool:
    """The invariant, as an edge: nothing downstream runs on a subject we could not establish."""
    return state.subject is not None and not is_identified(state)


def _needs_confirmation(state: CourseState) -> bool:
    return is_identified(state) and not state.subject_confirmed


def _needs_revision(state: CourseState) -> bool:
    return state.should_regenerate


def _course_nodes() -> dict[str, object]:
    """Every executor, built once so both entry points wire the identical tail."""
    return {
        "requirement": RequirementExecutor(id=WorkflowStep.REQUIREMENT),
        "subject": SubjectAnalysisExecutor(id=WorkflowStep.SUBJECT_ANALYSIS),
        "skill_analysis": SkillAnalysisExecutor(id=WorkflowStep.SKILL_ANALYSIS),
        "research": ResearchExecutor(id=WorkflowStep.RESEARCH),
        "curriculum": CurriculumExecutor(id=WorkflowStep.CURRICULUM),
        "chapter": ChapterExecutor(id=WorkflowStep.CHAPTER),
        "review": ReviewExecutor(id=WorkflowStep.REVIEW),
        "practice": PracticeExecutor(id=WorkflowStep.PRACTICE),
        "project": ProjectExecutor(id=WorkflowStep.PROJECT),
        "quiz": QuizExecutor(id=WorkflowStep.QUIZ),
        "publisher": PublisherExecutor(id=WorkflowStep.PUBLISHER),
    }


def _add_course_tail(builder: WorkflowBuilder, nodes: dict) -> WorkflowBuilder:
    return (
        builder.add_edge(nodes["skill_analysis"], nodes["research"])
        .add_edge(nodes["research"], nodes["curriculum"])
        .add_edge(nodes["curriculum"], nodes["chapter"])
        .add_edge(nodes["chapter"], nodes["review"])
        # Switch-case, not two conditional edges: it picks one target per message in a single
        # pass. Sibling conditions are evaluated one at a time, and the rewrite mutates the
        # very state they read, so two "opposite" conditions can both fire and duplicate the
        # whole tail of the course.
        .add_switch_case_edge_group(
            nodes["review"],
            [
                Case(condition=_needs_revision, target=nodes["chapter"]),
                Default(target=nodes["practice"]),
            ],
        )
        .add_edge(nodes["practice"], nodes["project"])
        .add_edge(nodes["project"], nodes["quiz"])
        .add_edge(nodes["quiz"], nodes["publisher"])
    )


def build_workflow() -> Workflow:
    """The first run: parse the request, establish the subject, then stop for confirmation."""
    nodes = _course_nodes()
    rejected = RejectedExecutor(id=REJECTED_ID)
    clarify = ClarifyExecutor(id=CLARIFY_ID)
    subject_clarify = SubjectClarifyExecutor(id=SUBJECT_CLARIFY_ID)
    confirm = ConfirmSubjectExecutor(id=CONFIRM_SUBJECT_ID)
    builder = (
        WorkflowBuilder(start_executor=nodes["requirement"])
        # Switch-case rather than three sibling conditions: it evaluates once and returns one
        # target, so the branches cannot overlap or leave a message with nowhere to go.
        .add_switch_case_edge_group(
            nodes["requirement"],
            [
                Case(condition=_is_not_learning_request, target=rejected),
                Case(condition=_needs_clarification, target=clarify),
                Default(target=nodes["subject"]),
            ],
        )
        .add_switch_case_edge_group(
            nodes["subject"],
            [
                Case(condition=_subject_not_identified, target=subject_clarify),
                Case(condition=_needs_confirmation, target=confirm),
                Default(target=nodes["skill_analysis"]),
            ],
        )
    )
    return _add_course_tail(builder, nodes).build()


def build_confirmed_workflow() -> Workflow:
    """The second run, after the learner approved the subject.

    A separate entry point rather than a resumed one: a suspended MAF workflow lives in memory
    and our jobs run in background tasks with no checkpoint storage, so it would not survive
    the wait. The analysis and its sources are replayed from the job instead, which also means
    the search and the two model calls are not paid for twice.
    """
    nodes = _course_nodes()
    builder = WorkflowBuilder(start_executor=nodes["skill_analysis"])
    return _add_course_tail(builder, nodes).build()
