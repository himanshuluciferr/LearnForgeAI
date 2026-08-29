"""A registered learner.

Cosmos partition key is `user_id`, matching every other container, and the id is derived from
the email so a login is a point read rather than a query against a second index.
"""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class User(BaseModel):
    id: str
    user_id: str
    email: str
    name: str = ""
    # Never the password. `hash_password` writes this and only `verify_password` reads it.
    password_hash: str
    created_at: datetime = Field(default_factory=_now)
