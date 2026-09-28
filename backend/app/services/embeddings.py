from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding

MODEL_NAME = "intfloat/multilingual-e5-small"
VECTOR_SIZE = 384


@lru_cache(maxsize=1)
def _model(cache_dir: str) -> TextEmbedding:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=cache_dir)


def embed_passages(texts: list[str], cache_dir: str) -> list[list[float]]:
    if not texts:
        return []
    vectors = _model(cache_dir).embed([f"passage: {text}" for text in texts], batch_size=32)
    return [vector.astype(float).tolist() for vector in vectors]


def embed_query(text: str, cache_dir: str) -> list[float]:
    vector = next(_model(cache_dir).embed([f"query: {text}"], batch_size=1))
    return vector.astype(float).tolist()
