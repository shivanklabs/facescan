from facescan_ai.types import Embedding


def test_embedding_repr_hides_values() -> None:
    emb = Embedding(values=(0.1, 0.2), model_name="auraface", model_version="v1")
    assert "0.1" not in repr(emb)
