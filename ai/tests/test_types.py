import numpy as np
from facescan_ai.types import BBox, Embedding


def test_embedding_repr_hides_values() -> None:
    vector = np.array([0.123456, 0.654321], dtype=np.float32)
    emb = Embedding(vector=vector, engine="auraface", engine_version="v1")
    text = repr(emb) + str(emb)
    assert "0.12" not in text
    assert "0.65" not in text
    assert "auraface@v1" in text
    assert emb.dim == 2


def test_bbox_helpers() -> None:
    box = BBox(10, 20, 30, 40)
    assert (box.x2, box.y2, box.short_side) == (40, 60, 30)
