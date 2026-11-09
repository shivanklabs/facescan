"""Local labelling page for one benchmark event (dev tool; binds to 127.0.0.1 only).

Shows the event's clusters, every unclustered face and a per-photo view, and lets the owner of
the photos name clusters with opaque person ids, remove wrong faces, merge clusters and mark
faces unknown. Labels are saved to datasets/<event>/labels.json after every change.

Face thumbnails and photo previews are cropped, scaled and JPEG-encoded in memory and served
from memory; nothing image-related is written to disk. Requests are accepted only for the
loopback host names (DNS-rebinding guard) and, for changes, only as JSON from the page's own
origin, so other websites cannot drive the labeler.
"""

import threading
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated

import cv2
import numpy as np
import uvicorn
from bench_dataset import UNKNOWN, Labels, face_key, save_labels, validate_person_id
from facescan_ai.image_io import ImageDecodeError, read_image
from facescan_ai.index_store import EventIndex
from facescan_ai.types import ImageBGR
from fastapi import FastAPI, HTTPException, Request
from fastapi import Path as PathParam
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

HOST = "127.0.0.1"
ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
STATIC_DIR = Path(__file__).resolve().parent / "static"
STATIC_FILES = {"labeler.js": "text/javascript", "labeler.css": "text/css"}
THUMB_HEIGHT = 128
PREVIEW_MAX_SIDE = 1400
PHOTO_CACHE_SIZE = 6
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
PERSON_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,31}$"


class NameBody(BaseModel):
    """Body: name a cluster."""

    model_config = ConfigDict(extra="forbid")
    person_id: str = Field(pattern=PERSON_PATTERN)


class MergeBody(BaseModel):
    """Body: merge cluster `source` into cluster `target`."""

    model_config = ConfigDict(extra="forbid")
    source: int = Field(ge=0)
    target: int = Field(ge=0)


class LabelBody(BaseModel):
    """Body: label one face with a person id or 'unknown', or clear it (null)."""

    model_config = ConfigDict(extra="forbid")
    label: str | None = Field(default=None, pattern=r"^([a-z0-9][a-z0-9_-]{0,31}|unknown)$")


