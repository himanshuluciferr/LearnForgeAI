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
from backend.workflow.executors import (
    CLARIFY_ID,
    REJECTED_ID,
    ClarifyExecutor,
    PublisherExecutor,
    RejectedExecutor,
)
from backend.workflow.state import CourseState, WorkflowStep


def _is_not_learning_request(state: CourseState) -> bool:
    return state.request is not None and not state.request.is_learning_request


def _named_several_skills(state: CourseState) -> bool:
    return state.request is not None and len(state.request.alternatives) > 1


def _needs_revision(state: CourseState) -> bool:
    return state.should_regenerate


def build_workflow() -> Workflow:
    # Executor ids are WorkflowStep values so progress events map straight to steps.
    requirement = RequirementExecutor(id=WorkflowStep.REQUIREMENT)
    skill_analysis = SkillAnalysisExecutor(id=WorkflowStep.SKILL_ANALYSIS)
    research = ResearchExecutor(id=WorkflowStep.RESEARCH)
    curriculum = CurriculumExecutor(id=WorkflowStep.CURRICULUM)
    chapter = ChapterExecutor(id=WorkflowStep.CHAPTER)
    practice = PracticeExecutor(id=WorkflowStep.PRACTICE)
    project = ProjectExecutor(id=WorkflowStep.PROJECT)
    quiz = QuizExecutor(id=WorkflowStep.QUIZ)
    review = ReviewExecutor(id=WorkflowStep.REVIEW)
    publisher = PublisherExecutor(id=WorkflowStep.PUBLISHER)
    rejected = RejectedExecutor(id=REJECTED_ID)
    clarify = ClarifyExecutor(id=CLARIFY_ID)
    return (
        WorkflowBuilder(start_executor=requirement)
        # Switch-case rather than three sibling conditions: it evaluates once and returns one
        # target, so the branches cannot overlap or leave a message with nowhere to go.
        .add_switch_case_edge_group(
            requirement,
            [
                Case(condition=_is_not_learning_request, target=rejected),
                Case(condition=_named_several_skills, target=clarify),
                Default(target=skill_analysis),
            ],
        )
        .add_edge(skill_analysis, research)
        .add_edge(research, curriculum)
        .add_edge(curriculum, chapter)
        .add_edge(chapter, review)
        # Switch-case, not two conditional edges: it picks one target per message in a single
        # pass. Sibling conditions are evaluated one at a time, and the rewrite mutates the
        # very state they read, so two "opposite" conditions can both fire and duplicate the
        # whole tail of the course.
        .add_switch_case_edge_group(
            review,
            [
                Case(condition=_needs_revision, target=chapter),
                Default(target=practice),
            ],
        )
        .add_edge(practice, project)
        .add_edge(project, quiz)
        .add_edge(quiz, publisher)
        .build()
    )
