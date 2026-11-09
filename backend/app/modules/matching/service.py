"""TEMPORARY DEV-ONLY matching: one selfie against one event, photo ids per band.

The selfie is embedded in memory only (invariant 3) and must show exactly one face. The result
carries photo ids per band, never vectors or scores. Real guest matching (login, consent,
liveness, guest-only search per invariant 5) replaces this in a later milestone.
"""

from uuid import UUID

from app.core.context import AppContext
from app.core.errors import ConflictError, NotFoundError, UnprocessableError
from app.db.event_scope import event_transaction
from app.modules.audit.service import record
from app.modules.events import repository as events_repo
from app.modules.events.service import data_key
from app.modules.vision_pipeline import repository
from app.ports.face_analyzer import ImageRejectedError, MatchBands, SelfieRejectedError


def dev_match(ctx: AppContext, event_id: UUID, selfie: bytes, actor: str) -> MatchBands:
    """
    Purpose: match a selfie against one searchable event.
    Inputs: event id, selfie bytes (memory only), actor for the audit row.
    Outputs: MatchBands. Raises NotFoundError, ConflictError (not searchable or models
        changed) or UnprocessableError (not one face / not an image).
    Side effects: reads and decrypts the event model; writes an audit row with counts only.
    """
    with event_transaction(ctx.sessions, event_id) as session:
        event = events_repo.get_event(session, event_id)
        if event is None:
            raise NotFoundError("event not found")
        if event.status != "searchable":
            raise ConflictError(f"event is not searchable (status {event.status})")
    try:
        query = ctx.analyzer.selfie_query(selfie)
    except SelfieRejectedError as exc:
        raise UnprocessableError(str(exc)) from exc
    except ImageRejectedError as exc:
        raise UnprocessableError(f"selfie: {exc}") from exc
    finally:
        del selfie
    with event_transaction(ctx.sessions, event_id) as session:
        key, _version = data_key(session, ctx.keys, event_id)
        faces, clusters, calibration, versions = repository.load_event_model(session, event_id, key)
        current = ctx.analyzer.engine_versions
        if any(versions.get(k) != v for k, v in current.items() if k in ("auraface", "sface")):
            raise ConflictError("event was indexed with other model versions; re-index it")
        bands = ctx.analyzer.match(faces, clusters, calibration, query)
        record(
            session,
            event_id=event_id,
            actor=actor,
            action="match.dev",
            details={"release": len(bands.release), "borderline": len(bands.borderline)},
        )
    return bands
