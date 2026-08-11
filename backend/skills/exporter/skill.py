"""Entry point for the export skill: renders a finished course to one Markdown document.

Pure and deterministic — no model, no I/O. The document is laid out for someone working
through it, so each chapter is followed by its own practice and quiz, and every answer is
held back to the end.
"""

from __future__ import annotations

import re

from backend.workflow.state import (
    Chapter,
    CourseState,
    PracticeItem,
    Project,
    Quiz,
)

FENCE = re.compile(r"^\s*(```|~~~)")
HEADING = re.compile(r"^(#{1,6})(\s)")
# Markdown tolerates up to three leading spaces before a block marker, so the escaper has
# to be laxer than HEADING above, which only ever sees a chapter's own deliberate headings.
ATX = re.compile(r"^ {0,3}#{1,6}(\s|$)")
SETEXT = re.compile(r"^ {0,3}(=+|-{2,})\s*$")
QUOTE = re.compile(r"^ {0,3}>")
MAX_HEADING_LEVEL = 6
OPTION_LABELS = "abcdefghijklmnopqrstuvwxyz"


def option_label(index: int) -> str:
    """Falls back to the raw number rather than wrapping past 'z' onto a duplicate letter."""
    return OPTION_LABELS[index] if index < len(OPTION_LABELS) else str(index + 1)


def demote_headings(markdown: str, by: int = 1) -> str:
    """Push a chapter's own headings down so they nest under the heading we give the chapter.

    Lines inside fenced code are left alone: a bash chapter is full of `# comment` lines
    that are not headings and must not be renumbered.
    """
    lines: list[str] = []
    in_fence = False

    for line in markdown.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        match = None if in_fence else HEADING.match(line)
        if match:
            level = min(len(match.group(1)) + by, MAX_HEADING_LEVEL)
            line = f"{'#' * level}{line[match.end(1):]}"
        lines.append(line)

    return "\n".join(lines)


def escape_structure(text: str) -> str:
    """Blunt the markdown in free-form model text so it cannot restructure the document.

    A practice prompt that quotes a conflicted file arrives as plain prose: `# Project X`,
    a row of `=` under a sentence, and `>>>>>>> feature-branch` are file contents, but
    markdown reads them as a heading, a setext heading and a block quote. A stray heading
    is the worst of the three, because it lands in the document outline above the chapter
    that contains it. Escaping keeps every character visible exactly as the model wrote it
    while removing its structural meaning.

    Fenced blocks are left alone: there the model has already said "this is literal".
    """
    lines: list[str] = []
    in_fence = False
    after_text = False

    for line in text.splitlines():
        if FENCE.match(line):
            in_fence = not in_fence
        elif not in_fence and (
            ATX.match(line) or QUOTE.match(line) or (after_text and SETEXT.match(line))
        ):
            stripped = line.lstrip()
            line = f"{line[: len(line) - len(stripped)]}\\{stripped}"
        # A setext underline only makes a heading out of the paragraph line above it.
        after_text = bool(line.strip()) and not in_fence
        lines.append(line)

    return "\n".join(lines)


def indent_continuation(text: str, width: int) -> str:
    """Hold the rest of a multi-line item inside its list item.

    An unindented second line ends the list, so the item after it starts a fresh one and
    the numbering restarts. Blank lines stay blank, which is what separates paragraphs
    within a single item.
    """
    first, newline, rest = text.partition("\n")
    if not newline:
        return text
    padded = [f"{' ' * width}{line}" if line.strip() else line for line in rest.splitlines()]
    return "\n".join([first, *padded])


def bullets(items: list[str]) -> str:
    return "\n".join(f"- {indent_continuation(item, 2)}" for item in items)


def numbered(items: list[str]) -> str:
    return "\n".join(
        f"{marker}{indent_continuation(item, len(marker))}"
        for marker, item in ((f"{index}. ", item) for index, item in enumerate(items, start=1))
    )


def section(heading: str, body: str) -> str:
    """Empty sections are dropped rather than printed as a bare heading."""
    return f"{heading}\n\n{body}" if body.strip() else ""


def join(blocks: list[str]) -> str:
    return "\n\n".join(block for block in blocks if block.strip())


