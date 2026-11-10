"""Compatibility test: prove new deduplicated vector and calibration serialization produces
byte-for-byte identical format to legacy code and that database-stored data still decrypts.
"""

import base64
import json
import secrets
import uuid

import numpy as np
from app.core.context import AppContext
from app.core.crypto import aad, open_sealed, seal
from app.db.event_scope import event_transaction
from app.modules.vision_pipeline import repository
from app.modules.vision_pipeline.repository import _pack, _unpack
from app.ports.face_analyzer import Vector
from facescan_ai.index_store import decode_vector, encode_vector
from facescan_ai.matching.calibration import EngineCalibration
from fastapi.testclient import TestClient


# ------------------------------------------------ Legacy implementations
def _legacy_pack(vectors: dict[str, Vector]) -> dict[str, str]:
    return {
        k: base64.b64encode(np.asarray(v, dtype="<f4").tobytes()).decode("ascii")
        for k, v in vectors.items()
    }


def _legacy_unpack(packed: dict[str, str]) -> dict[str, Vector]:
    return {
        k: np.frombuffer(base64.b64decode(v, validate=True), dtype="<f4").astype(np.float32)
        for k, v in packed.items()
    }


def _legacy_calibration_to_dict(cal: EngineCalibration) -> dict[str, float | int | bool | None]:
    return {
        "release_threshold": cal.release_threshold,
        "borderline_threshold": cal.borderline_threshold,
        "impostor_pairs": cal.impostor_pairs,
        "release_calibrated": cal.release_calibrated,
        "borderline_calibrated": cal.borderline_calibrated,
        "impostor_max": cal.impostor_max,
    }


def _legacy_calibration_from_dict(
    engine: str, raw: dict[str, float | int | bool | None]
) -> EngineCalibration:
    impostor_max = raw.get("impostor_max")
    return EngineCalibration(
        engine=engine,
        release_threshold=float(raw["release_threshold"] or 0.0),
        borderline_threshold=float(raw["borderline_threshold"] or 0.0),
        impostor_pairs=int(raw["impostor_pairs"] or 0),
        release_calibrated=bool(raw["release_calibrated"]),
        borderline_calibrated=bool(raw["borderline_calibrated"]),
        impostor_max=None if impostor_max is None else float(impostor_max),
    )


# ------------------------------------------------ Tests
def test_vector_serialization_byte_for_byte_identical() -> None:
    rng = np.random.default_rng(42)
    test_vectors = {
        "auraface": rng.standard_normal(512).astype(np.float32),
        "sface": rng.standard_normal(128).astype(np.float32),
        "zeros": np.zeros(64, dtype=np.float32),
        "negatives": np.full(32, -0.12345, dtype=np.float32),
    }

    # 1. Single vector encoding
    for name, vec in test_vectors.items():
        legacy_encoded = _legacy_pack({name: vec})[name]
        new_encoded = encode_vector(vec)
        assert new_encoded == legacy_encoded

        # Roundtrip decoding
        legacy_decoded = _legacy_unpack({name: legacy_encoded})[name]
        new_decoded = decode_vector(legacy_encoded)
        assert np.array_equal(new_decoded, legacy_decoded)
        assert np.array_equal(new_decoded, vec)

    # 2. Packed dict byte-for-byte serialization
    legacy_packed = _legacy_pack(test_vectors)
    new_packed = _pack(test_vectors)
    assert new_packed == legacy_packed

    legacy_json = json.dumps({"embeddings": legacy_packed}).encode("utf-8")
    new_json = json.dumps({"embeddings": new_packed}).encode("utf-8")
    assert new_json == legacy_json

    # 3. Unpack equivalence
    assert all(
        np.array_equal(_unpack(new_packed)[k], _legacy_unpack(legacy_packed)[k])
        for k in test_vectors
    )


def test_calibration_serialization_byte_for_byte_identical() -> None:
    cals = [
        EngineCalibration(
            engine="auraface",
            release_threshold=0.55,
            borderline_threshold=0.45,
            impostor_pairs=3000,
            release_calibrated=True,
            borderline_calibrated=True,
            impostor_max=0.523,
        ),
        EngineCalibration(
            engine="sface",
            release_threshold=0.45,
            borderline_threshold=0.363,
            impostor_pairs=10,
            release_calibrated=False,
            borderline_calibrated=False,
            impostor_max=None,
        ),
    ]

    for cal in cals:
        legacy_dict = _legacy_calibration_to_dict(cal)
        new_dict = cal.to_dict()
        assert new_dict == legacy_dict

        legacy_json = json.dumps(legacy_dict).encode("utf-8")
        new_json = json.dumps(new_dict).encode("utf-8")
        assert new_json == legacy_json

        # Reconstructed from legacy JSON
        legacy_restored = _legacy_calibration_from_dict(cal.engine, legacy_dict)
        new_restored = EngineCalibration.from_dict(cal.engine, legacy_dict)
        assert new_restored == legacy_restored
        assert new_restored == cal


def test_legacy_sealed_data_decrypts_with_new_code() -> None:
    event_id = uuid.uuid4()
    row_id = uuid.uuid4()
    key = secrets.token_bytes(32)

    vec = np.linspace(-1.0, 1.0, 128, dtype=np.float32)
    # Sealed with legacy pack
    payload = {"embeddings": _legacy_pack({"sface": vec}), "landmarks": [[1, 2], [3, 4]]}
    sealed_bytes = seal(key, json.dumps(payload).encode("utf-8"), aad(event_id, "faces", row_id))

    # Decrypted and decoded with new code
    decrypted_raw = open_sealed(key, sealed_bytes, aad(event_id, "faces", row_id))
    unpacked_payload = json.loads(decrypted_raw)
    unpacked_vec = _unpack(unpacked_payload["embeddings"])["sface"]

    assert np.array_equal(unpacked_vec, vec)


def test_database_stored_faces_and_calibration_roundtrip(
    organiser: TestClient, ctx: AppContext
) -> None:
    from app.modules.events.service import data_key
    from tests.conftest import make_png
    from tests.helpers import searchable_event

    photos = [make_png(1, 2, (64 + i, 48)) for i in range(2)]
    event_id_str, _, _ = searchable_event(ctx, organiser, photos, name="Serialization Compat")
    event_id = uuid.UUID(event_id_str)

    with event_transaction(ctx.sessions, event_id) as session:
        key, _ = data_key(session, ctx.keys, event_id)

        # 1. Decrypt faces and verify vectors
        faces = repository.load_faces(session, event_id, key)
        assert len(faces) >= 2
        for face in faces:
            assert "auraface" in face.embeddings
            assert "sface" in face.embeddings
            # Prove legacy and new pack/unpack on loaded vectors are identical
            assert _pack(face.embeddings) == _legacy_pack(face.embeddings)
            for _eng, vec in face.embeddings.items():
                assert np.array_equal(decode_vector(encode_vector(vec)), vec)

        # 2. Decrypt clusters and calibration
        _, clusters, cal_dict, engine_versions = repository.load_event_model(session, event_id, key)
        assert clusters and cal_dict
        for engine, cal_data in cal_dict.items():
            cal_obj = EngineCalibration.from_dict(engine, cal_data)
            legacy_cal_obj = _legacy_calibration_from_dict(engine, cal_data)
            assert cal_obj == legacy_cal_obj
            assert cal_obj.to_dict() == _legacy_calibration_to_dict(cal_obj)
