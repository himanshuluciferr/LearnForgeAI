"""Shared workflow state read and written by every executor.

This is the contract that constrains all agents: each one consumes earlier fields
and fills in its own. Changing a model here changes the prompts downstream.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Sequence

from pydantic import BaseModel, Field

# Placed below the reviewer's own noise, not at the top of the range. Reviewing one
# unchanged chapter three times returned 82, 85 and 92, so a bar of 90 would have sent
# good work back on a coin flip. Real chapters measured 82-95, a hollow one 10-15, so
# anything in between separates them and 75 leaves room for a bad sample of good work.
PASSING_REVIEW_SCORE = 75

# Caps the review -> chapter regeneration loop so a persistently low score cannot spin forever.
MAX_REVISIONS = 2


class WorkflowStep(StrEnum):
    REQUIREMENT = "requirement"
    SKILL_ANALYSIS = "skill-analysis"
    RESEARCH = "research"
    CURRICULUM = "curriculum"
    CHAPTER = "chapter"
    # Sits here, not at the end, so a rewrite loop does not drag practice, project and quiz
    # round with it. They read finished chapters and only ever need to run once.
    REVIEW = "review"
    PRACTICE = "practice"
    PROJECT = "project"
    QUIZ = "quiz"
    PUBLISHER = "publisher"


STEP_ORDER: tuple[WorkflowStep, ...] = tuple(WorkflowStep)

# Rough share of total runtime, used to report progress. Must sum to 100.
STEP_WEIGHTS: dict[WorkflowStep, int] = {
    WorkflowStep.REQUIREMENT: 5,
    WorkflowStep.SKILL_ANALYSIS: 5,
    WorkflowStep.RESEARCH: 10,
    WorkflowStep.CURRICULUM: 10,
    WorkflowStep.CHAPTER: 30,
    # One call per chapter plus a whole-syllabus pass, so it costs more than the 5 it started with.
    WorkflowStep.REVIEW: 11,
    WorkflowStep.PRACTICE: 8,
    WorkflowStep.PROJECT: 10,
    WorkflowStep.QUIZ: 8,
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
    alternatives: list[str] = Field(
        default_factory=list,
        description=(
            "Every skill the learner offered as an alternative to the others without choosing, "
            "as in 'React or Vue'. Include the one you put in `skill`. Leave this empty when "
            "they named a single skill, or several that belong in one course such as "
            "'React with TypeScript'."
        ),
    )


class SkillAnalysis(BaseModel):
    """Output of skill-analysis-agent — how big the topic is and where it leads."""

    category: str = Field(
        description="Broad field this skill belongs to, e.g. 'Cloud', 'Data Engineering'."
    )
    difficulty: ExperienceLevel = Field(
        description="How hard the skill is in itself, independent of this learner's level."
    )
    prerequisites: list[str] = Field(
        default_factory=list,
        description=(
            "Skills that genuinely block starting. Empty for self-contained beginner topics. "
            "Never list the skill itself, general computer literacy, or ordinary tools "
            "such as a text editor, a browser or a git host."
        ),
    )
    estimated_hours: int = Field(
        ge=1,
        le=500,
        description="Hours to reach the learner's stated goal, not to master the whole field.",
    )
    career_paths: list[str] = Field(
        default_factory=list,
        description="Real job titles this skill contributes to, e.g. 'Cloud Solution Architect'.",
    )


class ResourceKind(StrEnum):
    DOCS = "docs"
    MICROSOFT_LEARN = "microsoft-learn"
    GITHUB = "github"
    VIDEO = "video"
    BLOG = "blog"


class ResearchSource(BaseModel):
    """One reference the chapter writer is allowed to lean on."""

    title: str = Field(description="Title of the page, as it appears on the page itself.")
    url: str = Field(
        description=(
            "Full https URL you are confident exists. Prefer a stable landing or section "
            "page over a deep versioned link, which rots. Never guess a path."
        )
    )
    kind: ResourceKind = Field(description="What sort of resource this is.")
    summary: str = Field(
        description="One or two sentences: what this source covers and when it helps."
    )
    rank_score: float = Field(
        default=0.0, description="Ignored on input — the ranking step overwrites it."
    )


class ResearchBundle(BaseModel):
    """Response schema for research-agent. Structured output needs an object at the root."""

    sources: list[ResearchSource] = Field(
        description="Sources covering the skill from first steps through to advanced use."
    )


class ChapterOutline(BaseModel):
    """One planned chapter. The prose itself is written later by chapter-agent."""

    number: int = Field(description="Position in the course, starting at 1.")
    title: str = Field(
        description=(
            "What this chapter teaches, stated concretely. Name the actual topic rather "
            "than using a placeholder like 'Introduction' or 'Getting Started'."
        )
    )
    objectives: list[str] = Field(
        default_factory=list,
        description=(
            "Two to four things the learner can DO after this chapter. Start each with a "
            "verb, and make them checkable — 'create an index' not 'understand indexing'."
        ),
    )


class Curriculum(BaseModel):
    """Output of curriculum-agent — the plan the whole course is built from."""

    title: str = Field(description="Course title naming the skill and the outcome it leads to.")
    summary: str = Field(
        description="Two or three sentences on what the course covers and who it is for."
    )
    chapters: list[ChapterOutline] = Field(
        description="Ordered chapters. Each must build on the previous ones, never repeat them."
    )


class ChapterSection(BaseModel):
    """One titled part of a chapter. Asking for parts rather than one blob is what lets us
    render the Markdown structure ourselves instead of hoping for it."""

    heading: str = Field(
        description=(
            "Short heading naming what this part covers, e.g. 'Defining the fields'. "
            "No numbering and no generic labels like 'Introduction'."
        )
    )
    markdown: str = Field(
        description=(
            "This section's content in Markdown: short paragraphs, lists, and fenced code "
            "blocks with a language tag on every example. Do not write a heading — the "
            "heading above is rendered for you."
        )
    )


class ChapterDraft(BaseModel):
    """Response schema for chapter-agent.

    The number and title are already fixed by the curriculum, so the model is asked only
    for what it alone can produce. Whatever it returns is fitted back onto its outline.
    """

    sections: list[ChapterSection] = Field(
        description=(
            "Three to six sections in reading order. The first must show the learner "
            "something concrete; the last must leave them able to do the objectives."
        )
    )
    key_points: list[str] = Field(
        description=(
            "Three to six one-line takeaways worth revising later. State facts and rules, "
            "not a table of contents of the chapter."
        )
    )
    exercises: list[str] = Field(
        description=(
            "Two to four tasks the learner performs themselves, each one checkable by "
            "looking at the result. Give the task only, never the answer."
        )
    )


class Chapter(BaseModel):
    """A written chapter: its outline's number and title, plus the drafted content."""

    number: int
    title: str
    body_markdown: str
    key_points: list[str] = Field(default_factory=list)
    # Do-it-now tasks with no answer shipped. Anything with a worked answer is a PracticeItem.
    exercises: list[str] = Field(default_factory=list)


