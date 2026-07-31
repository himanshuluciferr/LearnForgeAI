"""Quiz delivery and answer-scoring endpoints."""

from fastapi import APIRouter

router = APIRouter(prefix="/quiz", tags=["quiz"])
