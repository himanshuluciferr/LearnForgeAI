"""Shared workflow state read and written by every executor.

This is the contract that constrains all agents: each one consumes earlier fields
and fills in its own. Changing a model here changes the prompts downstream.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Sequence

from pydantic import BaseModel, Field

PASSING_REVIEW_SCORE = 90

# Caps the review -> chapter regeneration loop so a persistently low score cannot spin forever.
MAX_REVISIONS = 2


class WorkflowStep(StrEnum):
    REQUIREMENT = "requirement"
    SKILL_ANALYSIS = "skill-analysis"
    RESEARCH = "research"
    CURRICULUM = "curriculum"
    CHAPTER = "chapter"
    PRACTICE = "practice"
    PROJECT = "project"
    QUIZ = "quiz"
    INTERVIEW = "interview"
    REVIEW = "review"
    PUBLISHER = "publisher"


STEP_ORDER: tuple[WorkflowStep, ...] = tuple(WorkflowStep)

# Rough share of total runtime, used to report progress. Must sum to 100.
STEP_WEIGHTS: dict[WorkflowStep, int] = {
    WorkflowStep.REQUIREMENT: 5,
    WorkflowStep.SKILL_ANALYSIS: 5,
    WorkflowStep.RESEARCH: 10,
    WorkflowStep.CURRICULUM: 10,
    WorkflowStep.CHAPTER: 30,
    WorkflowStep.PRACTICE: 8,
    WorkflowStep.PROJECT: 10,
    WorkflowStep.QUIZ: 8,
    WorkflowStep.INTERVIEW: 6,
    WorkflowStep.REVIEW: 5,
    WorkflowStep.PUBLISHER: 3,
}


def progress_percent(completed: Sequence[WorkflowStep]) -> int:
    return min(100, sum(STEP_WEIGHTS[step] for step in completed))


class ExperienceLevel(StrEnum):
    BEGINNER = "beginner"
    INTERMEDIATE = "intermediate"
    ADVANCED = "advanced"


class LearningRequest(BaseModel):
    """Output of requirement-agent — the raw Teams prompt turned into structure."""

    # Descriptions here become the JSON schema the model sees, so they steer extraction
    # more reliably than prompt text does.
    is_learning_request: bool = Field(
        description="False if the prompt is not a request to learn a skill. Other fields are then ignored."
    )
    skill: str = Field(default="", description="One skill or technology to learn, e.g. 'Azure AI Search'.")
    experience: ExperienceLevel = Field(
        default=ExperienceLevel.BEGINNER,
        description="The learner's current level with this skill. Assume beginner unless stated.",
    )
    goal: str = Field(default="", description="What the learner wants to be able to do, in one sentence.")
    daily_minutes: int = Field(
        default=30, ge=5, le=480, description="Minutes per day the learner can commit."
    )
    language: str = Field(
        default="en", description="ISO 639-1 code for the course language, e.g. 'en', 'hi'. Not the language name."
    )


class SkillAnalysis(BaseModel):
    category: str
    difficulty: ExperienceLevel
    prerequisites: list[str] = Field(default_factory=list)
    estimated_hours: int
    career_paths: list[str] = Field(default_factory=list)


class ResourceKind(StrEnum):
    DOCS = "docs"
    MICROSOFT_LEARN = "microsoft-learn"
    GITHUB = "github"
    VIDEO = "video"
    BLOG = "blog"


class ResearchSource(BaseModel):
    title: str
    url: str
    kind: ResourceKind
    summary: str
    rank_score: float = 0.0


class ChapterOutline(BaseModel):
    number: int
    title: str
    objectives: list[str] = Field(default_factory=list)


class Curriculum(BaseModel):
    title: str
    summary: str
    chapters: list[ChapterOutline]


class Chapter(BaseModel):
    number: int
    title: str
    body_markdown: str
    key_points: list[str] = Field(default_factory=list)
    exercises: list[str] = Field(default_factory=list)


class PracticeItem(BaseModel):
    chapter_number: int
    kind: str
    prompt: str
    solution: str | None = None


class Project(BaseModel):
    level: ExperienceLevel
    title: str
    features: list[str] = Field(default_factory=list)
    folder_structure: str = ""
    milestones: list[str] = Field(default_factory=list)
    stretch_goals: list[str] = Field(default_factory=list)


class QuizQuestion(BaseModel):
    question: str
    options: list[str]
    correct_index: int
    explanation: str = ""


class Quiz(BaseModel):
    scope: str
    questions: list[QuizQuestion]


class InterviewQuestion(BaseModel):
    level: ExperienceLevel
    question: str
    model_answer: str


class ReviewResult(BaseModel):
    score: int
    issues: list[str] = Field(default_factory=list)
    regenerate_chapters: list[int] = Field(default_factory=list)


class PublishedCourse(BaseModel):
    markdown_url: str
    pdf_url: str | None = None
    docx_url: str | None = None


class Rejection(BaseModel):
    """Terminal result when the prompt was not a request to learn something."""

    message: str


class CourseState(BaseModel):
    """Passed between executors for the lifetime of one generation job."""

    job_id: str
    user_id: str
    prompt: str

    request: LearningRequest | None = None
    skill_analysis: SkillAnalysis | None = None
    research: list[ResearchSource] = Field(default_factory=list)
    curriculum: Curriculum | None = None
    chapters: list[Chapter] = Field(default_factory=list)
    practice: list[PracticeItem] = Field(default_factory=list)
    projects: list[Project] = Field(default_factory=list)
    quizzes: list[Quiz] = Field(default_factory=list)
    interview: list[InterviewQuestion] = Field(default_factory=list)
    review: ReviewResult | None = None
    published: PublishedCourse | None = None

    completed_steps: list[WorkflowStep] = Field(default_factory=list)
    revision_count: int = 0

    def mark(self, step: WorkflowStep) -> None:
        """Record a finished step. The review loop revisits steps, so entries stay unique."""
        if step not in self.completed_steps:
            self.completed_steps.append(step)

    @property
    def should_regenerate(self) -> bool:
        return (
            self.review is not None
            and self.review.score < PASSING_REVIEW_SCORE
            and self.revision_count < MAX_REVISIONS
        )

    @property
    def percent(self) -> int:
        return progress_percent(self.completed_steps)
