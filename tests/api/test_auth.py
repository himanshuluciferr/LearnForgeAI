"""Signing up, signing in, and the rule that a caller never names themselves.

The last test here is the one that matters most: it walks the live route table rather than a
list someone remembered to update, so a new endpoint that reads `user_id` from the client
fails the suite the day it is written.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.security.base import SecurityBase
from fastapi.testclient import TestClient

from backend.api import auth as auth_api
from backend.api.deps import CurrentLearner, ticket_holder
from backend.main import app
from backend.services.security import create_stream_ticket, create_token, read_token
from backend.services.user_store import InMemoryUserStore

client = TestClient(app)

SIGNUP = {"email": "ada@example.com", "password": "correct horse battery", "name": "Ada"}


@pytest.fixture(autouse=True)
def store(monkeypatch):
    users = InMemoryUserStore()
    monkeypatch.setattr(auth_api, "user_store", users)
    return users


def signup(**changes) -> dict:
    return client.post("/auth/signup", json={**SIGNUP, **changes}).json()


# --- signing up ----------------------------------------------------------------------


def test_signing_up_returns_a_token_that_works():
    token = signup()["token"]

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json()["email"] == "ada@example.com"


def test_the_name_survives_the_round_trip():
    """It came back empty from /auth/me the first time: the token did not carry it."""
    token = signup()["token"]

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {token}"}).json()[
        "name"
    ] == "Ada"


def test_the_password_is_never_stored_or_returned(store):
    body = signup()

    assert "password" not in str(body)
    kept = store._users[body["user_id"]]
    assert kept.password_hash != SIGNUP["password"]
    assert SIGNUP["password"] not in kept.password_hash


def test_the_same_email_cannot_be_registered_twice():
    """Not a 200 that quietly replaces the first account's password."""
    signup()

    again = client.post("/auth/signup", json=SIGNUP)

    assert again.status_code == 409


def test_the_email_is_stored_folded_so_case_is_not_a_second_account():
    first = signup()
    second = client.post("/auth/signup", json={**SIGNUP, "email": "ADA@example.com"})

    assert second.status_code == 409
    assert first["email"] == "ada@example.com"


@pytest.mark.parametrize(
    "changes",
    [
        {"password": "short"},
        {"email": "not-an-email"},
        {"email": ""},
        {"password": ""},
    ],
)
def test_a_bad_signup_is_refused(changes):
    assert client.post("/auth/signup", json={**SIGNUP, **changes}).status_code == 422


# --- signing in ----------------------------------------------------------------------


def test_the_right_password_signs_in():
    registered = signup()

    body = client.post(
        "/auth/login", json={"email": SIGNUP["email"], "password": SIGNUP["password"]}
    )

    assert body.status_code == 200
    assert body.json()["user_id"] == registered["user_id"]


def test_the_wrong_password_does_not():
    signup()

    response = client.post(
        "/auth/login", json={"email": SIGNUP["email"], "password": "not the password"}
    )

    assert response.status_code == 401


def test_an_unknown_email_gives_the_same_answer_as_a_wrong_password():
    """Different messages would turn the login form into a list of who has an account."""
    signup()

    wrong = client.post(
        "/auth/login", json={"email": SIGNUP["email"], "password": "not the password"}
    )
    unknown = client.post(
        "/auth/login", json={"email": "nobody@example.com", "password": "not the password"}
    )

    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()


# --- who is asking -------------------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer nonsense"},
        {"Authorization": "Basic abc"},
        {"Authorization": "Bearer "},
    ],
)
def test_me_refuses_anything_that_is_not_a_valid_token(headers):
    assert client.get("/auth/me", headers=headers).status_code == 401


# --- stream tickets ------------------------------------------------------------------


def test_a_ticket_is_issued_to_a_signed_in_learner():
    token = signup()["token"]

    body = client.post("/auth/stream-ticket", headers={"Authorization": f"Bearer {token}"})

    assert body.status_code == 200
    assert body.json()["expires_in"] == 60


def test_a_ticket_cannot_be_had_without_signing_in():
    assert client.post("/auth/stream-ticket").status_code == 401


def test_a_ticket_is_not_a_session():
    """It travels in a url, where it reaches logs and history. If it opened the rest of the
    API it would be a session token written down in public."""
    ticket = create_stream_ticket("user-1")

    assert client.get("/auth/me", headers={"Authorization": f"Bearer {ticket}"}).status_code == 401
    assert read_token(ticket, expect="session") is None
    assert read_token(ticket, expect="stream") is not None


def test_a_session_is_not_a_ticket():
    """The other direction: a long-lived token must not be usable in a query string just
    because the stream accepts one there."""
    session = create_token("user-1", "ada@example.com")

    assert read_token(session, expect="stream") is None


def api_routes(router) -> list[APIRoute]:
    """Included routers are kept nested rather than flattened onto `app.routes`, so walking
    the top level alone finds only /health. The first version of these guards did exactly
    that and passed while proving nothing."""
    found: list[APIRoute] = []
    for route in getattr(router, "routes", []):
        if isinstance(route, APIRoute):
            found.append(route)
        elif hasattr(route, "original_router"):
            found.extend(api_routes(route.original_router))
    return found


def is_secured(dependant) -> bool:
    if isinstance(getattr(dependant, "call", None), SecurityBase):
        return True
    # The stream is authorised by a ticket rather than a header, because EventSource cannot
    # send one. It is still authentication, so it counts here.
    if getattr(dependant, "call", None) is ticket_holder:
        return True
    return any(is_secured(child) for child in dependant.dependencies)


def caller_supplied(route: APIRoute) -> list[str]:
    fields = route.dependant.query_params + route.dependant.path_params
    return [field.name for field in fields + route.dependant.header_params]


def test_the_walker_finds_the_whole_api_not_just_the_top_level():
    """These guards are only worth having if they look at every route, so pin that they do."""
    paths = {route.path for route in api_routes(app)}

    assert {"/health", "/auth/login", "/courses", "/quiz/{course_id}"} <= paths
    assert len(api_routes(app)) >= 15


def test_no_route_lets_the_caller_say_who_they_are():
    """`?user_id=` used to be how every endpoint knew the learner, which meant anyone could
    be anyone. Walking the real route table, so this cannot regress quietly."""
    offenders = [
        f"{route.path} takes {caller_supplied(route)}"
        for route in api_routes(app)
        if "user_id" in caller_supplied(route)
    ]

    assert offenders == []


def test_every_learner_route_is_behind_the_token():
    """A new endpoint that forgets the dependency is an open door, and the only reliable way
    to notice is to ask each route what it requires."""
    public = {"/health", "/auth/signup", "/auth/login"}
    open_doors = [
        route.path
        for route in api_routes(app)
        if route.path not in public and not is_secured(route.dependant)
    ]

    assert open_doors == []


def test_the_guards_above_can_actually_fail():
    """A guard that cannot fail is decoration. Both of these silently passed once already."""
    sample = FastAPI()

    @sample.get("/leaky")
    async def leaky(user_id: str) -> dict:  # pragma: no cover - never called
        return {}

    @sample.get("/guarded")
    async def guarded(learner: CurrentLearner) -> dict:  # pragma: no cover - never called
        return {}

    routes = {route.path: route for route in api_routes(sample)}
    assert "user_id" in caller_supplied(routes["/leaky"])
    assert caller_supplied(routes["/guarded"]) == []
    assert not is_secured(routes["/leaky"].dependant)
    assert is_secured(routes["/guarded"].dependant)

