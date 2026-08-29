"""Password hashing and session tokens.

Testing behaviour rather than digests: pinning a hash string would break the day the cost is
raised, which is a change we want to be able to make.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import jwt
import pytest

from backend.services import security
from backend.services.security import (
    create_token,
    hash_password,
    read_token,
    user_id_for,
    verify_password,
)


def test_a_stored_password_does_not_contain_the_password() -> None:
    stored = hash_password("correct horse battery staple")
    assert "correct horse battery staple" not in stored
    assert stored.startswith("scrypt$")


def test_the_right_password_verifies_and_the_wrong_one_does_not() -> None:
    stored = hash_password("hunter2hunter2")
    assert verify_password("hunter2hunter2", stored)
    assert not verify_password("hunter2hunter3", stored)
    assert not verify_password("", stored)


def test_the_same_password_hashes_differently_every_time() -> None:
    """A per-password salt. Without it, identical passwords are visibly identical in the
    store and one cracked hash breaks every account sharing it."""
    assert hash_password("same password") != hash_password("same password")


@pytest.mark.parametrize("stored", ["", "nonsense", "scrypt$bad", "bcrypt$1$2$3$4$5"])
def test_a_hash_we_cannot_read_fails_rather_than_raises(stored: str) -> None:
    """A corrupt row must be a failed login, not a 500 that says so."""
    assert not verify_password("anything", stored)


def test_the_user_id_follows_the_email_regardless_of_case_or_spacing() -> None:
    assert user_id_for("Ada@example.com") == user_id_for("  ada@EXAMPLE.com ")
    assert user_id_for("ada@example.com") != user_id_for("grace@example.com")


def test_a_token_round_trips() -> None:
    claims = read_token(create_token("user-1", "ada@example.com"))
    assert claims is not None
    assert claims["sub"] == "user-1"
    assert claims["email"] == "ada@example.com"


def test_a_token_signed_with_another_key_is_refused() -> None:
    """The whole point of the signature: a caller must not be able to mint their own."""
    forged = jwt.encode({"sub": "someone-else"}, "a" * 40, algorithm="HS256")
    assert read_token(forged) is None


def test_an_expired_token_is_refused() -> None:
    past = datetime.now(timezone.utc) - timedelta(days=1)
    stale = jwt.encode(
        {"sub": "user-1", "exp": past}, security._secret(), algorithm="HS256"
    )
    assert read_token(stale) is None


def test_an_unsigned_token_is_refused() -> None:
    """alg=none is the classic JWT hole: without an explicit algorithms= a decoder will
    happily accept a token that was never signed."""
    unsigned = jwt.encode({"sub": "admin"}, key="", algorithm="none")
    assert read_token(unsigned) is None


def test_a_tampered_token_is_refused() -> None:
    token = create_token("user-1", "ada@example.com")
    header, payload, signature = token.split(".")
    assert read_token(f"{header}.{payload}x.{signature}") is None


@pytest.mark.parametrize("token", ["", "not.a.token", "a.b", "....."])
def test_rubbish_is_refused_rather_than_raising(token: str) -> None:
    assert read_token(token) is None


def test_hashing_costs_enough_to_be_worth_doing() -> None:
    """The point of scrypt is that it is slow. If this ever comes back instant, the cost
    parameters have been lost and the hashes are cheap to attack offline."""
    start = time.perf_counter()
    hash_password("measure me")
    assert time.perf_counter() - start > 0.005
