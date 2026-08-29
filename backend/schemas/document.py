"""The course as the reader sees it.

`GET /courses/{id}` used to return the whole StoredCourse, which serialises all nineteen
fields of the workflow state: the research corpus, the reviewer's verdicts, the drafts, and
`quizzes[].questions[].correct_index`. The quiz endpoint strips the answer key and marks
server-side so a score cannot be forged; the course endpoint was handing the same learner
every answer.

So this is a deliberate projection rather than a dump. Anything not built here cannot leak.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from backend.models.course import StoredCourse
from backend.workflow.state import Chapter, ChapterDiagram, CourseState


class DiagramEdgeOut(BaseModel):
    source: str
    target: str
    label: str = ""


class DiagramOut(BaseModel):
    kind: str
    title: str
    nodes: list[str] = []
    edges: list[DiagramEdgeOut] = []


class TopicOut(BaseModel):
    """One topic as separate blocks, so the client renders each by name rather than parsing
    a wall of Markdown to find where the explanation stops and the code starts."""

    number: int
    label: str
    title: str
    what_it_is: str
    why_it_matters: str
    how_to_use: str
    implementation: str = ""
    diagram: DiagramOut | None = None


class PracticeOut(BaseModel):
    kind: str
    prompt: str
    solution: str


class ChapterOut(BaseModel):
    number: int
    title: str
    topics: list[TopicOut] = []
    key_points: list[str] = []
    exercises: list[str] = []
    practice: list[PracticeOut] = []
    diagram: DiagramOut | None = None
    # Whether a quiz exists, never the quiz itself: the questions come from /quiz, which is
    # the only place that knows how to withhold the answer.
    has_quiz: bool = False
    # Populated only for courses stored before topics existed, so an old course still renders.
    markdown: str = ""


class ProjectOut(BaseModel):
    level: str
    title: str
    summary: str = ""
    features: list[str] = []
    folder_structure: str = ""
    milestones: list[str] = []
    stretch_goals: list[str] = []


class CourseDocument(BaseModel):
    course_id: str
    title: str
    summary: str = ""
    created_at: datetime
    chapters: list[ChapterOut] = []
    projects: list[ProjectOut] = []
    has_final_quiz: bool = False
    markdown_url: str | None = None


def as_diagram(diagram: ChapterDiagram | None) -> DiagramOut | None:
    if diagram is None:
        return None
    return DiagramOut(
        kind=str(diagram.kind),
        title=diagram.title,
        nodes=list(diagram.nodes),
        edges=[
            DiagramEdgeOut(source=edge.source, target=edge.target, label=edge.label)
            for edge in diagram.edges
        ],
    )


def as_chapter(chapter: Chapter, state: CourseState) -> ChapterOut:
    quiz_chapters = {quiz.chapter_number for quiz in state.quizzes}
    return ChapterOut(
        number=chapter.number,
        title=chapter.title,
        topics=[
            TopicOut(
                number=topic.number,
                label=topic.label,
                title=topic.title,
                what_it_is=topic.what_it_is,
                why_it_matters=topic.why_it_matters,
                how_to_use=topic.how_to_use,
                implementation=topic.implementation,
                diagram=as_diagram(topic.diagram),
            )
            for topic in chapter.topics
        ],
        key_points=list(chapter.key_points),
        exercises=list(chapter.exercises),
        practice=[
            PracticeOut(kind=str(item.kind), prompt=item.prompt, solution=item.solution)
            for item in state.practice
            if item.chapter_number == chapter.number
        ],
        diagram=as_diagram(chapter.diagram),
        has_quiz=chapter.number in quiz_chapters,
        markdown="" if chapter.topics else chapter.body_markdown,
    )


def as_document(course: StoredCourse) -> CourseDocument:
    state = course.state
    curriculum = state.curriculum
    return CourseDocument(
        course_id=course.id,
        title=curriculum.title if curriculum else "",
        summary=curriculum.summary if curriculum else "",
        created_at=course.created_at,
        chapters=[as_chapter(chapter, state) for chapter in state.chapters],
        projects=[
            ProjectOut(
                level=str(project.level),
                title=project.title,
                summary=project.summary,
                features=list(project.features),
                folder_structure=project.folder_structure,
                milestones=list(project.milestones),
                stretch_goals=list(project.stretch_goals),
            )
            for project in state.projects
        ],
        has_final_quiz=any(quiz.chapter_number is None for quiz in state.quizzes),
        markdown_url=state.published.markdown_url if state.published else None,
    )
