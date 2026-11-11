"""Imports every ORM model so Base.metadata is complete (Alembic and tests use this)."""

from app.db.base import Base
from app.jobs.models import Job
from app.modules.audit.models import AuditLog
from app.modules.consent.models import Consent
from app.modules.events.models import (
    Collection,
    Event,
    EventInvite,
    EventKey,
    EventMember,
    EventSection,
)
from app.modules.gallery.models import Claim
from app.modules.identity.models import AuthChallenge, AuthSession, MagicLink, Passkey, User
from app.modules.ingest.models import Photo
from app.modules.matching.models import CannotLink, Match, SearchAttempt, UserTemplate
from app.modules.scene_search.models import (
    ClaimAttempt,
    SceneEmbedding,
    SceneSearchAttempt,
    SceneSearchCandidate,
)
from app.modules.vision_pipeline.models import Cluster, EventCalibration, Face

EVENT_SCOPED_TABLES = (
    "events",
    "event_members",
    "event_sections",
    "event_keys",
    "collections",
    "photos",
    "faces",
    "clusters",
    "event_calibrations",
    "matches",
    "claims",
    "consents",
    "user_templates",
    "audit_log",
    "jobs",
    "event_invites",
    "search_attempts",
    "scene_embeddings",
    "scene_search_candidates",
    "scene_search_attempts",
    "claim_attempts",
    "cannot_links",
)

__all__ = [
    "EVENT_SCOPED_TABLES",
    "AuditLog",
    "AuthChallenge",
    "AuthSession",
    "Base",
    "CannotLink",
    "Claim",
    "ClaimAttempt",
    "Cluster",
    "Collection",
    "Consent",
    "Event",
    "EventCalibration",
    "EventInvite",
    "EventKey",
    "EventMember",
    "EventSection",
    "Face",
    "Job",
    "MagicLink",
    "Match",
    "Passkey",
    "Photo",
    "SceneEmbedding",
    "SceneSearchAttempt",
    "SceneSearchCandidate",
    "SearchAttempt",
    "User",
    "UserTemplate",
]
