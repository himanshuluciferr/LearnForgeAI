"""Tests for the export skill: how a finished course is laid out as one Markdown document."""

from __future__ import annotations

import pytest

from backend.skills.exporter.skill import (
    demote_headings,
    escape_structure,
    indent_continuation,
    numbered,
    option_label,
    render_course,
    render_project,
    render_quiz,
)
from backend.workflow.state import (
    Chapter,
    ChapterOutline,
    CourseState,
    Curriculum,
    ExperienceLevel,
    LearningRequest,
    PracticeItem,
    PracticeKind,
    Project,
    Quiz,
    QuizQuestion,
    SkillAnalysis,
)

REQUEST = LearningRequest(
    is_learning_request=True,
    skill="Git",
    experience=ExperienceLevel.BEGINNER,
    goal="rebase without fear",
    daily_minutes=30,
)
CURRICULUM = Curriculum(
    title="Git for the Terrified",
    summary="Ten days to a clean history.",
    chapters=[ChapterOutline(number=1, title="Commits", objectives=["a"])],
)


def chapter(number: int = 1, body: str = "Some prose.") -> Chapter:
    return Chapter(
        number=number,
        title=f"Chapter body {number}",
        body_markdown=body,
        key_points=["points survive"],
        exercises=["do the thing"],
    )


def question(text: str = "What is a commit?", correct: int = 1) -> QuizQuestion:
    return QuizQuestion(
        question=text,
        options=["wrong", "a snapshot", "also wrong", "still wrong"],
        correct_index=correct,
        explanation="Because it is.",
    )


def state(**overrides) -> CourseState:
    base = {
        "job_id": "j",
        "user_id": "u",
        "prompt": "teach me git",
        "request": REQUEST,
        "curriculum": CURRICULUM,
        "chapters": [chapter()],
    }
    return CourseState(**{**base, **overrides})


# --- heading levels -----------------------------------------------------------------


def test_a_chapters_own_headings_sit_below_the_heading_we_give_it():
    assert demote_headings("## Setup\ntext\n### Detail") == "### Setup\ntext\n#### Detail"


def test_a_comment_in_a_code_block_is_not_a_heading():
    """The trap this function exists for: shell chapters are full of `# comment` lines."""
    body = "## Setup\n\n```bash\n# clone the repo\ngit clone x\n```\n\n## Next"

    assert "# clone the repo" in demote_headings(body)
    assert "## clone the repo" not in demote_headings(body)


def test_a_tilde_fence_hides_comments_too():
    assert demote_headings("~~~\n# not a heading\n~~~") == "~~~\n# not a heading\n~~~"


def test_an_unclosed_fence_does_not_swallow_the_rest_of_the_chapter():
    """Demoting stops, but nothing is lost — a stray fence must not delete content."""
    body = "```\n# comment\n\n## Real heading"

    assert "## Real heading" in demote_headings(body)


def test_headings_never_run_past_the_deepest_level_markdown_has():
    assert demote_headings("###### Deep") == "###### Deep"


def test_a_hash_that_is_not_a_heading_is_left_alone():
    assert demote_headings("#hashtag\nC# is a language") == "#hashtag\nC# is a language"


# --- options ------------------------------------------------------------------------


def test_options_are_lettered_from_a():
    assert option_label(0) == "a"
    assert option_label(3) == "d"


def test_a_quiz_longer_than_the_alphabet_numbers_its_options_instead():
    """Duplicate labels would make an answer key ambiguous, so we stop lettering."""
    assert option_label(26) == "27"


def test_every_option_is_offered_to_the_learner():
    rendered = render_quiz(Quiz(scope="s", questions=[question()], chapter_number=1))

    assert "a) wrong" in rendered
    assert "b) a snapshot" in rendered


def test_the_quiz_the_learner_reads_does_not_say_which_answer_is_right():
    rendered = render_quiz(Quiz(scope="s", questions=[question()], chapter_number=1))

    assert "Because it is." not in rendered
    assert "**" not in rendered


# --- free-form model text -----------------------------------------------------------
#
# Every case below came out of a real generated course. A practice prompt that quotes a
# conflicted README is plain prose to the model and structure to Markdown.


def test_a_file_listing_in_a_prompt_does_not_become_a_heading():
    """The one that reached the document: `# Project X` outranked the chapter above it."""
    prompt = "Resolve this conflict:\n\n<<<<<<< HEAD\n# Project X\n=======\n# Project X\n>>>>>>> feature"
    items = [PracticeItem(chapter_number=1, kind=PracticeKind.APPLY, prompt=prompt, solution="s")]

    document = render_course(state(practice=items))

    assert "\\# Project X" in document
    assert "\n# Project X" not in document


def test_a_row_of_equals_under_a_sentence_does_not_become_a_heading():
    """Setext headings need no `#` at all, so escaping ATX alone would have missed this."""
    assert escape_structure("This project does A and B.\n=======") == (
        "This project does A and B.\n\\======="
    )


def test_a_row_of_equals_after_a_blank_line_is_left_alone():
    """Nothing sits above it to be turned into a heading, so there is nothing to defuse."""
    assert escape_structure("text\n\n=======") == "text\n\n======="


def test_a_conflict_marker_does_not_become_a_block_quote():
    assert escape_structure(">>>>>>> feature-branch") == "\\>>>>>>> feature-branch"


def test_text_the_model_fenced_itself_is_left_exactly_as_written():
    fenced = "Look:\n\n```\n# Project X\n>>>>>>> theirs\n```"

    assert escape_structure(fenced) == fenced


def test_a_table_separator_survives_escaping():
    """`|---|---|` is dashes but not only dashes, and a table must still render."""
    assert escape_structure("| a | b |\n|---|---|") == "| a | b |\n|---|---|"


