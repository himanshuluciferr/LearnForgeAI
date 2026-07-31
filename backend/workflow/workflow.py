"""Builds the course-generation workflow with WorkflowBuilder.

Agents and deterministic functions are registered as executors and connected by
edges: requirement -> skill-analysis -> research -> curriculum -> chapter
-> practice -> project -> quiz -> interview -> review -> publisher.

review uses a conditional edge, looping back when the quality score is too low.
"""

from __future__ import annotations

from agent_framework import Workflow, WorkflowBuilder

from backend.agents.requirement import RequirementExecutor
from backend.workflow.executors import REJECTED_ID, RejectedExecutor
from backend.workflow.state import CourseState, WorkflowStep


def _is_not_learning_request(state: CourseState) -> bool:
    return state.request is not None and not state.request.is_learning_request


def build_workflow() -> Workflow:
    # Executor ids are WorkflowStep values so progress events map straight to steps.
    requirement = RequirementExecutor(id=WorkflowStep.REQUIREMENT)
    rejected = RejectedExecutor(id=REJECTED_ID)
    return (
        WorkflowBuilder(start_executor=requirement)
        .add_edge(requirement, rejected, condition=_is_not_learning_request)
        .build()
    )
