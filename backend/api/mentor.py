"""Mentor chat endpoints, grounded in the user's generated course.

Second place in the system that reads raw learner text — requirement-agent is the other — so
the question is quoted into the prompt as a question rather than appended as instructions.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from backend.agents.mentor import NOT_COVERED, answer_question
from backend.schemas.mentor import MentorQuestion, MentorReply
from backend.services.course_store import course_store

router = APIRouter(prefix="/mentor", tags=["mentor"])


@router.post("/{course_id}")
async def ask(course_id: str, user_id: str, question: MentorQuestion) -> MentorReply:
    course = await course_store.get(course_id, user_id)
    if course is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Course not found")

    answer = await answer_question(
        question.question,
        course.state,
        where={"course_id": course_id, "user_id": user_id},
    )
    return MentorReply(
        course_id=course_id,
        question=question.question,
        answer=answer.answer if answer.grounded else NOT_COVERED,
        grounded=answer.grounded,
        chapter_number=answer.chapter_number,
        # Nothing read for this question came from a chapter, so a grounded answer with no
        # chapter behind it is one we went and fetched.
        looked_up=answer.grounded and answer.chapter_number is None and bool(answer.look_up),
    )
