from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastembed import TextEmbedding

MODEL_NAME = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
VECTOR_SIZE = 384


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
    vectors = _model(cache_dir).embed(texts, batch_size=32)
    return [vector.astype(float).tolist() for vector in vectors]


def embed_query(text: str, cache_dir: str) -> list[float]:
    vector = next(_model(cache_dir).embed([text], batch_size=1))
    return vector.astype(float).tolist()
