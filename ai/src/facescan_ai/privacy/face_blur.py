"""Irreversibly blurs every detected face, with a generous margin.

"When unsure, blur": callers use a detector configured with a low score threshold and a small
minimum face size (config.BLUR_DETECTOR_CONFIG), so borderline detections are blurred too.
Each face region is pixelated and then Gaussian-blurred, which cannot be undone.
"""

import cv2
import numpy as np

from facescan_ai.types import Face, ImageBGR

DEFAULT_MARGIN = 0.4  # fraction of the box size added on every side
_PIXEL_BLOCKS = 8


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
        kernel = max(3, ((min(x2 - x1, y2 - y1) // 4) | 1))
        out[y1:y2, x1:x2] = cv2.GaussianBlur(pixelated, (kernel, kernel), 0)
    return out
