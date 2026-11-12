"""Invariant 4: per-event data keys wrapped by the master key; destroying a key makes the
event's faces, prototypes and calibration unreadable."""

import secrets
import uuid

import pytest
from app.connectors.keys.env_master_key import EnvMasterKeyManager, parse_master_key
from app.core.context import AppContext
from app.core.crypto import DecryptionError, aad, open_sealed, seal
from app.db.event_scope import event_transaction
from app.features.events.service import EventKeyDestroyedError, data_key, destroy_event_key
from app.features.photo_processing import repository
from app.interfaces.key_manager import KeyUnwrapError
from fastapi.testclient import TestClient
from sqlalchemy import text
from tests.conftest import make_png


def test_wrap_unwrap_round_trip_and_binding() -> None:
    km = EnvMasterKeyManager(secrets.token_bytes(32))
    event_a, event_b = uuid.uuid4(), uuid.uuid4()
    dek = secrets.token_bytes(32)
    wrapped = km.wrap(event_a, dek)
    assert dek not in wrapped
    assert km.unwrap(event_a, wrapped) == dek
    with pytest.raises(KeyUnwrapError):
        km.unwrap(event_b, wrapped)  # bound to its event
    with pytest.raises(KeyUnwrapError):
        EnvMasterKeyManager(secrets.token_bytes(32)).unwrap(event_a, wrapped)  # other master
    tampered = bytearray(wrapped)
    tampered[-1] ^= 1
    with pytest.raises(KeyUnwrapError):
        km.unwrap(event_a, bytes(tampered))
    assert km.wrap(event_a, dek) != wrapped  # random nonce


def test_master_key_parsing_never_echoes_the_value() -> None:
    with pytest.raises(ValueError) as info:
        parse_master_key("not-base64!!")
    assert "not-base64" not in str(info.value)
    with pytest.raises(ValueError):
        parse_master_key("c2hvcnQ=")


def test_sealed_data_is_bound_to_event_table_and_row() -> None:
    key = secrets.token_bytes(32)
    e, r = uuid.uuid4(), uuid.uuid4()
    sealed = seal(key, b"vector bytes", aad(e, "faces", r))
    assert open_sealed(key, sealed, aad(e, "faces", r)) == b"vector bytes"
    for wrong in (
        aad(uuid.uuid4(), "faces", r),
        aad(e, "clusters", r),
        aad(e, "faces", uuid.uuid4()),
    ):
        with pytest.raises(DecryptionError):
            open_sealed(key, sealed, wrong)
    with pytest.raises(DecryptionError):
        open_sealed(secrets.token_bytes(32), sealed, aad(e, "faces", r))


def test_destroying_the_key_makes_faces_prototypes_and_calibration_unreadable(
    organiser: TestClient, ctx: AppContext
) -> None:
    from tests.helpers import FRAMES_HEADERS, frames_body, guest_client, searchable_event

    event_id_text, invite, _ = searchable_event(
        ctx, organiser, [make_png(1, 2, (64 + i, 48)) for i in range(3)]
    )
    event_id = uuid.UUID(event_id_text)
    guest = guest_client(ctx, "guest-keys@example.test", event_id_text, invite)
    with event_transaction(ctx.sessions, event_id) as s:
        key, _ = data_key(s, ctx.keys, event_id)
        faces, clusters, calibration, _ = repository.load_event_model(s, event_id, key)
        assert faces and clusters and calibration
        stored = s.execute(text("SELECT embedding_encrypted FROM faces LIMIT 1")).scalar_one()
        vector_bytes = faces[0].embeddings["auraface"][:4].astype("<f4").tobytes()
        assert vector_bytes not in stored  # ciphertext only

    destroy_event_key(ctx, event_id, "test")

    with event_transaction(ctx.sessions, event_id) as s:
        with pytest.raises(EventKeyDestroyedError):
            data_key(s, ctx.keys, event_id)
        row = s.execute(text("SELECT wrapped_key, destroyed_at FROM event_keys")).one()
        assert row.wrapped_key is None and row.destroyed_at is not None
        # The ciphertext is still there, but no key in the system opens it.
        assert s.execute(text("SELECT count(*) FROM faces")).scalar() == len(faces)
        with pytest.raises(DecryptionError):
            repository.load_faces(s, event_id, secrets.token_bytes(32))
    frames = frames_body(*[make_png(1, 1, (60 + i, 40)) for i in range(3)])
    response = guest.post(f"/events/{event_id}/search", content=frames, headers=FRAMES_HEADERS)
    assert response.status_code == 409  # event closed: nothing can be searched
