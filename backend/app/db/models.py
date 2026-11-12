"""Imports every ORM model so Base.metadata is complete (Alembic and tests use this)."""

from app.background_jobs.models import Job
from app.db.base import Base
from app.features.activity_log.models import AuditLog
from app.features.consent.models import Consent
from app.features.events.models import (
    CameraOffset,
    Collection,
    Event,
    EventInvite,
    EventKey,
    EventMember,
    EventSection,
)
from app.features.face_matching.models import CannotLink, Match, SearchAttempt, UserTemplate
from app.features.gallery.models import Claim
from app.features.login.models import AuthChallenge, AuthSession, MagicLink, Passkey, User
from app.features.photo_processing.models import Cluster, EventCalibration, Face
from app.features.photo_upload.models import Photo, UploadSession
from app.features.scene_search.models import (
    ClaimAttempt,
    SceneEmbedding,
    SceneSearchAttempt,
    SceneSearchCandidate,
)

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
    "camera_offsets",
    "upload_sessions",
)

__all__ = [
    "EVENT_SCOPED_TABLES",
    "AuditLog",
    "AuthChallenge",
    "AuthSession",
    "Base",
    "CameraOffset",
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
    "UploadSession",
    "User",
    "UserTemplate",
]
