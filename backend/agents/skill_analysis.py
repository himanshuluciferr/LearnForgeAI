"""skill-analysis-agent — difficulty, prerequisites, duration, career paths."""

from __future__ import annotations

from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import CourseState, LearningRequest, SkillAnalysis, WorkflowStep

AGENT_NAME = "skill-analysis-agent"


@lru_cache
def get_skill_analysis_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("skill_analysis"),
        default_options={"response_format": SkillAnalysis},
    )


def build_prompt(request: LearningRequest) -> str:
    """This agent reads requirement-agent's output, never the raw Teams message."""
    return (
        f"Skill: {request.skill}\n"
        f"Learner's current level: {request.experience}\n"
        f"Goal: {request.goal or 'not stated'}\n"
        f"Time available: {request.daily_minutes} minutes per day"
    )


async def analyse_skill(request: LearningRequest) -> SkillAnalysis:
    response = await get_skill_analysis_agent().run(build_prompt(request))
    return response.value


class SkillAnalysisExecutor(Executor):
    """Graph node for skill-analysis-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None  # guaranteed by the edge condition
        state.skill_analysis = await analyse_skill(state.request)
        state.mark(WorkflowStep.SKILL_ANALYSIS)
        await ctx.send_message(state)

