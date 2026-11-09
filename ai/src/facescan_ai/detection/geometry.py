"""Pure geometry for detection: non-maximum suppression, the tile pyramid and coordinate mapping.

No model code here, so everything is unit-testable with synthetic numbers.
"""

import math
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

BoxArray = NDArray[np.float32]  # N x 4 as x1, y1, x2, y2


@dataclass(frozen=True, slots=True)
class Tile:
    """A window of a (possibly downscaled) image fed to the detector.

    `scale` maps original pixels to this level (level = original * scale); (x, y) is the
    window's top-left at that level; width/height may be smaller than the network input
    at the right/bottom border (the rest is zero padding).
    """

    scale: float
    x: int
    y: int
    width: int
    height: int
    level_width: int
    level_height: int


def iou_matrix(a: BoxArray, b: BoxArray) -> NDArray[np.float32]:
    """Purpose: pairwise IoU. Inputs: N x 4 and M x 4 boxes. Outputs: N x M. No side effects."""
    x1 = np.maximum(a[:, None, 0], b[None, :, 0])
    y1 = np.maximum(a[:, None, 1], b[None, :, 1])
    x2 = np.minimum(a[:, None, 2], b[None, :, 2])
    y2 = np.minimum(a[:, None, 3], b[None, :, 3])
    inter = np.clip(x2 - x1, 0, None) * np.clip(y2 - y1, 0, None)
    area_a = (a[:, 2] - a[:, 0]) * (a[:, 3] - a[:, 1])
    area_b = (b[:, 2] - b[:, 0]) * (b[:, 3] - b[:, 1])
    union = area_a[:, None] + area_b[None, :] - inter
    return np.where(union > 0, inter / np.maximum(union, 1e-9), 0.0).astype(np.float32)


def nms(boxes: BoxArray, scores: NDArray[np.float32], iou_threshold: float) -> list[int]:
    """
    Purpose: greedy non-maximum suppression.
    Inputs: N x 4 boxes (x1, y1, x2, y2), N scores, IoU threshold.
    Outputs: kept indices, highest score first. Side effects: none.
    """
    if boxes.shape[0] == 0:
        return []
    order = list(np.argsort(-scores, kind="stable"))
    keep: list[int] = []
    while order:
        best = int(order.pop(0))
        keep.append(best)
        if not order:
            break
        rest = np.array(order)
        overlaps = iou_matrix(boxes[best : best + 1], boxes[rest])[0]
        order = [int(i) for i, o in zip(rest, overlaps, strict=True) if o <= iou_threshold]
    return keep


def plan_tiles(
    width: int, height: int, tile_size: int, overlap: int, start_scale: float = 1.0
) -> list[Tile]:
    """
    Purpose: tiles for an image pyramid. The finest level uses `start_scale` (<= 1); each next
        level halves the size, until the whole image fits one tile. Every level is covered by
        overlapping windows, so a face no larger than `overlap` at that level lies entirely
        inside at least one window; larger faces are whole at a coarser level.
    Inputs: image size, network input size, overlap in pixels (< tile_size), finest scale.
    Outputs: tiles, finest level first. Side effects: none.
    """
    if not 0 <= overlap < tile_size:
        raise ValueError("overlap must be in [0, tile_size)")
    if width <= 0 or height <= 0:
        raise ValueError("image size must be positive")
    if not 0 < start_scale <= 1:
        raise ValueError("start_scale must be in (0, 1]")
    tiles: list[Tile] = []
    fit_scale = min(1.0, tile_size / max(width, height))
    scale = start_scale
    while True:
        if scale <= fit_scale * 1.0001:
            scale = fit_scale
        lw = max(1, round(width * scale))
        lh = max(1, round(height * scale))
        stride = tile_size - overlap
        for y in _starts(lh, tile_size, stride):
            for x in _starts(lw, tile_size, stride):
                tiles.append(
                    Tile(scale, x, y, min(tile_size, lw - x), min(tile_size, lh - y), lw, lh)
                )
        if scale == fit_scale:
            return tiles
        scale /= 2.0


def _starts(length: int, tile_size: int, stride: int) -> list[int]:
    """Purpose: window start offsets covering [0, length); the last window ends at the edge."""
    if length <= tile_size:
        return [0]
    count = math.ceil((length - tile_size) / stride) + 1
    starts = [min(i * stride, length - tile_size) for i in range(count)]
    return sorted(set(starts))


def to_original(tile: Tile, x: float, y: float) -> tuple[float, float]:
    """Purpose: map a point from tile coordinates to original-image pixels."""
    return (tile.x + x) / tile.scale, (tile.y + y) / tile.scale


def touches_inner_edge(tile: Tile, box: tuple[float, float, float, float], margin: float) -> bool:
    """
    Purpose: True if a box (tile coordinates) touches a tile edge that is not an image edge.
        Such a face may be cut off; the overlapping neighbour or a coarser level has it whole.
    Side effects: none.
    """
    x1, y1, x2, y2 = box
    left_inner = tile.x > 0
    top_inner = tile.y > 0
    right_inner = tile.x + tile.width < tile.level_width
    bottom_inner = tile.y + tile.height < tile.level_height
    return (
        (left_inner and x1 <= margin)
        or (top_inner and y1 <= margin)
        or (right_inner and x2 >= tile.width - margin)
        or (bottom_inner and y2 >= tile.height - margin)
    )
