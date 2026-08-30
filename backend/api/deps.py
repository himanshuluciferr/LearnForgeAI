"""The dependency every endpoint uses to find out who is asking.

Before this, `user_id` was a query parameter: it routed to a partition and proved nothing, so
`?user_id=someone.else` was enough to be them. It now comes from a signed token, and no route
takes it from the caller.
"""

from __future__ import annotations

import time
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.schemas.auth import Learner
from backend.services import user_store as users
from backend.services.security import STREAM, read_token

# auto_error=False so a missing header is answered here with the same 401 as a bad one, rather
# than a 403 from the security scheme.
bearer = HTTPBearer(auto_error=False)

# A signature says the token was ours, not that the account still exists. Checking the store
# on every request would add a point read to every call, so a confirmed account is trusted for
# this long: it bounds a deleted account's reach to a minute rather than the token's week.
KNOWN_FOR_SECONDS = 60.0
_known: dict[str, float] = {}

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sign in first",
    headers={"WWW-Authenticate": "Bearer"},
)


def forget(user_id: str | None = None) -> None:
    """Drops what has been confirmed, so a test starts from nothing."""
    _known.pop(user_id, None) if user_id else _known.clear()


async def still_registered(user_id: str) -> bool:
    now = time.monotonic()
    if _known.get(user_id, 0.0) > now:
        return True
    # Only a positive is remembered. Caching a miss would keep a learner out for a minute
    # after they signed up.
    if await users.user_store.get(user_id) is None:
        return False
    _known[user_id] = now + KNOWN_FOR_SECONDS
    return True


async def learner_from(token: str, expect: str) -> Learner:
    claims = read_token(token, expect=expect)
    if not claims or not claims.get("sub"):
        raise UNAUTHENTICATED
    if not await still_registered(claims["sub"]):
        raise UNAUTHENTICATED
    return Learner(
        user_id=claims["sub"], email=claims.get("email", ""), name=claims.get("name", "")
    )


async def current_learner(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Learner:
    if credentials is None:
        raise UNAUTHENTICATED
    return await learner_from(credentials.credentials, expect="session")


async def ticket_holder(ticket: str = "") -> Learner:
    """For EventSource, which cannot set a header. Only ever accepts a stream ticket, so the
    thing in the url is useless anywhere else."""
    if not ticket:
        raise UNAUTHENTICATED
    return await learner_from(ticket, expect=STREAM)


# One alias, so a route says who is asking in the same way everywhere and cannot accidentally
# declare the learner as a plain parameter the client could set.
CurrentLearner = Annotated[Learner, Depends(current_learner)]
TicketHolder = Annotated[Learner, Depends(ticket_holder)]
