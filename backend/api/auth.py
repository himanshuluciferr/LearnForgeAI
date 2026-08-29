"""Sign up and sign in.

Deliberately small: no refresh flow, no password reset, no email verification. Everything here
either had to be right or was left out.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException, status

from backend.api.deps import CurrentLearner
from backend.models.user import User
from backend.schemas.auth import Credentials, Learner, Session, SignUp, StreamTicket
from backend.services.security import (
    TICKET_SECONDS,
    create_stream_ticket,
    create_token,
    hash_password,
    user_id_for,
    verify_password,
)
from backend.services.user_store import user_store

router = APIRouter(prefix="/auth", tags=["auth"])

WRONG = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED, detail="That email and password do not match"
)


@router.post("/signup", status_code=status.HTTP_201_CREATED)
async def signup(request: SignUp) -> Session:
    email = request.email.strip().lower()
    user_id = user_id_for(email)
    created = await user_store.create(
        User(
            id=user_id,
            user_id=user_id,
            email=email,
            name=request.name,
            password_hash=await asyncio.to_thread(hash_password, request.password),
        )
    )
    if created is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="That email is already registered"
        )
    return Session(
        token=create_token(created.user_id, created.email, created.name),
        user_id=created.user_id,
        email=created.email,
        name=created.name,
    )


@router.post("/login")
async def login(request: Credentials) -> Session:
    email = request.email.strip().lower()
    user = await user_store.get(user_id_for(email))
    # Hashed even when no such user exists, so the reply time does not say which emails are
    # registered, and both failures give the same message for the same reason.
    stored = user.password_hash if user else hash_password("no such user")
    if not await asyncio.to_thread(verify_password, request.password, stored) or user is None:
        raise WRONG
    return Session(
        token=create_token(user.user_id, user.email, user.name),
        user_id=user.user_id,
        email=user.email,
        name=user.name,
    )


@router.get("/me")
async def me(learner: CurrentLearner) -> Learner:
    """Lets the app tell a live session from an expired one without guessing at the token."""
    return learner


@router.post("/stream-ticket")
async def stream_ticket(learner: CurrentLearner) -> StreamTicket:
    """Traded for the session token because EventSource cannot send a header."""
    return StreamTicket(
        ticket=create_stream_ticket(learner.user_id), expires_in=TICKET_SECONDS
    )
