"""API models for sign-in and account (no email addresses in responses)."""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class MagicLinkRequest(BaseModel):
    """Body: send a sign-in link."""

    model_config = ConfigDict(extra="forbid")
    email: str = Field(min_length=3, max_length=254)
    next: str | None = Field(default=None, max_length=200)


class MagicLinkVerify(BaseModel):
    """Body: the token from the link's fragment."""

    model_config = ConfigDict(extra="forbid")
    token: str = Field(min_length=20, max_length=100)


class SignInResponse(BaseModel):
    """Where to go after signing in."""

    next: str
    has_passkey: bool


class PasskeyCredential(BaseModel):
    """Body: a WebAuthn credential JSON from the browser (validated by py_webauthn)."""

    model_config = ConfigDict(extra="forbid")
    credential: dict[str, object]


class MeResponse(BaseModel):
    """The signed-in user's flags."""

    user_id: UUID
    auth_method: str
    has_passkey: bool
    age_answered: bool
    is_minor: bool
    can_create_events: bool


class AgeAnswer(BaseModel):
    """Body: the age question."""

    model_config = ConfigDict(extra="forbid", strict=True)
    is_adult: bool


class SignedOutResponse(BaseModel):
    """Sessions ended."""

    sessions_ended: int