def render_practice(items: list[PracticeItem]) -> str:
    """Prompts only. The solutions are collected into the answer key at the end, so the
    learner cannot read one by accident while attempting the task."""
    return numbered([f"**({item.kind})** {escape_structure(item.prompt)}" for item in items])


def render_quiz(quiz: Quiz) -> str:
    blocks = []
    for index, question in enumerate(quiz.questions, start=1):
        options = "\n".join(
            f"   {option_label(position)}) {option}"
            for position, option in enumerate(question.options)
        )
        blocks.append(f"{index}. {question.question}\n{options}")
    return "\n\n".join(blocks)


def render_chapter(chapter: Chapter, practice: list[PracticeItem], quiz: Quiz | None) -> str:
    return join(
        [
            f"## Chapter {chapter.number}: {chapter.title}",
            demote_headings(chapter.body_markdown),
            section("### Key points", bullets(chapter.key_points)),
            section("### Try it now", bullets(chapter.exercises)),
            section("### Practice", render_practice(practice)),
            section("### Check yourself", render_quiz(quiz) if quiz else ""),
        ]
    )


def render_project(project: Project) -> str:
    tree = f"```\n{project.folder_structure.strip()}\n```" if project.folder_structure else ""
    return join(
        [
            f"### {project.level.title()}: {project.title}",
            project.summary,
            section("**What it does**", bullets(project.features)),
            section("**Structure**", tree),
            section("**Milestones**", numbered(project.milestones)),
            section("**Going further**", bullets(project.stretch_goals)),
        ]
    )


def render_answer_key(practice: list[PracticeItem], quizzes: list[Quiz]) -> str:
    blocks = ["## Answers"]

    for number in sorted({item.chapter_number for item in practice}):
        solutions = [
            escape_structure(item.solution)
            for item in practice
            if item.chapter_number == number
        ]
        blocks.append(section(f"### Chapter {number} practice", numbered(solutions)))

    for quiz in quizzes:
        answers = [
            f"**{option_label(question.correct_index)})** "
            f"{question.options[question.correct_index]}"
            + (f" — {question.explanation}" if question.explanation else "")
            for question in quiz.questions
        ]
        blocks.append(section(f"### {quiz.scope}", numbered(answers)))

    return join(blocks)


def header(state: CourseState) -> str:
    assert state.curriculum is not None and state.request is not None
    facts = [f"**Skill:** {state.request.skill}", f"**Level:** {state.request.assumed_level}"]
    if state.skill_analysis is not None:
        facts.append(f"**Estimated:** {state.skill_analysis.estimated_hours} hours")
    facts.append(f"**Chapters:** {len(state.chapters)}")

    return join([f"# {state.curriculum.title}", state.curriculum.summary, " · ".join(facts)])


def contents(chapters: list[Chapter]) -> str:
    return section(
        "## Contents",
        bullets([f"Chapter {chapter.number}: {chapter.title}" for chapter in chapters]),
    )


def render_course(state: CourseState) -> str:
    """Assemble the whole course. Raises rather than publishing something unreadable."""
    if state.curriculum is None or state.request is None:
        raise ValueError("exporter was asked to publish a course with no curriculum")
    if not state.chapters:
        raise ValueError("exporter was asked to publish a course with no chapters")

    by_chapter = {quiz.chapter_number: quiz for quiz in state.quizzes}
    numbers = {chapter.number for chapter in state.chapters}

    # A rewrite can drop a chapter, leaving practice and quizzes pointing at nothing. They
    # are filtered once, here, so the answer key cannot grow a heading for a chapter the
    # document does not contain.
    practice = [item for item in state.practice if item.chapter_number in numbers]
    quizzes = [
        quiz for quiz in state.quizzes if quiz.chapter_number is None or quiz.chapter_number in numbers
    ]

    blocks = [header(state), contents(state.chapters)]
    for chapter in state.chapters:
        own = [item for item in practice if item.chapter_number == chapter.number]
        blocks.append(render_chapter(chapter, own, by_chapter.get(chapter.number)))

    if state.projects:
        blocks.append(
            join(["## Projects"] + [render_project(project) for project in state.projects])
        )
    for quiz in quizzes:
        if quiz.chapter_number is None:
            blocks.append(section(f"## {quiz.scope}", render_quiz(quiz)))

    blocks.append(render_answer_key(practice, quizzes))
    return join(blocks) + "\n"