class PracticeKind(StrEnum):
    """Multiple choice is deliberately absent — machine-marked questions are quiz-agent's."""

    RECALL = "recall"
    APPLY = "apply"
    BUILD = "build"
    DIAGNOSE = "diagnose"


class PracticeTask(BaseModel):
    """Response shape for practice-agent. The chapter is already known, so it is attached
    afterwards rather than asked for."""

    kind: PracticeKind = Field(
        description=(
            "What the learner has to do: recall it from memory, apply it to a scenario, "
            "build something, or diagnose something broken."
        )
    )
    prompt: str = Field(
        description=(
            "The task, stated so the learner knows when they are done. Name the specific "
            "thing to produce. Never offer options to choose between."
        )
    )
    solution: str = Field(
        description=(
            "The worked answer, with the reasoning or the code, so a learner can mark "
            "themselves. Never a single word and never just a restatement of the task."
        )
    )


class PracticeSet(BaseModel):
    """Response schema for practice-agent: one chapter's worth of practice."""

    tasks: list[PracticeTask] = Field(
        description=(
            "One task per learning objective, in the order the objectives were given. "
            "Vary the kind across the set rather than making them all recall."
        )
    )


class PracticeItem(BaseModel):
    """One practice task tied to a chapter.

    Unlike a chapter exercise it always ships a worked solution; unlike a quiz question it
    is judged by the learner rather than marked by the machine.
    """

    chapter_number: int
    kind: PracticeKind
    prompt: str
    solution: str


class ProjectDraft(BaseModel):
    """One project in the ramp.

    The difficulty is its position in the list, so it is not asked for, and the folder tree
    is drawn from `files` rather than requested as ASCII art.
    """

    title: str = Field(
        description=(
            "What the thing is called, naming what it does. Not 'Practice Project 1' and "
            "not a to-do list or a calculator unless the skill is genuinely about those."
        )
    )
    summary: str = Field(
        description=(
            "One or two sentences a learner could put on a CV: what it does and what it "
            "proves they can do."
        )
    )
    features: list[str] = Field(
        description=(
            "Three to six things the finished project does, each observable by using it. "
            "Every one must be buildable with what the course teaches."
        )
    )
    files: list[str] = Field(
        description=(
            "Every file and folder in the project as a full path from the project root, "
            "e.g. 'src/api/routes.py'. End folder paths with '/'. List them plainly, one "
            "per entry — do not draw a tree."
        )
    )
    milestones: list[str] = Field(
        description=(
            "Three to six steps in build order, each ending with something that runs. "
            "Never 'set up the project' as a milestone on its own."
        )
    )
    stretch_goals: list[str] = Field(
        description=(
            "Two to four extensions for a learner who finishes early, beyond what the "
            "course covered."
        )
    )


