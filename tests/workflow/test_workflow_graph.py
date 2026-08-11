"""Tests for the workflow graph itself.

The agents are tested elsewhere. What is untested until here is the wiring: that every
step hands to the next one, and that the single backward edge exists and is gated.
"""

from __future__ import annotations

from agent_framework import SwitchCaseEdgeGroup

from backend.workflow.executors import CLARIFY_ID, REJECTED_ID
from backend.workflow.state import WorkflowStep
from backend.workflow.workflow import build_workflow


def edges() -> set[tuple[str, str, str | None]]:
    """Every declared edge as (source, target, condition name).

    Internal edges are dropped: the framework adds one per executor to feed it, and they
    say nothing about our pipeline.
    """
    return {
        (str(edge.source_id), str(edge.target_id), edge.condition_name)
        for group in build_workflow().edge_groups
        for edge in group.edges
        if not str(edge.source_id).startswith("internal:")
    }


def cases_out_of(source: str) -> list[tuple[str | None, str]]:
    """One node's switch-case branches in evaluation order, as (condition name, target).

    Scoped by source: more than one node routes this way, and merging their groups would
    let a change to either quietly satisfy the other's test.
    """
    return [
        (getattr(case, "condition_name", None), case.target_id)
        for group in build_workflow().edge_groups
        if isinstance(group, SwitchCaseEdgeGroup) and source in group.source_executor_ids
        for case in group.cases
    ]


def review_cases() -> list[tuple[str | None, str]]:
    return cases_out_of(str(WorkflowStep.REVIEW))


def test_every_step_hands_to_the_next_one():
    """review is absent from this chain because it routes through a switch-case group."""
    chain = [
        WorkflowStep.SKILL_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
        WorkflowStep.CHAPTER,
        WorkflowStep.REVIEW,
        # review -> practice is a switch-case branch, so the run of plain edges restarts here.
        WorkflowStep.PRACTICE,
        WorkflowStep.PROJECT,
        WorkflowStep.QUIZ,
    ]
    routed = {(WorkflowStep.REVIEW, WorkflowStep.PRACTICE)}
    declared = edges()

    for source, target in zip(chain, chain[1:]):
        if (source, target) in routed:
            continue
        assert (str(source), str(target), None) in declared


def test_review_picks_exactly_one_branch_per_message():
    """Two sibling conditional edges are evaluated one at a time, and the rewrite mutates
    the state they read — so both fired and the whole tail of the course ran twice."""
    assert review_cases() == [
        ("_needs_revision", str(WorkflowStep.CHAPTER)),
        (None, str(WorkflowStep.PRACTICE)),
    ]


def test_a_reviewed_course_falls_through_to_its_exercises():
    """The pass branch is the default, so it cannot drift out of step with the fail branch."""
    assert review_cases()[-1] == (None, str(WorkflowStep.PRACTICE))


def test_practice_project_and_quiz_sit_outside_the_rewrite_loop():
    """They read finished chapters, so looping them would re-pay for work that was fine."""
    downstream = {
        str(WorkflowStep.PRACTICE),
        str(WorkflowStep.PROJECT),
        str(WorkflowStep.QUIZ),
    }
    loop = {str(WorkflowStep.CHAPTER), str(WorkflowStep.REVIEW)}

    assert not [
        (source, target)
        for source, target, _ in edges()
        if source in downstream and target in loop
    ]


def test_a_rejected_prompt_leaves_the_pipeline_immediately():
    assert cases_out_of(str(WorkflowStep.REQUIREMENT))[0] == (
        "_is_not_learning_request",
        REJECTED_ID,
    )


def test_a_request_without_one_named_skill_stops_before_anything_is_generated():
    """Picking a subject silently is the failure this branch exists to prevent."""
    assert ("_needs_clarification", CLARIFY_ID) in cases_out_of(str(WorkflowStep.REQUIREMENT))


def test_a_clear_request_is_the_default_route():
    """Default rather than a third condition, so no prompt can fall through to no branch."""
    assert cases_out_of(str(WorkflowStep.REQUIREMENT))[-1] == (
        None,
        str(WorkflowStep.SKILL_ANALYSIS),
    )


def test_neither_early_exit_can_reach_the_rest_of_the_pipeline():
    """Both are terminal: a course must never be built after we said we could not build one."""
    assert not [
        (source, target) for source, target, _ in edges() if source in {REJECTED_ID, CLARIFY_ID}
    ]


def test_review_can_send_the_course_back_to_be_rewritten():
    assert (str(WorkflowStep.REVIEW), str(WorkflowStep.CHAPTER), None) in edges()


def test_the_loop_back_to_chapter_is_the_only_backward_edge():
    """A second backward edge would make the run order unpredictable."""
    positions = {step: index for index, step in enumerate(WorkflowStep)}
    backward = {
        (source, target)
        for source, target, _ in edges()
        if source in positions and target in positions and positions[target] <= positions[source]
    }

    assert backward == {(WorkflowStep.REVIEW, WorkflowStep.CHAPTER)}