def test_a_multi_line_prompt_does_not_end_the_list_it_is_in():
    """An unindented second line closes the item, so the next task restarts at 1."""
    items = [
        PracticeItem(chapter_number=1, kind=PracticeKind.APPLY, prompt="one\n\nstill one", solution="s"),
        PracticeItem(chapter_number=1, kind=PracticeKind.RECALL, prompt="two", solution="s"),
    ]

    document = render_course(state(practice=items))

    assert "\n   still one" in document


def test_a_blank_line_inside_an_item_stays_blank():
    """Indenting it would turn the separator between two paragraphs into content."""
    assert indent_continuation("first\n\nsecond", 3) == "first\n\n   second"


def test_indentation_is_measured_from_the_number_so_double_digits_still_line_up():
    items = [f"item {n}" for n in range(1, 11)]

    assert numbered(items).endswith("10. item 10")
    assert "\n10. " in numbered(items)


def test_a_solution_cannot_restructure_the_answer_key_either():
    items = [
        PracticeItem(chapter_number=1, kind=PracticeKind.APPLY, prompt="p", solution="# Fixed")
    ]

    document = render_course(state(practice=items))

    assert "\\# Fixed" in document


# --- the whole document -------------------------------------------------------------


def test_a_course_with_no_chapters_is_not_published():
    empty = state(chapters=[])

    with pytest.raises(ValueError, match="no chapters"):
        render_course(empty)


def test_a_course_with_no_curriculum_is_not_published():
    unplanned = state(curriculum=None)

    with pytest.raises(ValueError, match="no curriculum"):
        render_course(unplanned)


def test_the_document_opens_with_the_course_title_and_contents():
    document = render_course(state())

    assert document.startswith("# Git for the Terrified")
    assert "## Contents" in document
    assert "Chapter 1: Chapter body 1" in document


def test_the_estimated_hours_appear_when_the_skill_was_analysed():
    analysis = SkillAnalysis(
        category="Tooling", difficulty=ExperienceLevel.BEGINNER, estimated_hours=12
    )

    assert "**Estimated:** 12 hours" in render_course(state(skill_analysis=analysis))


def test_a_course_generated_without_a_skill_analysis_still_renders():
    """Every field but curriculum and chapters is degraded, not broken."""
    assert "Estimated" not in render_course(state())


def test_practice_follows_the_chapter_it_belongs_to():
    item = PracticeItem(
        chapter_number=1, kind=PracticeKind.APPLY, prompt="Rebase it", solution="git rebase main"
    )
    document = render_course(state(practice=[item]))

    assert document.index("Chapter 1: Chapter body 1") < document.index("Rebase it")


def test_practice_left_over_from_a_dropped_chapter_is_not_printed_at_all():
    """A rewrite can drop a chapter. Its practice must not reappear as an answer to a
    question the document never asks."""
    items = [
        PracticeItem(chapter_number=1, kind=PracticeKind.RECALL, prompt="mine", solution="s1"),
        PracticeItem(chapter_number=9, kind=PracticeKind.RECALL, prompt="orphan", solution="s9"),
    ]
    document = render_course(state(practice=items))

    assert "mine" in document
    assert "orphan" not in document
    assert "s9" not in document
    assert "Chapter 9" not in document


def test_a_quiz_for_a_dropped_chapter_is_not_printed_either():
    quizzes = [
        Quiz(scope="Chapter 9", questions=[question("orphaned q")], chapter_number=9),
    ]

    assert "orphaned q" not in render_course(state(quizzes=quizzes))


def test_solutions_are_held_back_so_they_cannot_be_read_by_accident():
    item = PracticeItem(
        chapter_number=1, kind=PracticeKind.APPLY, prompt="Rebase it", solution="git rebase main"
    )
    document = render_course(state(practice=[item]))

    assert document.index("Rebase it") < document.index("## Answers")
    assert document.index("git rebase main") > document.index("## Answers")


def test_a_chapter_quiz_appears_under_its_chapter_and_the_final_one_stands_alone():
    quizzes = [
        Quiz(scope="Chapter 1: Chapter body 1", questions=[question("chapter q")], chapter_number=1),
        Quiz(scope="Final assessment", questions=[question("final q")], chapter_number=None),
    ]
    document = render_course(state(quizzes=quizzes))

    assert "### Check yourself" in document
    assert "## Final assessment" in document
    assert document.index("chapter q") < document.index("final q")


def test_the_answer_key_names_the_letter_and_the_answer():
    quizzes = [Quiz(scope="Chapter 1", questions=[question()], chapter_number=1)]
    answers = render_course(state(quizzes=quizzes)).split("## Answers")[1]

    assert "**b)** a snapshot" in answers
    assert "Because it is." in answers


def test_a_project_shows_its_tree_as_code_so_the_indentation_survives():
    project = Project(
        level=ExperienceLevel.BEGINNER,
        title="Commit Explorer",
        summary="Walk a repo's history.",
        folder_structure="src/\n  main.py",
        milestones=["read the log"],
    )
    rendered = render_project(project)

    assert "```\nsrc/\n  main.py\n```" in rendered
    assert "### Beginner: Commit Explorer" in rendered


def test_a_project_with_nothing_but_a_title_prints_no_empty_headings():
    rendered = render_project(Project(level=ExperienceLevel.BEGINNER, title="Bare"))

    assert "**Milestones**" not in rendered
    assert "**Structure**" not in rendered


def test_a_course_with_no_projects_has_no_projects_heading():
    assert "## Projects" not in render_course(state())


def test_the_document_ends_with_a_newline():
    """Files that do not end in a newline confuse diffs and some markdown renderers."""
    assert render_course(state()).endswith("\n")
