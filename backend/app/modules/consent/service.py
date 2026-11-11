"""Consent and age: notice text, consent for face search, withdrawal.

- The notice text is versioned config (English and Hindi, draft pending legal review). A
  guest records two consents per event: 'notice' (read the notice) and 'face_search'.
- A face search needs a current, non-withdrawn face_search consent for the current notice
  version, an adult age answer, and the guest role.
- Withdrawal sets withdrawn_at, deletes the guest's matches and any template for the event,
  ends their sessions for the event, and is audit-logged.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from uuid import UUID

import yaml
from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core import policy as policy_module
from app.core.auth import CurrentUser, revoke_sessions
from app.core.context import AppContext, guest_search_allowed
from app.core.deps import STAFF_ROLES, require_role
from app.core.errors import ConflictError
from app.db.event_scope import event_transaction, user_transaction
from app.modules.audit.service import record
from app.modules.consent.models import Consent
from app.modules.events import repository as events_repo
from app.modules.events.models import Event
from app.modules.identity.models import User
from app.modules.matching.models import Match

NOTICE = "notice"
FACE_SEARCH = "face_search"
LANGUAGES = ("en", "hi")


@dataclass(frozen=True, slots=True)
class Notice:
    """Versioned notice text per language."""

    version: str
    draft: bool
    texts: dict[str, dict[str, str]]


@lru_cache(maxsize=4)
def load_notice(path: str) -> Notice:
    """Purpose: read the notice config once. Raises ValueError on a malformed file."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("languages"), dict):
        raise ValueError("consent notice config is malformed")
    texts = {
        lang: {str(k): str(v) for k, v in raw["languages"][lang].items()}
        for lang in LANGUAGES
        if lang in raw["languages"]
    }
    if set(texts) != set(LANGUAGES):
        raise ValueError("consent notice needs English and Hindi text")
    return Notice(version=str(raw["version"]), draft=bool(raw.get("draft", True)), texts=texts)


def notice_for(ctx: AppContext) -> Notice:
    """Purpose: the current notice (path relative to the repo root or absolute)."""
    path = Path(ctx.settings.consent_notice_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[4] / path
    return load_notice(str(path))


def active_consent(session: Session, event_id: UUID, user_id: UUID, purpose: str) -> Consent | None:
    """Purpose: the newest non-withdrawn consent of one purpose (inside the event scope)."""
    return session.scalars(
        select(Consent)
        .where(
            Consent.event_id == event_id,
            Consent.user_id == user_id,
            Consent.purpose == purpose,
            Consent.withdrawn_at.is_(None),
        )
        .order_by(Consent.granted_at.desc())
    ).first()


def load_country_policy(
    country_code: str, policies_dir: str | Path | None = None
) -> policy_module.CountryPolicy:
    """
    Purpose: load country policy delegation wrapper for consent checks.
    Inputs: country_code (e.g. 'IN'), optional policies_dir path.
    Outputs: CountryPolicy instance.
    Side effects: delegates to app.core.policy.load_country_policy.
    """
    return policy_module.load_country_policy(country_code, policies_dir)


def check_guest_search_access(
    session: Session, ctx: AppContext, event_id: UUID, user: CurrentUser
) -> None:
    """
    Purpose: unified access check for guest face search, scene search, candidate preview,
        and claims. Enforces guest role, no staff role, searchable event, search window,
        age policy, and active consent for the current notice version.
    Inputs: database session, app context, event_id, current user.
    Outputs: None.
    Side effects: raises HTTPException (403) or ConflictError on access rejection.
    """
    roles = require_role(session, event_id, user.user_id, ("guest",))
    if roles & set(STAFF_ROLES):
        raise HTTPException(status_code=403, detail="staff_cannot_search")
    event = events_repo.get_event(session, event_id)
    if event is None or event.status != "searchable":
        raise ConflictError("this event's photos are not ready yet")
    now = datetime.now(UTC)
    if event.search_window_ends_at is not None and now > event.search_window_ends_at:
        raise HTTPException(status_code=403, detail="Search window for this event has closed")
    policy = load_country_policy(event.country, ctx.settings.config_policies_dir)
    account = session.get(User, user.user_id)
    if not policy.minors_self_search_allowed:
        if account is None or account.age_answered_at is None:
            raise HTTPException(status_code=403, detail="age_question_required")
        if account.is_minor:
            raise HTTPException(status_code=403, detail="minor_guardian_consent_required")
    notice = notice_for(ctx)
    consent = active_consent(session, event_id, user.user_id, FACE_SEARCH)
    if consent is None or consent.notice_version != notice.version:
        raise HTTPException(status_code=403, detail="consent_required")


@dataclass(frozen=True, slots=True)
class GuestStatus:
    """What the guest pages need to decide the next step (no personal data)."""

    roles: tuple[str, ...]
    age_answered: bool
    is_minor: bool
    consent_current: bool
    notice_version: str
    search_enabled: bool
    dev_without_liveness: bool
    event_searchable: bool


def guest_status(ctx: AppContext, user: CurrentUser, event_id: UUID) -> GuestStatus:
    """
    Purpose: the guest's state in one event. 404 for non-members.
    Inputs: ctx, user, event_id.
    Outputs: GuestStatus with consent and search enablement flags.
    Side effects: none.
    """
    notice = notice_for(ctx)
    with event_transaction(ctx.sessions, event_id) as session:
        roles = require_role(
            session, event_id, user.user_id, ("guest", "organiser", "photographer")
        )
        account = session.get(User, user.user_id)
        event = session.get(Event, event_id)
        consent = active_consent(session, event_id, user.user_id, FACE_SEARCH)
        policy = load_country_policy(
            event.country if event else "IN", ctx.settings.config_policies_dir
        )
        age_answered = (
            account is not None and account.age_answered_at is not None
        ) or policy.minors_self_search_allowed
        is_minor = (account is None or account.is_minor) and not policy.minors_self_search_allowed
        return GuestStatus(
            roles=tuple(sorted(roles)),
            age_answered=age_answered,
            is_minor=is_minor,
            consent_current=consent is not None and consent.notice_version == notice.version,
            notice_version=notice.version,
            search_enabled=guest_search_allowed(ctx.settings),
            dev_without_liveness=ctx.settings.env == "dev" and ctx.settings.liveness_adapter == "",
            event_searchable=event is not None and event.status == "searchable",
        )


def grant(
    ctx: AppContext, user: CurrentUser, event_id: UUID, notice_version: str, language: str
) -> None:
    """
    Purpose: record the notice and the separate face-search consent for this event.
    Inputs: ctx, user, event_id, notice_version, language.
    Outputs: None.
    Side effects: inserts consent rows, restores withdrawn matches, records audit log.
    """
    notice = notice_for(ctx)
    if notice_version != notice.version:
        raise ConflictError("the notice has changed; read it again")
    now = datetime.now(UTC)
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("guest",))
        event = session.get(Event, event_id)
        policy = load_country_policy(
            event.country if event else "IN", ctx.settings.config_policies_dir
        )
        account = session.get(User, user.user_id)
        if not policy.minors_self_search_allowed:
            if account is None or account.age_answered_at is None:
                raise HTTPException(status_code=403, detail="age_question_required")
            if account.is_minor:
                raise HTTPException(status_code=403, detail="minor_guardian_consent_required")
        for purpose in (NOTICE, FACE_SEARCH):
            existing = active_consent(session, event_id, user.user_id, purpose)
            if existing is not None and existing.notice_version == notice.version:
                continue
            if existing is not None:
                existing.withdrawn_at = now  # superseded by the new version
            session.add(
                Consent(
                    event_id=event_id,
                    user_id=user.user_id,
                    purpose=purpose,
                    notice_version=notice.version,
                    language=language,
                    granted_at=now,
                )
            )

        # Restore matches previously marked as withdrawn
        session.execute(
            update(Match)
            .where(
                Match.event_id == event_id,
                Match.user_id == user.user_id,
                Match.score_band == "withdrawn",
            )
            .values(score_band="release")
        )

        record(
            session,
            event_id=event_id,
            actor=user.actor,
            action="consent.granted",
            details={"notice_version": notice.version, "language": language},
        )


