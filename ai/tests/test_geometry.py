import numpy as np
import pytest
from facescan_ai.face_detection.geometry import (
    Tile,
    iou_matrix,
    nms,
    plan_tiles,
    to_original,
    touches_inner_edge,
)


def boxes(*rows: tuple[float, float, float, float]) -> np.ndarray:
    return np.array(rows, dtype=np.float32)


def test_iou_identical_disjoint_and_half() -> None:
    m = iou_matrix(boxes((0, 0, 10, 10)), boxes((0, 0, 10, 10), (20, 20, 30, 30), (5, 0, 15, 10)))
    assert m[0, 0] == pytest.approx(1.0)
    assert m[0, 1] == pytest.approx(0.0)
    assert m[0, 2] == pytest.approx(50 / 150)


def test_nms_keeps_best_of_overlapping_and_all_disjoint() -> None:
    b = boxes((0, 0, 10, 10), (1, 1, 11, 11), (50, 50, 60, 60), (51, 50, 61, 60))
    s = np.array([0.6, 0.9, 0.5, 0.8], dtype=np.float32)
    assert nms(b, s, 0.3) == [1, 3]


def test_nms_empty_and_threshold_edge() -> None:
    assert nms(np.zeros((0, 4), np.float32), np.zeros((0,), np.float32), 0.5) == []
    b = boxes((0, 0, 10, 10), (5, 0, 15, 10))  # IoU 1/3
    s = np.array([0.9, 0.8], dtype=np.float32)
    assert nms(b, s, 0.34) == [0, 1]
    assert nms(b, s, 0.33) == [0]


def test_small_image_is_one_tile_at_scale_one() -> None:
    tiles = plan_tiles(500, 300, 640, 192)
    assert tiles == [Tile(1.0, 0, 0, 500, 300, 500, 300)]


def test_pyramid_ends_with_a_single_fitting_tile() -> None:
    tiles = plan_tiles(4000, 3000, 640, 192)
    scales = sorted({t.scale for t in tiles}, reverse=True)
    assert scales[0] == 1.0
    assert scales[-1] == pytest.approx(640 / 4000)
    assert [t for t in tiles if t.scale == scales[-1]] == [
        Tile(scales[-1], 0, 0, 640, 480, 640, 480)
    ]
    for a, b in zip(scales, scales[1:], strict=False):
        assert b >= a / 2 - 1e-9  # no gap bigger than a factor of 2


def test_start_scale_skips_finer_levels() -> None:
    tiles = plan_tiles(4000, 3000, 640, 192, start_scale=0.5)
    assert max(t.scale for t in tiles) == 0.5


@pytest.mark.parametrize(("width", "height"), [(1500, 1000), (4000, 3000), (641, 2000)])
def test_every_small_face_fits_whole_in_some_tile(width: int, height: int) -> None:
    overlap = 192
    tiles = [t for t in plan_tiles(width, height, 640, overlap) if t.scale == 1.0]
    rng = np.random.default_rng(0)
    for _ in range(300):
        size = int(rng.integers(8, overlap + 1))
        x = int(rng.integers(0, width - size))
        y = int(rng.integers(0, height - size))
        assert any(
            t.x <= x and t.y <= y and x + size <= t.x + t.width and y + size <= t.y + t.height
            for t in tiles
        )


def test_tiles_stay_inside_the_level() -> None:
    for t in plan_tiles(3001, 1999, 640, 192):
        assert t.x >= 0 and t.y >= 0
        assert t.x + t.width <= t.level_width
        assert t.y + t.height <= t.level_height


def test_to_original_maps_offset_and_scale() -> None:
    tile = Tile(scale=0.5, x=448, y=0, width=640, height=640, level_width=2000, level_height=1500)
    assert to_original(tile, 10, 20) == (916.0, 40.0)
    assert to_original(Tile(1.0, 0, 0, 100, 100, 100, 100), 3.5, 4.5) == (3.5, 4.5)


def test_touches_inner_edge_only_for_inner_edges() -> None:
    middle = Tile(1.0, 448, 448, 640, 640, 2000, 2000)
    corner = Tile(1.0, 0, 0, 640, 640, 2000, 2000)
    near_left = (1.0, 100.0, 50.0, 150.0)
    near_right = (600.0, 100.0, 639.5, 150.0)
    centre = (300.0, 300.0, 340.0, 340.0)
    assert touches_inner_edge(middle, near_left, 2.0)
    assert not touches_inner_edge(corner, near_left, 2.0)  # left edge is the image edge
    assert touches_inner_edge(corner, near_right, 2.0)
    assert not touches_inner_edge(middle, centre, 2.0)


def test_plan_tiles_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        plan_tiles(100, 100, 640, 640)
    with pytest.raises(ValueError):
        plan_tiles(0, 100, 640, 192)
    with pytest.raises(ValueError):
        plan_tiles(100, 100, 640, 192, start_scale=0)
