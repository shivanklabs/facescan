"""YuNet face detector (OpenCV Zoo, MIT) on ONNX Runtime, with a tiled image pyramid.

Large photos are scanned as overlapping 640x640 tiles at several scales, so small faces in a
crowd (about 20-40 px) are found as well as close-ups; detections are merged with NMS.
"""

import math

import cv2
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from facescan_ai.config import DetectorConfig
from facescan_ai.detection.geometry import Tile, nms, plan_tiles, to_original, touches_inner_edge
from facescan_ai.types import BBox, Face, ImageBGR

STRIDES = (8, 16, 32)


def decode_yunet(
    outputs: dict[str, NDArray[np.float32]], input_size: int, score_threshold: float
) -> tuple[NDArray[np.float32], NDArray[np.float32], NDArray[np.float32]]:
    """
    Purpose: turn raw YuNet outputs into boxes, landmarks and scores (same maths as OpenCV's
        FaceDetectorYN for the 2023mar model).
    Inputs: output name -> array (cls_/obj_/bbox_/kps_<stride>), square input size, threshold.
    Outputs: boxes N x 4 (x1, y1, x2, y2), landmarks N x 5 x 2, scores N (input pixels).
    Side effects: none.
    """
    boxes, points, scores = [], [], []
    for stride in STRIDES:
        cells = input_size // stride
        cls = np.clip(outputs[f"cls_{stride}"].reshape(-1), 0.0, 1.0)
        obj = np.clip(outputs[f"obj_{stride}"].reshape(-1), 0.0, 1.0)
        score = np.sqrt(cls * obj)
        keep = np.nonzero(score >= score_threshold)[0]
        if keep.size == 0:
            continue
        rows = (keep // cells).astype(np.float32)
        cols = (keep % cells).astype(np.float32)
        bbox = outputs[f"bbox_{stride}"].reshape(-1, 4)[keep]
        kps = outputs[f"kps_{stride}"].reshape(-1, 10)[keep]
        cx = (cols + bbox[:, 0]) * stride
        cy = (rows + bbox[:, 1]) * stride
        w = np.exp(bbox[:, 2]) * stride
        h = np.exp(bbox[:, 3]) * stride
        boxes.append(np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1))
        lx = (kps[:, 0::2] + cols[:, None]) * stride
        ly = (kps[:, 1::2] + rows[:, None]) * stride
        points.append(np.stack([lx, ly], axis=2))
        scores.append(score[keep])
    if not boxes:
        return (
            np.zeros((0, 4), np.float32),
            np.zeros((0, 5, 2), np.float32),
            np.zeros((0,), np.float32),
        )
    return (
        np.concatenate(boxes).astype(np.float32),
        np.concatenate(points).astype(np.float32),
        np.concatenate(scores).astype(np.float32),
    )


class YuNetDetector:
    """Face detector: bbox, 5 landmarks and score for every face in an image."""

    name = "yunet"

    def __init__(self, session: ort.InferenceSession, version: str, config: DetectorConfig) -> None:
        """
        Purpose: wrap a verified YuNet session (see ModelRegistry.create_session).
        Inputs: session, model version, detector config. Side effects: none.
        """
        shape = session.get_inputs()[0].shape
        if shape[2:] != [config.tile_size, config.tile_size]:
            raise ValueError(f"YuNet input is {shape}, config.tile_size is {config.tile_size}")
        self._session = session
        self._input_name = session.get_inputs()[0].name
        self._output_names = [o.name for o in session.get_outputs()]
        self.version = version
        self.config = config

    def detect(self, image: ImageBGR) -> list[Face]:
        """
        Purpose: find faces of at least config.min_face_px in a BGR image.
        Inputs: H x W x 3 uint8 BGR image. Outputs: faces sorted by score, best first.
        Side effects: none (in-memory inference only).
        """
        height, width = image.shape[:2]
        cfg = self.config
        tiles = plan_tiles(width, height, cfg.tile_size, cfg.tile_overlap, cfg.start_scale)
        levels: dict[float, ImageBGR] = {}
        boxes: list[tuple[float, float, float, float]] = []
        marks: list[NDArray[np.float32]] = []
        scores: list[float] = []
        for tile in tiles:
            level = levels.get(tile.scale)
            if level is None:
                level = _resize(image, tile)
                levels = {tile.scale: level}  # keep one level in memory at a time
            for box, pts, score in self._detect_tile(level, tile):
                boxes.append(box)
                marks.append(pts)
                scores.append(score)
        if not boxes:
            return []
        box_arr = np.array(boxes, dtype=np.float32)
        score_arr = np.array(scores, dtype=np.float32)
        faces = []
        for i in nms(box_arr, score_arr, cfg.nms_iou):
            x1, y1, x2, y2 = (float(v) for v in box_arr[i])
            x1, y1 = max(0.0, x1), max(0.0, y1)
            x2, y2 = min(float(width), x2), min(float(height), y2)
            bbox = BBox(x1, y1, x2 - x1, y2 - y1)
            if bbox.short_side < cfg.min_face_px:
                continue
            pts = marks[i]
            landmarks = (
                (float(pts[0, 0]), float(pts[0, 1])),
                (float(pts[1, 0]), float(pts[1, 1])),
                (float(pts[2, 0]), float(pts[2, 1])),
                (float(pts[3, 0]), float(pts[3, 1])),
                (float(pts[4, 0]), float(pts[4, 1])),
            )
            faces.append(Face(bbox=bbox, landmarks=landmarks, score=float(score_arr[i])))
        return faces

    def _detect_tile(
        self, level: ImageBGR, tile: Tile
    ) -> list[tuple[tuple[float, float, float, float], NDArray[np.float32], float]]:
        """
        Purpose: run YuNet on one tile and map results to original-image coordinates.
        Outputs: (box x1,y1,x2,y2, landmarks 5x2, score) per kept detection. Side effects: none.
        """
        size = self.config.tile_size
        crop = level[tile.y : tile.y + tile.height, tile.x : tile.x + tile.width]
        padded = np.zeros((size, size, 3), dtype=np.float32)
        padded[: tile.height, : tile.width] = crop
        blob = padded.transpose(2, 0, 1)[None]
        raw = self._session.run(self._output_names, {self._input_name: blob})
        outputs = {
            name: np.asarray(value, dtype=np.float32)
            for name, value in zip(self._output_names, raw, strict=True)
        }
        boxes, points, scores = decode_yunet(outputs, size, self.config.score_threshold)
        results = []
        for box, pts, score in zip(boxes, points, scores, strict=True):
            tile_box = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
            if touches_inner_edge(tile, tile_box, self.config.edge_margin):
                continue
            x1, y1 = to_original(tile, tile_box[0], tile_box[1])
            x2, y2 = to_original(tile, tile_box[2], tile_box[3])
            mapped = np.array(
                [to_original(tile, float(px), float(py)) for px, py in pts], dtype=np.float32
            )
            results.append(((x1, y1, x2, y2), mapped, float(score)))
        return results


def _resize(image: ImageBGR, tile: Tile) -> ImageBGR:
    """Purpose: the pyramid level for a tile's scale (INTER_AREA when shrinking)."""
    if math.isclose(tile.scale, 1.0):
        return image
    resized = cv2.resize(image, (tile.level_width, tile.level_height), interpolation=cv2.INTER_AREA)
    return np.asarray(resized, dtype=np.uint8)
