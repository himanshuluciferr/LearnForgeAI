"""project-agent — beginner, intermediate and advanced portfolio projects."""

from __future__ import annotations

import logging
from functools import lru_cache

from agent_framework import Agent, Executor, WorkflowContext, handler

from backend.prompts.loader import load_prompt
from backend.services.foundry import get_chat_client
from backend.workflow.state import (
    Curriculum,
    CourseState,
    ExperienceLevel,
    LearningRequest,
    Project,
    ProjectDraft,
    ProjectPlan,
    SkillAnalysis,
    WorkflowStep,
)

logger = logging.getLogger(__name__)

AGENT_NAME = "project-agent"

# The rungs of the ramp, in order. A draft's difficulty is its position, so this also fixes
# how many projects a course gets.
LEVELS = (
    ExperienceLevel.BEGINNER,
    ExperienceLevel.INTERMEDIATE,
    ExperienceLevel.ADVANCED,
)

# Enough to describe a real layout, not so many that the tree becomes the deliverable.
MAX_FILES = 40


@lru_cache
def get_project_agent() -> Agent:
    return get_chat_client().as_agent(
        name=AGENT_NAME,
        instructions=load_prompt("project"),
        default_options={"response_format": ProjectPlan},
    )


# Marks a path that ended in "/" so an empty folder is still drawn as one. No real path
# part can be empty, so the key cannot collide with a file name.
DIR_MARKER = ""


def is_path_part(part: str) -> bool:
    """A note is not a path. Models slip commentary such as '(place PDFs here)' into the
    file list, and it would otherwise be drawn as a file."""
    return bool(part.strip()) and not part.strip().startswith("(")


def build_tree(paths: list[str]) -> dict:
    tree: dict = {}
    for path in paths[:MAX_FILES]:
        parts = path.strip("/").split("/")
        kept = [part for part in parts if is_path_part(part)]
        node = tree
        for part in kept:
            node = node.setdefault(part, {})
        # A dropped note means its parent was the folder being described.
        if kept and (path.rstrip().endswith("/") or len(kept) < len(parts)):
            node[DIR_MARKER] = {}
    return tree


def children(node: dict) -> dict:
    return {name: child for name, child in node.items() if name != DIR_MARKER}


def is_folder(node: dict) -> bool:
    return DIR_MARKER in node or bool(children(node))


def render_tree(tree: dict, prefix: str = "") -> str:
    """Folders before files, then alphabetical, so the same paths always draw the same tree."""
    entries = sorted(children(tree).items(), key=lambda entry: (not is_folder(entry[1]), entry[0]))
    lines = []
    for index, (name, node) in enumerate(entries):
        last = index == len(entries) - 1
        slash = "/" if is_folder(node) else ""
        lines.append(f"{prefix}{'└── ' if last else '├── '}{name}{slash}")
        if children(node):
            lines.append(render_tree(node, prefix + ("    " if last else "│   ")))
    return "\n".join(lines)


def folder_structure(paths: list[str]) -> str:
    """Drawn here rather than asked for.

    A tree is box-drawing characters lined up by hand, which is exactly the kind of
    formatting a model gets subtly wrong; the paths behind it are not.
    """
    return render_tree(build_tree(paths))


def is_usable(draft: ProjectDraft) -> bool:
    return bool(draft.title.strip() and draft.features)


def assemble(level: ExperienceLevel, draft: ProjectDraft) -> Project:
    return Project(
        level=level,
        title=draft.title,
        summary=draft.summary,
        features=draft.features,
        folder_structure=folder_structure(draft.files),
        milestones=draft.milestones,
        stretch_goals=draft.stretch_goals,
    )


def assemble_all(plan: ProjectPlan) -> list[Project]:
    usable = [draft for draft in plan.projects if is_usable(draft)]

    # Two projects is a thinner portfolio; none is a course you cannot show anyone.
    if not usable:
        raise ValueError("project-agent returned no usable projects")

    return [assemble(level, draft) for level, draft in zip(LEVELS, usable)]


def ambition_floor(request: LearningRequest) -> str:
    """State the one branch that applies rather than a rule about adapting.

    Left to a general instruction the first project comes back as a tutorial exercise
    regardless of who the learner is.
    """
    if request.assumed_level is ExperienceLevel.BEGINNER:
        return (
            "The learner is new to this skill, so the first project may start from nothing "
            "— but it must still do something real, not print a greeting."
        )
    return (
        f"The learner already works with {request.skill}. Even the first project must be "
        "past tutorial level: assume the basics and open on something they cannot already do."
    )


def format_chapters(curriculum: Curriculum) -> str:
    return "\n".join(
        f"- Ch {outline.number} {outline.title}: {'; '.join(outline.objectives)}"
        for outline in curriculum.chapters
    )


def format_career_paths(analysis: SkillAnalysis) -> str:
    if not analysis.career_paths:
        return "No specific roles were identified, so aim at general professional credibility."
    roles = ", ".join(analysis.career_paths)
    return f"These projects should read well to someone hiring for: {roles}."


def build_prompt(
    request: LearningRequest, analysis: SkillAnalysis, curriculum: Curriculum
) -> str:
    return (
        f"Skill: {request.skill}\n"
        f"Learner's goal: {request.goal or 'not stated'}\n"
        f"Course language: {request.language}\n"
        f"Produce exactly {len(LEVELS)} projects.\n\n"
        f"{ambition_floor(request)}\n"
        f"{format_career_paths(analysis)}\n\n"
        f"Course: {curriculum.title}\n"
        f"{curriculum.summary}\n\n"
        f"What the course teaches, and therefore what the projects may use:\n"
        f"{format_chapters(curriculum)}"
    )


async def design_projects(
    request: LearningRequest, analysis: SkillAnalysis, curriculum: Curriculum
) -> list[Project]:
    """One call, not one per project.

    Chapters and quizzes fan out because each is independent. A ramp is a single design
    decision, and three separate calls would each reach for the most obvious idea.
    """
    logger.info("project-agent: designing %d projects", len(LEVELS))
    response = await get_project_agent().run(build_prompt(request, analysis, curriculum))
    return assemble_all(response.value)


class ProjectExecutor(Executor):
    """Graph node for project-agent."""

    @handler
    async def run(self, state: CourseState, ctx: WorkflowContext[CourseState]) -> None:
        assert state.request is not None and state.curriculum is not None
        assert state.skill_analysis is not None
        state.projects = await design_projects(
            state.request, state.skill_analysis, state.curriculum
        )
        state.mark(WorkflowStep.PROJECT)
        await ctx.send_message(state)
