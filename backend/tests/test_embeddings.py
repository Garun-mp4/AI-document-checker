from __future__ import annotations

from collections.abc import Iterable

import pytest
from fastembed import TextEmbedding

from app.services import embeddings
from app.services.embeddings import MODEL_NAME, VECTOR_SIZE, EmbeddingConfigurationError


def test_configured_embedding_model_is_supported_and_matches_vector_size() -> None:
    model = next(
        (item for item in TextEmbedding.list_supported_models() if item["model"] == MODEL_NAME),
        None,
    )

    assert model is not None, f"{MODEL_NAME} is not in the installed FastEmbed model registry"
    assert model["dim"] == VECTOR_SIZE


class FakeVector(list[float]):
    def astype(self, _dtype: type[float]) -> FakeVector:
        return self

    def tolist(self) -> list[float]:
        return list(self)


class FakeEmbedding:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], int]] = []

    def embed(self, texts: Iterable[str], batch_size: int) -> Iterable[FakeVector]:
        values = list(texts)
        self.calls.append((values, batch_size))
        return iter(FakeVector([0.25] * VECTOR_SIZE) for _ in values)


def test_passage_embedding_uses_plain_text_and_expected_dimension(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake = FakeEmbedding()
    monkeypatch.setattr(embeddings, "_model", lambda _cache_dir: fake)

    vectors = embeddings.embed_passages(["Фрагмент документа"], str(tmp_path))

    assert fake.calls == [(["Фрагмент документа"], 32)]
    assert len(vectors) == 1
    assert len(vectors[0]) == VECTOR_SIZE


def test_query_embedding_uses_plain_text_and_expected_dimension(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    fake = FakeEmbedding()
    monkeypatch.setattr(embeddings, "_model", lambda _cache_dir: fake)

    vector = embeddings.embed_query("вопрос по документу", str(tmp_path))

    assert fake.calls == [(["вопрос по документу"], 1)]
    assert len(vector) == VECTOR_SIZE


def test_unsupported_embedding_model_fails_with_actionable_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class NoModels:
        @classmethod
        def list_supported_models(cls) -> list[dict[str, object]]:
            return []

        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("Unsupported models must be rejected before model construction")

    monkeypatch.setattr(embeddings, "MODEL_NAME", "unknown/model")
    monkeypatch.setattr(embeddings, "TextEmbedding", NoModels)
    embeddings._model.cache_clear()
    try:
        with pytest.raises(EmbeddingConfigurationError, match="отсутствует в реестре"):
            embeddings._model(str(tmp_path))
    finally:
        embeddings._model.cache_clear()


def test_embedding_dimension_mismatch_is_reported_before_download(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    class WrongDimension:
        @classmethod
        def list_supported_models(cls) -> list[dict[str, object]]:
            return [{"model": MODEL_NAME, "dim": 768}]

        def __init__(self, **_kwargs: object) -> None:
            raise AssertionError("Dimension mismatch must be rejected before model construction")

    monkeypatch.setattr(embeddings, "TextEmbedding", WrongDimension)
    embeddings._model.cache_clear()
    try:
        with pytest.raises(EmbeddingConfigurationError, match="размерности 768"):
            embeddings._model(str(tmp_path))
    finally:
        embeddings._model.cache_clear()
