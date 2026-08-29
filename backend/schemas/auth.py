"""Request and response models for signing up and logging in."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Enough to be a plausible address and to catch a typo. Not RFC 5322: nothing here ever sends
# an email, so the address is only an account key and strict parsing would buy a dependency
# and nothing else.
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class Credentials(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    email: str = Field(pattern=EMAIL, max_length=200)
    # Long enough to matter, capped because scrypt hashes whatever it is given and a megabyte
    # of password is a free way to tie up a worker.
    password: str = Field(min_length=8, max_length=200)


class SignUp(Credentials):
    name: str = Field(default="", max_length=100)


class Session(BaseModel):
    token: str
    user_id: str
    email: str
    name: str = ""


class Learner(BaseModel):
    user_id: str
    email: str
    name: str = ""
