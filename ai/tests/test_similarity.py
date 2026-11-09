import numpy as np
import pytest
from facescan_ai.embedding.base import cosine_similarity, cosine_similarity_matrix, l2_normalise


def v(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


def test_cosine_basic_cases() -> None:
    assert cosine_similarity(v(1, 0), v(1, 0)) == pytest.approx(1.0)
    assert cosine_similarity(v(1, 0), v(0, 1)) == pytest.approx(0.0)
    assert cosine_similarity(v(1, 0), v(-1, 0)) == pytest.approx(-1.0)
    assert cosine_similarity(v(3, 4), v(6, 8)) == pytest.approx(1.0)  # scale-invariant


def test_cosine_rejects_bad_input() -> None:
    with pytest.raises(ValueError):
        cosine_similarity(v(1, 0), v(1, 0, 0))
    with pytest.raises(ValueError):
        cosine_similarity(v(0, 0), v(1, 0))


def test_matrix_matches_pairwise_and_handles_empty() -> None:
    rng = np.random.default_rng(1)
    q = rng.normal(size=8).astype(np.float32)
    g = rng.normal(size=(5, 8)).astype(np.float32)
    expected = [cosine_similarity(q, row) for row in g]
    assert cosine_similarity_matrix(q, g).tolist() == pytest.approx(expected, abs=1e-6)
    assert cosine_similarity_matrix(q, np.zeros((0, 8), np.float32)).shape == (0,)


def test_l2_normalise() -> None:
    out = l2_normalise(v(3, 4))
    assert out.dtype == np.float32
    assert float(np.linalg.norm(out)) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        l2_normalise(v(0, 0))
    with pytest.raises(ValueError):
        l2_normalise(v(np.nan, 1))
