"""Imports every ORM model so Base.metadata is complete (Alembic and tests use this)."""

from app.db.base import Base
from app.jobs.models import Job
from app.modules.audit.models import AuditLog
from app.modules.consent.models import Consent
from app.modules.events.models import Collection, Event, EventKey, EventMember, EventSection
from app.modules.gallery.models import Claim
from app.modules.identity.models import Passkey, User
from app.modules.ingest.models import Photo
from app.modules.matching.models import Match, UserTemplate
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
)

__all__ = [
    "EVENT_SCOPED_TABLES",
    "AuditLog",
    "Base",
    "Claim",
    "Cluster",
    "Collection",
    "Consent",
    "Event",
    "EventCalibration",
    "EventKey",
    "EventMember",
    "EventSection",
    "Face",
    "Job",
    "Match",
    "Passkey",
    "Photo",
    "User",
    "UserTemplate",
]