class LabelerState:
    """In-memory view of the event plus labels; every change is saved to labels.json."""

    def __init__(
        self,
        index: EventIndex,
        photos_dir: Path,
        labels: Labels,
        labels_path: Path,
        selfies: list[str],
    ) -> None:
        """
        Purpose: hold the event, the photo folder and the labels.
        Side effects: drops stale merges/removals when the index was rebuilt.
        """
        self.index = index
        self.photos_dir = photos_dir.resolve()
        self.labels = labels
        self.labels_path = labels_path
        self.selfies = selfies
        self.lock = threading.Lock()
        self._photos: OrderedDict[str, ImageBGR] = OrderedDict()
        labels.reset_view_if_stale(index)

    # ---- view model -------------------------------------------------------------------
    def _root(self, cluster_id: int) -> int:
        """Purpose: follow merges to the surviving cluster id."""
        seen = set()
        while cluster_id in self.labels.merged and cluster_id not in seen:
            seen.add(cluster_id)
            cluster_id = self.labels.merged[cluster_id]
        return cluster_id

    def key(self, row: int) -> str:
        """Purpose: stable key of a face row."""
        face = self.index.faces[row]
        return face_key(face.photo_id, face.bbox)

    def view_clusters(self) -> dict[int, list[int]]:
        """Purpose: cluster id -> face rows after merges and removals."""
        view: dict[int, list[int]] = {}
        for cluster in self.index.clusters:
            root = self._root(cluster.cluster_id)
            for row in cluster.all_faces:
                if self.key(row) not in self.labels.removed:
                    view.setdefault(root, []).append(row)
        return {k: sorted(v) for k, v in sorted(view.items())}

    def label_of(self, row: int) -> str | None:
        """Purpose: current label of a face row."""
        face = self.index.faces[row]
        return self.labels.label_for(face.photo_id, face.bbox)

    def state(self) -> dict[str, object]:
        """Purpose: everything the page needs (ids, boxes, labels; no vectors)."""
        clusters = self.view_clusters()
        clustered = {row for rows in clusters.values() for row in rows}
        photos: dict[str, list[int]] = {}
        for row, face in enumerate(self.index.faces):
            photos.setdefault(face.photo_id, []).append(row)
        cluster_list = []
        for cid, rows in clusters.items():
            names = {self.label_of(r) for r in rows}
            cluster_list.append(
                {
                    "id": cid,
                    "faces": rows,
                    "label": names.pop() if len(names) == 1 else ("mixed" if names else None),
                }
            )
        return {
            "event": self.index.event,
            "selfie_persons": self.selfies,
            "persons": sorted(self.labels.persons()),
            "clusters": cluster_list,
            "unclustered": [r for r in range(len(self.index.faces)) if r not in clustered],
            "photos": [
                {"index": i, "photo_id": photo, "faces": rows}
                for i, (photo, rows) in enumerate(sorted(photos.items()))
            ],
            "faces": [
                {
                    "row": row,
                    "photo_id": face.photo_id,
                    "size": round(face.bbox.short_side),
                    "quality": face.quality,
                    "weak": face.weak,
                    "label": self.label_of(row),
                }
                for row, face in enumerate(self.index.faces)
            ],
        }

    # ---- changes ----------------------------------------------------------------------
    def _save(self) -> None:
        """Purpose: persist labels.json. Side effects: writes one file."""
        save_labels(self.labels, self.labels_path)

    def name_cluster(self, cluster_id: int, person_id: str) -> None:
        """Purpose: label every face in a view cluster. Side effects: saves labels."""
        rows = self.view_clusters().get(cluster_id)
        if rows is None:
            raise KeyError(cluster_id)
        validate_person_id(person_id)
        for row in rows:
            face = self.index.faces[row]
            self.labels.set_label(face.photo_id, face.bbox, person_id)
        self._save()

    def merge(self, source: int, target: int) -> None:
        """Purpose: merge two view clusters. Side effects: saves labels."""
        clusters = self.view_clusters()
        if source not in clusters or target not in clusters or source == target:
            raise KeyError(source)
        self.labels.merged[source] = target
        self._save()

    def remove_face(self, row: int) -> None:
        """Purpose: take a face out of its cluster view and clear its label."""
        face = self.index.faces[row]
        self.labels.removed.add(self.key(row))
        self.labels.set_label(face.photo_id, face.bbox, None)
        self._save()

    def label_face(self, row: int, label: str | None) -> None:
        """Purpose: set, change or clear one face's label. Side effects: saves labels."""
        if label is not None and label != UNKNOWN:
            validate_person_id(label)
        face = self.index.faces[row]
        self.labels.set_label(face.photo_id, face.bbox, label)
        self._save()

    # ---- images (memory only) -----------------------------------------------------------
    def photo(self, photo_id: str) -> ImageBGR:
        """Purpose: decoded photo from a small in-memory LRU cache. Side effects: reads file."""
        cached = self._photos.get(photo_id)
        if cached is not None:
            self._photos.move_to_end(photo_id)
            return cached
        path = (self.photos_dir / photo_id).resolve()
        if not path.is_relative_to(self.photos_dir):
            raise FileNotFoundError(photo_id)
        image = read_image(path)
        self._photos[photo_id] = image
        while len(self._photos) > PHOTO_CACHE_SIZE:
            self._photos.popitem(last=False)
        return image

    def thumbnail(self, row: int) -> bytes:
        """Purpose: JPEG bytes of one face crop (in memory). Side effects: none on disk."""
        face = self.index.faces[row]
        image = self.photo(face.photo_id)
        h, w = image.shape[:2]
        b = face.bbox
        pad = 0.25 * max(b.width, b.height)
        x1, y1 = max(0, int(b.x - pad)), max(0, int(b.y - pad))
        x2, y2 = min(w, int(b.x2 + pad)), min(h, int(b.y2 + pad))
        crop = image[y1:y2, x1:x2]
        if crop.size == 0:
            crop = np.zeros((THUMB_HEIGHT, THUMB_HEIGHT, 3), dtype=np.uint8)
        scale = THUMB_HEIGHT / crop.shape[0]
        crop = np.asarray(
            cv2.resize(crop, (max(1, round(crop.shape[1] * scale)), THUMB_HEIGHT)), dtype=np.uint8
        )
        return _jpeg(crop)

    def preview(self, photo_index: int) -> bytes:
        """Purpose: scaled photo with numbered face boxes (in memory). No disk writes."""
        photos = sorted({f.photo_id for f in self.index.faces})
        photo_id = photos[photo_index]
        image = self.photo(photo_id).copy()
        for row, face in enumerate(self.index.faces):
            if face.photo_id != photo_id:
                continue
            b = face.bbox
            colour = (0, 160, 255) if self.label_of(row) is None else (0, 200, 0)
            thickness = max(2, image.shape[1] // 400)
            cv2.rectangle(image, (int(b.x), int(b.y)), (int(b.x2), int(b.y2)), colour, thickness)
            cv2.putText(
                image,
                str(row),
                (int(b.x), max(20, int(b.y) - 6)),
                cv2.FONT_HERSHEY_SIMPLEX,
                max(0.6, image.shape[1] / 1600),
                colour,
                thickness,
            )
        scale = min(1.0, PREVIEW_MAX_SIDE / max(image.shape[:2]))
        if scale < 1.0:
            size = (round(image.shape[1] * scale), round(image.shape[0] * scale))
            image = np.asarray(cv2.resize(image, size), dtype=np.uint8)
        return _jpeg(image)


def _jpeg(image: ImageBGR) -> bytes:
    """Purpose: encode to JPEG in memory. Raises ValueError if encoding fails."""
    ok, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:
        raise ValueError("could not encode image")
    return bytes(encoded.tobytes())


def create_app(state: LabelerState) -> FastAPI:
    """
    Purpose: build the labeler app around one LabelerState.
    Outputs: FastAPI app (no docs endpoints). Side effects: none until served.
    """
    app = FastAPI(title="FaceScan labeler", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=ALLOWED_HOSTS)

    @app.middleware("http")
    async def same_origin_only(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        """Purpose: refuse cross-site requests that change labels."""
        if request.method != "GET":
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            if origin is not None and origin not in (f"http://{host}",):
                return JSONResponse({"error": "cross_origin"}, status_code=403)
        return await call_next(request)

    face_row = Annotated[int, PathParam(ge=0)]

    def check_row(row: int) -> int:
        """Purpose: 404 for a face row outside the index."""
        if row >= len(state.index.faces):
            raise HTTPException(status_code=404, detail="no such face")
        return row

    @app.get("/", response_class=HTMLResponse)
    def page() -> HTMLResponse:
        """Purpose: the labelling page."""
        html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
        return HTMLResponse(html, headers=NO_STORE)

    @app.get("/static/{name}")
    def static(name: str) -> Response:
        """Purpose: the page's script and stylesheet (fixed allow-list)."""
        if name not in STATIC_FILES:
            raise HTTPException(status_code=404, detail="not found")
        return Response(
            (STATIC_DIR / name).read_bytes(), media_type=STATIC_FILES[name], headers=NO_STORE
        )

    @app.get("/api/state")
    def get_state() -> JSONResponse:
        """Purpose: clusters, faces, photos and labels."""
        with state.lock:
            return JSONResponse(state.state(), headers=NO_STORE)

    @app.post("/api/clusters/{cluster_id}/name")
    def name_cluster(
        cluster_id: Annotated[int, PathParam(ge=0)], body: NameBody
    ) -> dict[str, bool]:
        """Purpose: label every face of a cluster with a person id."""
        with state.lock:
            try:
                state.name_cluster(cluster_id, body.person_id)
            except (KeyError, ValueError) as exc:
                raise HTTPException(status_code=404, detail="no such cluster") from exc
        return {"ok": True}

    @app.post("/api/clusters/merge")
    def merge(body: MergeBody) -> dict[str, bool]:
        """Purpose: merge one cluster into another."""
        with state.lock:
            try:
                state.merge(body.source, body.target)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="no such cluster") from exc
        return {"ok": True}

    @app.post("/api/faces/{row}/remove")
    def remove(row: face_row) -> dict[str, bool]:
        """Purpose: remove a wrong face from its cluster."""
        with state.lock:
            state.remove_face(check_row(row))
        return {"ok": True}

    @app.post("/api/faces/{row}/label")
    def label(row: face_row, body: LabelBody) -> dict[str, bool]:
        """Purpose: label a face (person id or unknown) or clear it."""
        with state.lock:
            state.label_face(check_row(row), body.label)
        return {"ok": True}

    @app.get("/api/faces/{row}/thumb")
    def thumb(row: face_row) -> Response:
        """Purpose: face thumbnail, cropped in memory."""
        with state.lock:
            try:
                data = state.thumbnail(check_row(row))
            except (FileNotFoundError, ImageDecodeError) as exc:
                raise HTTPException(status_code=404, detail="photo missing") from exc
        return Response(data, media_type="image/jpeg", headers=NO_STORE)

    @app.get("/api/photos/{photo_index}/image")
    def preview(photo_index: Annotated[int, PathParam(ge=0)]) -> Response:
        """Purpose: photo preview with numbered face boxes, drawn in memory."""
        with state.lock:
            try:
                data = state.preview(photo_index)
            except (IndexError, FileNotFoundError, ImageDecodeError) as exc:
                raise HTTPException(status_code=404, detail="photo missing") from exc
        return Response(data, media_type="image/jpeg", headers=NO_STORE)

    return app


def server_config(app: FastAPI, port: int) -> uvicorn.Config:
    """
    Purpose: uvicorn settings for the labeler: loopback only, no access log.
    Side effects: none. The host is fixed; there is deliberately no way to change it.
    """
    return uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False)


def serve(app: FastAPI, port: int) -> None:
    """Purpose: run the labeler until Ctrl+C. Side effects: listens on 127.0.0.1:<port>."""
    uvicorn.Server(server_config(app, port)).run()
