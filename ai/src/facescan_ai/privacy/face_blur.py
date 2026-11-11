"""Irreversibly blurs every detected face, with tiling, lower threshold and generous margin.

"When unsure, blur": callers use a detector configured with a low score threshold and a small
minimum face size, so borderline detections are blurred too.
For candidate previews: detects faces with tiling at threshold 0.35, enlarges each box by 25%,
blurs strongly, and downsizes to a low-resolution web copy (default max 480 px, JPEG quality 85).
"""

from typing import Protocol

import cv2
import numpy as np

from facescan_ai.types import BBox, Face, ImageBGR, Landmarks

DEFAULT_MARGIN = 0.40
PREVIEW_MARGIN = 0.25
_PIXEL_BLOCKS = 8


class FaceDetector(Protocol):
    """Protocol for detectors supporting detect(image, min_score)."""

    def detect(self, image: ImageBGR) -> list[Face]: ...


def blur_faces(image: ImageBGR, faces: list[Face], margin: float = DEFAULT_MARGIN) -> ImageBGR:
    """
    Purpose: return a copy of the image with every face region destroyed.
    Inputs: BGR image, faces (from a permissive detector), margin >= 0.
    Outputs: new BGR image. Side effects: none (the input is not modified).
    """
    if margin < 0:
        raise ValueError("margin must be >= 0")
    out = image.copy()
    height, width = out.shape[:2]
    for face in faces:
        box = face.bbox
        pad_x, pad_y = box.width * margin, box.height * margin
        x1 = max(0, int(np.floor(box.x - pad_x)))
        y1 = max(0, int(np.floor(box.y - pad_y)))
        x2 = min(width, int(np.ceil(box.x2 + pad_x)))
        y2 = min(height, int(np.ceil(box.y2 + pad_y)))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        region = out[y1:y2, x1:x2]
        small = cv2.resize(region, (_PIXEL_BLOCKS, _PIXEL_BLOCKS), interpolation=cv2.INTER_AREA)
        pixelated = cv2.resize(small, (x2 - x1, y2 - y1), interpolation=cv2.INTER_NEAREST)
        kernel = max(7, ((min(x2 - x1, y2 - y1) // 3) | 1))
        out[y1:y2, x1:x2] = cv2.GaussianBlur(pixelated, (kernel, kernel), 0)
    return out


def _nms_faces(faces: list[Face], iou_threshold: float = 0.4) -> list[Face]:
    """Purpose: combine overlapping detections from tiling with Non-Maximum Suppression."""
    if not faces:
        return []
    boxes = [f.bbox for f in faces]
    scores = [f.score for f in faces]
    indices = cv2.dnn.NMSBoxes(
        bboxes=[[int(b.x), int(b.y), int(b.width), int(b.height)] for b in boxes],
        scores=scores,
        score_threshold=0.0,
        nms_threshold=iou_threshold,
    )
    if len(indices) == 0:
        return []
    flat_indices = np.asarray(indices).reshape(-1)
    return [faces[int(i)] for i in flat_indices]


def detect_faces_with_tiling(
    image: ImageBGR,
    detector: FaceDetector,
) -> list[Face]:
    """
    Purpose: detect faces at full image resolution plus 2x2 overlapping tiles
        to guarantee small and side-angle faces are caught.
    Inputs: BGR image, detector.
    Outputs: list of Face detections mapped back to full image coordinates.
    Side effects: none.
    """
    h, w = image.shape[:2]
    all_faces: list[Face] = list(detector.detect(image))

    # If image is large enough, run 2x2 overlapping sub-tiles (each ~60% of W and H)
    if min(h, w) >= 300:
        tile_w = int(w * 0.60)
        tile_h = int(h * 0.60)
        offsets = [
            (0, 0),
            (w - tile_w, 0),
            (0, h - tile_h),
            (w - tile_w, h - tile_h),
        ]
        for ox, oy in offsets:
            tile = image[oy : oy + tile_h, ox : ox + tile_w]
            tile_faces = detector.detect(tile)
            for f in tile_faces:
                b = f.bbox
                mapped_bbox = BBox(x=b.x + ox, y=b.y + oy, width=b.width, height=b.height)
                lm = f.landmarks
                mapped_landmarks: Landmarks = (
                    (float(lm[0][0] + ox), float(lm[0][1] + oy)),
                    (float(lm[1][0] + ox), float(lm[1][1] + oy)),
                    (float(lm[2][0] + ox), float(lm[2][1] + oy)),
                    (float(lm[3][0] + ox), float(lm[3][1] + oy)),
                    (float(lm[4][0] + ox), float(lm[4][1] + oy)),
                )
                all_faces.append(Face(bbox=mapped_bbox, landmarks=mapped_landmarks, score=f.score))

    return _nms_faces(all_faces)


def create_blurred_preview(
    image: ImageBGR,
    detector: FaceDetector,
    max_side: int = 480,
    jpeg_quality: int = 85,
    margin: float = PREVIEW_MARGIN,
) -> bytes:
    """
    Purpose: generate low-resolution scene search candidate preview with every face blurred.
    Inputs: BGR image, detector, max_side (default 480), jpeg_quality (85), margin (25%).
    Outputs: JPEG bytes (no unblurred face bytes).
    Side effects: none.
    """
    detected_faces = detect_faces_with_tiling(image, detector)
    blurred = blur_faces(image, detected_faces, margin=margin)

    # Downscale so longest side <= max_side
    h, w = blurred.shape[:2]
    longest = max(h, w)
    if longest > max_side:
        scale = max_side / float(longest)
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        resized: ImageBGR = np.asarray(
            cv2.resize(blurred, (new_w, new_h), interpolation=cv2.INTER_AREA),
            dtype=np.uint8,
        )
        blurred = resized

    ok, encoded = cv2.imencode(".jpg", blurred, [cv2.IMWRITE_JPEG_QUALITY, jpeg_quality])
    if not ok:
        raise RuntimeError("could not encode blurred preview")
    return bytes(encoded.tobytes())
