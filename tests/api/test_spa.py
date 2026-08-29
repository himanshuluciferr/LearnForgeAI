"""Tests for serving the built React app alongside the API.

The catch-all that makes deep links survive a reload is the only unauthenticated route in the
app, so what it will and will not answer with is worth pinning.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend import main
from backend.main import API_PREFIXES, app

client = TestClient(app)

built = pytest.mark.skipif(
    not main.STATIC.is_dir(), reason="the frontend has not been built into backend/static"
)


def test_the_api_still_answers_for_itself():
    """A mount at the root that shadowed the API would be the obvious way to break this."""
    assert client.get("/health").json() == {"status": "ok"}


def test_an_unauthenticated_api_call_is_still_refused():
    assert client.get("/courses").status_code == 401


@built
def test_a_deep_link_serves_the_app_rather_than_a_404():
    """A learner reloading on /read/<id> must get the app back, not a not-found."""
    response = client.get("/read/some-course-id")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")


@built
def test_the_root_serves_the_app():
    assert client.get("/").status_code == 200


@built
def test_the_shell_carries_no_learner_data():
    """It is served to anyone, so it must be an empty page that then signs the learner in."""
    body = client.get("/").text.lower()

    for leak in ("email", "token", "user_id", "password"):
        assert leak not in body


@built
@pytest.mark.parametrize("prefix", sorted(API_PREFIXES - {"health", "assets"}))
def test_a_mistyped_api_path_is_a_404_and_not_a_page(prefix):
    """Answering an unknown API path with HTML turns a broken call into a confusing one: the
    client gets 200 and a page where it expected JSON."""
    response = client.get(f"/{prefix}/nothing/here/at/all")

    assert response.status_code in (401, 404, 405)
    assert not response.headers["content-type"].startswith("text/html")


def test_every_api_router_is_covered_by_the_prefix_list():
    """The list is what stops the catch-all masking a real 404, so it has to stay complete."""
    from fastapi.routing import APIRoute

    from tests.api.test_auth import api_routes

    tops = {
        route.path.split("/")[1]
        for route in api_routes(app)
        if isinstance(route, APIRoute) and route.path.startswith("/") and route.path != "/"
    }
    named = {top for top in tops if not top.startswith("{")}

    assert named <= API_PREFIXES
