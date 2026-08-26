"""Response models for learner progress.

Every number here is computed from what is stored — chapters read and quiz attempts — rather
than kept as its own field, so the parts cannot drift out of step with the total.
"""

from __future__ import annotations

from pydantic import BaseModel


class ChapterProgress(BaseModel):
    number: int
    title: str
    read: bool
    # None when the learner has not taken that chapter's quiz; 0 is a real score.
    best_quiz_percent: int | None = None


class ProgressOut(BaseModel):
    course_id: str
    title: str
    chapters_read: int
    chapters_total: int
    percent: int
    next_chapter: int | None = None
    # Signed, and good for six days: the account has shared-key access disabled, so a read link
    # is a user-delegation SAS and Azure caps that at seven.
    markdown_url: str | None = None
    chapters: list[ChapterProgress]