class ProjectPlan(BaseModel):
    """Response schema for project-agent.

    All the projects come from one call because they have to work as a ramp — separate calls
    would each reach for the most obvious idea and produce three variations of it.
    """

    projects: list[ProjectDraft] = Field(
        description=(
            "Projects in increasing order of ambition, each a different kind of thing rather "
            "than the previous one with more features."
        )
    )


class Project(BaseModel):
    """A portfolio project. `level` is how hard the project is within this course, not a
    claim about the learner — that is `LearningRequest.experience`."""

    level: ExperienceLevel
    title: str
    summary: str = ""
    features: list[str] = Field(default_factory=list)
    folder_structure: str = ""
    milestones: list[str] = Field(default_factory=list)
    stretch_goals: list[str] = Field(default_factory=list)


class QuizDraft(BaseModel):
    """Response shape for one quiz question.

    The right answer is asked for as text, never as an index. An index is a claim about a
    list the model has to keep in its head while writing it, and that claim is often wrong;
    the text is a thing it already knows. We build the options and locate the answer.
    """

    question: str = Field(
        description=(
            "One question with a single defensible answer. Ask about what the chapter "
            "taught, not about the chapter itself, and never say 'according to the text'."
        )
    )
    correct_answer: str = Field(
        description="The right answer, stated in full so it reads correctly on its own."
    )
    distractors: list[str] = Field(
        description=(
            "Three answers that are wrong but tempting: real mistakes a learner makes here. "
            "Each must be the same kind of thing and a similar length as the right answer, "
            "and none may be arguably correct."
        )
    )
    explanation: str = Field(
        description=(
            "Why the right answer is right and why a learner picking a wrong one went "
            "astray. Never mention option letters or positions."
        )
    )


class QuizSet(BaseModel):
    """Response schema for quiz-agent: the questions for one scope."""

    questions: list[QuizDraft] = Field(
        description="Questions in the order the material was taught, each testing a different point."
    )


class QuizQuestion(BaseModel):
    """An assembled question. `correct_index` is computed when the options are shuffled,
    so it cannot disagree with `options`."""

    question: str
    options: list[str]
    correct_index: int
    explanation: str = ""


class Quiz(BaseModel):
    """Machine-marked assessment. Anything that needs a human to judge it is a PracticeItem."""

    scope: str
    questions: list[QuizQuestion]
    # None marks the final assessment. Carried as a field so nothing has to parse `scope`.
    chapter_number: int | None = None


class ChapterVerdict(BaseModel):
    """Response shape for reviewing one chapter.

    The chapter number is not asked for — we know which chapter we sent — and neither is
    whether to rewrite it, which is a cost decision rather than a reading one.
    """

    score: int = Field(
        description=(
            "0-100 for this chapter alone. 90 or above means a learner could work through "
            "it unaided. Judge what is actually on the page, not how important the topic is."
        )
    )
    issues: list[str] = Field(
        default_factory=list,
        description=(
            "What a rewrite would have to fix, most serious first, each naming the "
            "specific passage or the missing explanation. Leave this empty when the "
            "chapter is sound — do not invent criticism to appear thorough."
        ),
    )


class CourseVerdict(BaseModel):
    """Response shape for the whole-course pass: the problems no single chapter can show."""

    issues: list[str] = Field(
        default_factory=list,
        description=(
            "Problems only visible across chapters: a prerequisite that is never taught, "
            "two chapters covering the same ground, or an order that uses something "
            "before explaining it. Do not repeat faults contained in a single chapter. "
            "Leave empty if the course holds together."
        ),
    )


class ReviewResult(BaseModel):
    score: int
    issues: list[str] = Field(default_factory=list)
    regenerate_chapters: list[int] = Field(default_factory=list)

    # Keyed by chapter number, so a rewrite can be told what was wrong with its last draft.
    chapter_issues: dict[int, list[str]] = Field(default_factory=dict)


class PublishedCourse(BaseModel):
    markdown_url: str
    pdf_url: str | None = None
    docx_url: str | None = None


class Rejection(BaseModel):
    """Terminal result when the prompt was not a request to learn something."""

    message: str


class Clarification(BaseModel):
    """Terminal result when the learner named several skills and chose none of them.

    Kept apart from Rejection: we understood the message and can help, we just must not
    guess which course they wanted.
    """

    message: str
    options: list[str]


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
        """Keyed off the chapter list rather than the score, so the decision to loop and the
        work that loop would do cannot disagree."""
        return (
            self.review is not None
            and bool(self.review.regenerate_chapters)
            and self.revision_count < MAX_REVISIONS
        )

    @property
    def percent(self) -> int:
        return progress_percent(self.completed_steps)
