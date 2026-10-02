from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding

from app.services.artifact_cache import (
    content_key,
    load_json_cache,
    store_json_cache_batch,
)

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_SIZE = 384
EMBEDDING_CACHE_VERSION = "fastembed-0.8.1-v1"


class EmbeddingConfigurationError(RuntimeError):
    """The configured embedding model cannot be used with the installed FastEmbed registry."""


@lru_cache(maxsize=1)
def _model(cache_dir: str) -> TextEmbedding:
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    model_info = next(
        (item for item in TextEmbedding.list_supported_models() if item["model"] == MODEL_NAME),
        None,
    )
    if model_info is None:
        raise EmbeddingConfigurationError(
            f"Локальная модель эмбеддингов «{MODEL_NAME}» отсутствует в реестре установленного FastEmbed. "
            "Проверьте конфигурацию модели и список поддерживаемых моделей."
        )
    if model_info["dim"] != VECTOR_SIZE:
        raise EmbeddingConfigurationError(
            f"Модель эмбеддингов «{MODEL_NAME}» создаёт векторы размерности {model_info['dim']}, "
            f"а база данных настроена на {VECTOR_SIZE}."
        )
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=cache_dir)


def embed_passages(texts: list[str], cache_dir: str) -> list[list[float]]:
    if not texts:
        return []
    results: list[list[float] | None] = [None] * len(texts)
    missing: dict[str, tuple[str, list[int]]] = {}
    for index, text in enumerate(texts):
        key = content_key(
            namespace="passage",
            model=MODEL_NAME,
            model_version=EMBEDDING_CACHE_VERSION,
            content=text,
        )
        cached = load_json_cache(cache_dir, "embedding", key)
        vector = cached.get("vector") if cached else None
        if _valid_vector(vector):
            results[index] = vector
        elif key in missing:
            missing[key][1].append(index)
        else:
            missing[key] = (text, [index])

    if missing:
        missing_items = list(missing.items())
        vectors = _model(cache_dir).embed([value[0] for _, value in missing_items], batch_size=32)
        cache_items: list[tuple[str, dict[str, list[float]]]] = []
        for (key, (_text, indexes)), vector in zip(missing_items, vectors, strict=True):
            normalized = vector.astype(float).tolist()
            if not _valid_vector(normalized):
                raise EmbeddingConfigurationError("Локальная модель эмбеддингов вернула вектор неверной размерности.")
            cache_items.append((key, {"vector": normalized}))
            for index in indexes:
                results[index] = normalized
        store_json_cache_batch(cache_dir, "embedding", cache_items)

    # Every slot is filled from either a validated cache item or a model result.
    return [vector for vector in results if vector is not None]


def _valid_vector(value: object) -> bool:
    return (
        isinstance(value, list)
        and len(value) == VECTOR_SIZE
        and all(isinstance(item, (int, float)) and not isinstance(item, bool) and math.isfinite(item) for item in value)
    )


def embed_query(text: str, cache_dir: str) -> list[float]:
    vector = next(_model(cache_dir).embed([text], batch_size=1))
    return vector.astype(float).tolist()