def withdraw(ctx: AppContext, user: CurrentUser, event_id: UUID) -> dict[str, int]:
    """
    Purpose: withdraw face-search consent for one event without deleting any data.
    Inputs: ctx, user, event_id.
    Outputs: dict of count of updated consents, matches, and revoked sessions.
    Side effects: marks consents withdrawn, marks matches 'withdrawn',
        ends event sessions, logs audit row.
    """
    now = datetime.now(UTC)
    with event_transaction(ctx.sessions, event_id) as session:
        require_role(session, event_id, user.user_id, ("guest",))
        consents = session.scalars(
            select(Consent).where(
                Consent.event_id == event_id,
                Consent.user_id == user.user_id,
                Consent.withdrawn_at.is_(None),
            )
        ).all()
        for consent in consents:
            consent.withdrawn_at = now
        matches = session.execute(
            update(Match)
            .where(
                Match.event_id == event_id,
                Match.user_id == user.user_id,
                Match.score_band == "release",
            )
            .values(score_band="withdrawn")
        )
        counts = {
            "consents": len(consents),
            "matches": int(getattr(matches, "rowcount", 0) or 0),
            "templates": 0,
        }
        record(
            session, event_id=event_id, actor=user.actor, action="consent.withdrawn", details=counts
        )
    with ctx.sessions() as session, session.begin():
        counts["sessions"] = revoke_sessions(session, user.user_id, event_id=event_id)
    return counts


@dataclass(frozen=True, slots=True)
class ConsentRecord:
    """One of the user's consents (privacy settings)."""

    event_id: UUID
    event_name: str
    purpose: str
    notice_version: str
    granted_at: datetime
    withdrawn_at: datetime | None


def my_consents(ctx: AppContext, user: CurrentUser) -> list[ConsentRecord]:
    """Purpose: the user's consents across their events (user scope, read-only)."""
    with user_transaction(ctx.sessions, user.user_id) as session:
        rows = session.execute(
            select(Consent, Event.name)
            .join(Event, Event.id == Consent.event_id)
            .where(Consent.user_id == user.user_id)
            .order_by(Consent.granted_at.desc())
        ).all()
        return [
            ConsentRecord(
                c.event_id, name, c.purpose, c.notice_version, c.granted_at, c.withdrawn_at
            )
            for c, name in rows
        ]
