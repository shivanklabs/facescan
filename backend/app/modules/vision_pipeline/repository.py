"""Encrypted storage of an event's faces, clusters (prototypes and membership) and calibration.

Vectors are packed as little-endian float32 + base64 inside JSON, then sealed with the event
data key; the associated data names the event, the table and the row (app/core/crypto.py).
Run every function inside the event's own transaction (RLS).
"""

import base64
import json
from uuid import UUID

import numpy as np
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.crypto import aad, open_sealed, seal
from app.modules.vision_pipeline.models import Cluster, EventCalibration, Face
from app.ports.face_analyzer import AnalysedFace, BuiltCluster, BuiltEvent, StoredFace, Vector

CalibrationDict = dict[str, dict[str, float | int | bool | None]]


def _pack(vectors: dict[str, Vector]) -> dict[str, str]:
    """Purpose: vectors -> base64 float32 text."""
    return {
        k: base64.b64encode(np.asarray(v, dtype="<f4").tobytes()).decode("ascii")
        for k, v in vectors.items()
    }


def _unpack(packed: dict[str, str]) -> dict[str, Vector]:
    """Purpose: inverse of _pack."""
    return {
        k: np.frombuffer(base64.b64decode(v, validate=True), dtype="<f4").astype(np.float32)
        for k, v in packed.items()
    }


def delete_event_model(session: Session, event_id: UUID) -> None:
    """Purpose: remove an event's faces, clusters and calibration before re-indexing."""
    session.execute(delete(Face).where(Face.event_id == event_id))
    session.execute(delete(Cluster).where(Cluster.event_id == event_id))
    session.execute(delete(EventCalibration).where(EventCalibration.event_id == event_id))


def add_faces(
    session: Session,
    event_id: UUID,
    photo_id: UUID,
    faces: list[AnalysedFace],
    data_key: bytes,
    key_version: int,
) -> None:
    """Purpose: insert a photo's faces with embeddings and landmarks encrypted."""
    for analysed in faces:
        face = Face(
            event_id=event_id,
            photo_id=photo_id,
            bbox_x=analysed.bbox[0],
            bbox_y=analysed.bbox[1],
            bbox_w=analysed.bbox[2],
            bbox_h=analysed.bbox[3],
            detector_score=analysed.detector_score,
            quality=analysed.quality,
            embedding_encrypted=b"",
            key_version=key_version,
        )
        session.add(face)
        session.flush()
        payload = {
            "embeddings": _pack(analysed.embeddings),
            "landmarks": [list(p) for p in analysed.landmarks],
        }
        face.embedding_encrypted = seal(
            data_key, json.dumps(payload).encode("utf-8"), aad(event_id, "faces", face.id)
        )


def load_faces(session: Session, event_id: UUID, data_key: bytes) -> list[StoredFace]:
    """Purpose: decrypt every face of the event. Raises DecryptionError on a wrong key."""
    faces = []
    for face in session.scalars(
        select(Face).where(Face.event_id == event_id).order_by(Face.created_at, Face.id)
    ):
        payload = json.loads(
            open_sealed(data_key, face.embedding_encrypted, aad(event_id, "faces", face.id))
        )
        faces.append(
            StoredFace(
                face_id=face.id,
                photo_id=face.photo_id,
                bbox=(face.bbox_x, face.bbox_y, face.bbox_w, face.bbox_h),
                quality=face.quality,
                detector_score=face.detector_score,
                weak=face.weak,
                embeddings=_unpack(payload["embeddings"]),
            )
        )
    return faces


def save_event_model(
    session: Session,
    event_id: UUID,
    built: BuiltEvent,
    engine_versions: dict[str, str],
    data_key: bytes,
    key_version: int,
) -> None:
    """Purpose: store weak flags, clusters (prototypes and membership encrypted together) and
    encrypted calibration."""
    faces = {f.id: f for f in session.scalars(select(Face).where(Face.event_id == event_id))}
    for face_id, weak in built.weak.items():
        faces[face_id].weak = weak
    for number, cluster in enumerate(built.clusters):
        row = Cluster(
            event_id=event_id,
            cluster_no=number,
            size=len(cluster.members) + len(cluster.attached_weak),
            payload_encrypted=b"",
            key_version=key_version,
        )
        session.add(row)
        session.flush()
        payload = {
            "prototypes": _pack(cluster.prototypes),
            "members": [str(f) for f in cluster.members],
            "attached_weak": [str(f) for f in cluster.attached_weak],
        }
        row.payload_encrypted = seal(
            data_key, json.dumps(payload).encode("utf-8"), aad(event_id, "clusters", row.id)
        )
    calibration = EventCalibration(
        event_id=event_id, payload_encrypted=b"", key_version=key_version
    )
    session.add(calibration)
    session.flush()
    calibration.payload_encrypted = seal(
        data_key,
        json.dumps({"calibration": built.calibration, "engine_versions": engine_versions}).encode(),
        aad(event_id, "event_calibrations", calibration.id),
    )


def load_event_model(
    session: Session, event_id: UUID, data_key: bytes
) -> tuple[list[StoredFace], list[BuiltCluster], CalibrationDict, dict[str, str]]:
    """
    Purpose: decrypt the event's faces, clusters and calibration for matching.
    Outputs: (faces, clusters, calibration, engine versions). Raises DecryptionError.
    """
    faces = load_faces(session, event_id, data_key)
    clusters = []
    for row in session.scalars(
        select(Cluster).where(Cluster.event_id == event_id).order_by(Cluster.cluster_no)
    ):
        payload = json.loads(
            open_sealed(data_key, row.payload_encrypted, aad(event_id, "clusters", row.id))
        )
        clusters.append(
            BuiltCluster(
                members=[UUID(f) for f in payload["members"]],
                attached_weak=[UUID(f) for f in payload["attached_weak"]],
                prototypes=_unpack(payload["prototypes"]),
            )
        )
    calibration: CalibrationDict = {}
    versions: dict[str, str] = {}
    row_cal = session.scalars(
        select(EventCalibration).where(EventCalibration.event_id == event_id)
    ).first()
    if row_cal is not None:
        payload = json.loads(
            open_sealed(
                data_key,
                row_cal.payload_encrypted,
                aad(event_id, "event_calibrations", row_cal.id),
            )
        )
        calibration = payload["calibration"]
        versions = payload["engine_versions"]
    return faces, clusters, calibration, versions
