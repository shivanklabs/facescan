"""API models for sign-in and account (no email addresses in responses)."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SignInResponse(BaseModel):
    """Where to go after signing in."""

    next: str
    has_passkey: bool


class PasskeyCredential(BaseModel):
    """Body: a WebAuthn credential JSON from the browser (validated by py_webauthn)."""

    model_config = ConfigDict(extra="forbid")
    credential: dict[str, object]


# A display name: 1-80 printable characters, no angle brackets (shown in admin lists).
NAME_PATTERN = r"^[^\x00-\x1f\x7f<>]{1,80}$"


class RegisterOptionsRequest(BaseModel):
    """Body: start the common registration. The account type can never be admin."""

    model_config = ConfigDict(extra="forbid", strict=True)
    name: str = Field(min_length=1, max_length=80, pattern=NAME_PATTERN)
    email: str = Field(min_length=3, max_length=254)
    account_type: Literal["guest", "organiser", "photographer"]
    # The person ticked "I agree to the privacy policy and data use"; nothing else is accepted.
    accept_privacy: Literal[True]


class RegisterRequest(RegisterOptionsRequest):
    """Body: finish the registration with the new passkey (same name/email/type again)."""

    credential: dict[str, object]


class MeResponse(BaseModel):
    """The signed-in user's flags."""

    user_id: UUID
    auth_method: str
    has_passkey: bool
    age_answered: bool
    is_minor: bool
    can_create_events: bool
    is_admin: bool = False
    is_staff: bool = False
    role: str = "guest"  # guest / organiser / photographer / admin (set by the server)
    status: str = "active"  # active / pending / rejected / suspended
    display_name: str | None = None
    home: str = "/photos"


class AgeAnswer(BaseModel):
    """Body: the age question."""

    model_config = ConfigDict(extra="forbid", strict=True)
    is_adult: bool


class SignedOutResponse(BaseModel):
    """Sessions ended."""

    sessions_ended: int


class StaffKeyRedeem(BaseModel):
    """Body: a staff key to use once."""

    model_config = ConfigDict(extra="forbid", strict=True)
    key: str = Field(min_length=20, max_length=100, pattern=r"^FS-[A-Za-z0-9_-]+$")


class StaffKeyCreate(BaseModel):
    """Body: create a staff key (photographer keys belong to one event)."""

    model_config = ConfigDict(extra="forbid")
    role: Literal["admin", "organiser", "photographer"]
    event_id: UUID | None = None


class StaffKeyItem(BaseModel):
    """A staff key as listed to its creator (never the key itself)."""

    key_id: UUID
    role: str
    event_id: UUID | None
    created_at: datetime
    expires_at: datetime
    used: bool
    revoked: bool


class StaffKeyCreated(StaffKeyItem):
    """A new staff key: the key is shown this once."""

    key: str


class StaffKeyList(BaseModel):
    """The staff keys the caller created."""

    keys: list[StaffKeyItem]


class DeleteAccountRequest(BaseModel):
    """Body: the typed confirmation that the person wants their account erased."""

    model_config = ConfigDict(extra="forbid", strict=True)
    confirm: Literal["DELETE"]
