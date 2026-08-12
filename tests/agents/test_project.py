"""Offline tests for project-agent: the ramp, the drawn tree, grounding, wiring."""

import pytest

from backend.agents import project as project_module
from backend.agents.project import (
    LEVELS,
    MAX_FILES,
    ProjectExecutor,
    ambition_floor,
    assemble_all,
    audience,
    build_prompt,
    design_projects,
    folder_structure,
    is_usable,
)
from backend.workflow.state import (
    ChapterOutline,
    CourseState,
    Curriculum,
    ExperienceLevel,
    IdentityStatus,
    LearningRequest,
    ProjectDraft,
    ProjectPlan,
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


class StubResponse:
    def __init__(self, value: ProjectPlan) -> None:
        self.value = value


class StubAgent:
    def __init__(self, plan: ProjectPlan | None = None) -> None:
        self.plan = plan if plan is not None else make_plan()
        self.prompts: list[str] = []

    async def run(self, prompt: str) -> StubResponse:
        self.prompts.append(prompt)
        return StubResponse(self.plan)


def make_draft(n: int = 1, files: list[str] | None = None, features: int = 3) -> ProjectDraft:
    return ProjectDraft(
        title=f"Project {n}",
        summary=f"summary {n}",
        features=[f"feature {i}" for i in range(1, features + 1)],
        files=files if files is not None else ["src/main.py", "README.md"],
        milestones=[f"milestone {i}" for i in range(1, 4)],
        stretch_goals=[f"stretch {i}" for i in range(1, 3)],
    )


def make_plan(count: int = 3) -> ProjectPlan:
    return ProjectPlan(projects=[make_draft(n) for n in range(1, count + 1)])


def make_request(**overrides) -> LearningRequest:
    return LearningRequest(
        **{
            "is_learning_request": True,
            "skill": "Azure AI Search",
            "experience": ExperienceLevel.BEGINNER,
            "goal": "build a search feature",
            "daily_minutes": 30,
            **overrides,
        }
    )


def make_subject(name: str = "Azure AI Search") -> SubjectAnalysis:
    return SubjectAnalysis(
        identity_status=IdentityStatus.CONFIRMED,
        canonical_name=name,
        subject_type=TechnicalSubjectType.SERVICE,
        description="A managed search service.",
    )


def make_curriculum(count: int = 3) -> Curriculum:
    return Curriculum(
        title="Azure AI Search end to end",
        summary="Indexing through to ranking.",
        chapters=[
            ChapterOutline(
                number=n, title=f"Topic {n}", objectives=[f"do thing {n}a", f"do thing {n}b"]
            )
            for n in range(1, count + 1)
        ],
    )


def use_stub(monkeypatch, agent: StubAgent) -> StubAgent:
    monkeypatch.setattr(project_module, "get_project_agent", lambda: agent)
    return agent


# --- the difficulty is position, never asked for --------------------------------------


def test_the_model_is_not_asked_which_level_a_project_is():
    assert "level" not in ProjectDraft.model_fields


def test_levels_are_stamped_on_in_order():
    projects = assemble_all(make_plan())

    assert [project.level for project in projects] == list(LEVELS)


def test_a_short_plan_still_starts_at_the_beginning_of_the_ramp():
    projects = assemble_all(make_plan(count=2))

    assert [project.level for project in projects] == [
        ExperienceLevel.BEGINNER,
        ExperienceLevel.INTERMEDIATE,
    ]


def test_extra_projects_are_dropped_rather_than_left_unlevelled():
    projects = assemble_all(make_plan(count=5))

    assert len(projects) == len(LEVELS)


# --- degraded versus broken -----------------------------------------------------------


def test_a_project_without_features_is_not_usable():
    assert not is_usable(make_draft(features=0))
    assert is_usable(make_draft())


def test_a_plan_with_nothing_usable_raises():
    plan = ProjectPlan(projects=[make_draft(1, features=0)])

    with pytest.raises(ValueError, match="no usable projects"):
        assemble_all(plan)


def test_one_unusable_project_leaves_the_others_standing():
    plan = ProjectPlan(projects=[make_draft(1, features=0), make_draft(2), make_draft(3)])

    projects = assemble_all(plan)

    assert [project.title for project in projects] == ["Project 2", "Project 3"]


# --- the tree is drawn, not requested -------------------------------------------------


def test_the_model_is_asked_for_paths_not_a_tree():
    assert "files" in ProjectDraft.model_fields
    assert "folder_structure" not in ProjectDraft.model_fields


def test_paths_become_a_tree():
    tree = folder_structure(["src/cli.py", "src/util/io.py", "README.md"])

    assert tree == "\n".join(
        [
            "├── src/",
            "│   ├── util/",
            "│   │   └── io.py",
            "│   └── cli.py",
            "└── README.md",
        ]
    )


def test_the_same_paths_always_draw_the_same_tree():
    paths = ["b/second.py", "a/first.py", "README.md"]

    assert folder_structure(paths) == folder_structure(list(reversed(paths)))


def test_a_trailing_slash_does_not_create_a_blank_entry():
    tree = folder_structure(["logs/", "main.py"])

    assert tree == "├── logs/\n└── main.py"


def test_a_note_in_the_file_list_is_not_drawn_as_a_file():
    """Seen live: the model wrote `data/pdfs/ (place PDF files here)`, which rendered as a
    file called "(place PDF files here)". The note goes, the folder it described stays."""
    tree = folder_structure(["data/pdfs/ (place PDF files here)", "main.py"])

    assert tree == "├── data/\n│   └── pdfs/\n└── main.py"


def test_the_file_list_cannot_grow_without_limit():
    tree = folder_structure([f"file{n}.py" for n in range(MAX_FILES * 3)])

    assert len(tree.splitlines()) == MAX_FILES


def test_no_files_means_an_empty_tree_not_a_crash():
    assert folder_structure([]) == ""


# --- grounding ------------------------------------------------------------------------


def test_the_prompt_lists_what_the_course_actually_teaches():
    prompt = build_prompt(make_request(), make_subject(), make_curriculum())

    assert "Ch 1 Topic 1: do thing 1a; do thing 1b" in prompt
    assert "Ch 3 Topic 3" in prompt


def test_the_projects_are_aimed_at_the_subject_that_was_identified():
    """This replaced `career_paths`, which asked a model to invent job titles from memory."""
    prompt = build_prompt(make_request(), make_subject("Apache Spark"), make_curriculum())

    assert "hiring for work with Apache Spark" in prompt


def test_an_unnamed_subject_still_gives_the_model_a_direction():
    assert "this subject" in audience(make_subject(name=None))


def test_an_experienced_learner_is_told_to_skip_tutorial_level():
    floor = ambition_floor(make_request(experience=ExperienceLevel.ADVANCED))

    assert "already works with Azure AI Search" in floor
    assert "past tutorial level" in floor


def test_a_beginner_is_allowed_to_start_from_nothing():
    assert "start from nothing" in ambition_floor(make_request())


def test_the_prompt_states_the_exact_project_count():
    prompt = build_prompt(make_request(), make_subject(), make_curriculum())

    assert f"Produce exactly {len(LEVELS)} projects." in prompt


# --- the step -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_ramp_comes_from_one_call_not_one_per_project(monkeypatch):
    """Separate calls would each reach for the most obvious idea and repeat each other."""
    agent = use_stub(monkeypatch, StubAgent())

    await design_projects(make_request(), make_subject(), make_curriculum())

    assert len(agent.prompts) == 1


@pytest.mark.asyncio
async def test_the_executor_marks_the_step_and_forwards_state(monkeypatch):
    use_stub(monkeypatch, StubAgent())
    state = CourseState(job_id="j", user_id="u", prompt="p", request=make_request())
    state.subject = make_subject()
    state.curriculum = make_curriculum()
    ctx = CapturingContext()

    await ProjectExecutor(id=WorkflowStep.PROJECT).run(state, ctx)

    assert WorkflowStep.PROJECT in state.completed_steps
    assert len(state.projects) == len(LEVELS)
    assert ctx.messages == [state]


def test_progress_reaches_eighty_six_percent_once_projects_are_designed():
    done = [
        WorkflowStep.REQUIREMENT,
        WorkflowStep.SUBJECT_ANALYSIS,
        WorkflowStep.RESEARCH,
        WorkflowStep.CURRICULUM,
        WorkflowStep.CHAPTER,
        WorkflowStep.PRACTICE,
        WorkflowStep.PROJECT,
        WorkflowStep.QUIZ,
    ]

    assert progress_percent(done) == 86
