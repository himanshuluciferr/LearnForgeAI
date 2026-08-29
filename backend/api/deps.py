"""The dependency every endpoint uses to find out who is asking.

Before this, `user_id` was a query parameter: it routed to a partition and proved nothing, so
`?user_id=someone.else` was enough to be them. It now comes from a signed token, and no route
takes it from the caller.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.schemas.auth import Learner
from backend.services.security import read_token

# auto_error=False so a missing header is answered here with the same 401 as a bad one, rather
# than a 403 from the security scheme.
bearer = HTTPBearer(auto_error=False)

UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Sign in first",
    headers={"WWW-Authenticate": "Bearer"},
)


def current_learner(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
) -> Learner:
    if credentials is None:
        raise UNAUTHENTICATED
    claims = read_token(credentials.credentials)
    if not claims or not claims.get("sub"):
        raise UNAUTHENTICATED
    return Learner(
        user_id=claims["sub"], email=claims.get("email", ""), name=claims.get("name", "")
    )


# One alias, so a route says who is asking in the same way everywhere and cannot accidentally
# declare the learner as a plain parameter the client could set.
CurrentLearner = Annotated[Learner, Depends(current_learner)]
