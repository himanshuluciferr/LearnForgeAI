"""Mentor chat endpoints, grounded in the user's generated course."""

from fastapi import APIRouter

router = APIRouter(prefix="/mentor", tags=["mentor"])
