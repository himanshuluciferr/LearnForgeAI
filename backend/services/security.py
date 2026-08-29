"""Password hashing and session tokens.

scrypt from the standard library rather than a hashing dependency: it is a real password KDF
with a memory cost, which is the property that makes a stolen hash expensive to attack. Plain
sha256 is not, however many times you loop it.

A hackathon is not a reason to store a password badly. It is a reason to keep the surface
small: email and password, one signed token, no refresh flow, no password reset.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone

import jwt

from backend.config.settings import get_settings

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
TOKEN_DAYS = 7

# OWASP's floor for scrypt. n is the memory/CPU cost and is the number that matters.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
KEY_BYTES = 32
SALT_BYTES = 16


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    """Self-describing, so the cost can be raised later without stranding existing hashes."""
    salt = os.urandom(SALT_BYTES)
    key = hashlib.scrypt(
        password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64(salt)}${_b64(key)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time compare: a byte-by-byte one leaks how much of the hash matched."""
    try:
        scheme, n, r, p, salt, expected = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(),
            salt=_unb64(salt),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(_unb64(expected)),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(expected))


def user_id_for(email: str) -> str:
    """Derived from the email so a login is a point read rather than a query on a second index.

    Not a secret: authorisation comes from the token, and this only says which partition to
    look in.
    """
    return hashlib.sha256(email.strip().lower().encode()).hexdigest()[:32]


def _secret() -> str:
    """A generated per-process secret rather than a shared default. A default in source is the
    same secret on every deployment, which is no secret at all; the cost of generating one is
    that tokens do not survive a restart, and that is the right way round."""
    configured = get_settings().jwt_secret
    if configured:
        return configured
    global _EPHEMERAL
    if _EPHEMERAL is None:
        _EPHEMERAL = secrets.token_urlsafe(32)
        logger.warning("JWT_SECRET is not set: signing with a key that dies with this process")
    return _EPHEMERAL


_EPHEMERAL: str | None = None


def create_token(user_id: str, email: str, name: str = "") -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": user_id,
            "email": email,
            # Carried so the app can greet the learner without a second call. A rename would
            # need a fresh token, which is a fair trade for one fewer round trip.
            "name": name,
            "iat": now,
            "exp": now + timedelta(days=TOKEN_DAYS),
        },
        _secret(),
        algorithm=ALGORITHM,
    )


def read_token(token: str) -> dict | None:
    """None rather than an exception: an unreadable token and an expired one are the same
    answer to the caller, which is 401."""
    try:
        # algorithms= is not optional. Without it a token claiming alg=none would be accepted.
        return jwt.decode(token, _secret(), algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None
