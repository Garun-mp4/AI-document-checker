from __future__ import annotations

import json

from app.services.artifact_cache import (
    content_key,
    load_json_cache,
    processing_cache_key,
    store_json_cache,
    store_json_cache_batch,
)


def test_processing_cache_key_changes_with_document_and_pipeline_configuration() -> None:
    common = {
        "input_checksum": "a" * 64,
        "file_type": "pdf",
        "configuration": {"ocr_languages": "rus+eng", "ocr_dpi": 200},
        "parser_version": "parser-v1",
        "converter_version": "0.1.8",
    }

    key = processing_cache_key(**common)

    assert key == processing_cache_key(**common)
    assert key != processing_cache_key(**{**common, "input_checksum": "b" * 64})
    assert key != processing_cache_key(**{**common, "configuration": {"ocr_languages": "eng", "ocr_dpi": 200}})
    assert key != processing_cache_key(**{**common, "parser_version": "parser-v2"})
    assert key != processing_cache_key(**{**common, "converter_version": "0.1.9"})


def test_cache_round_trip_and_corruption_are_safe_misses(tmp_path) -> None:
    key = "c" * 64
    payload = {"markdown": "# Synthetic", "sources": [{"page": 1}]}

    assert store_json_cache(tmp_path, "processing", key, payload)
    assert load_json_cache(tmp_path, "processing", key) == payload

    path = tmp_path / f".processing-cache-{key}.json"
    path.write_text("not json", encoding="utf-8")
    assert load_json_cache(tmp_path, "processing", key) is None


def test_cache_rejects_a_payload_with_a_different_key_or_schema(tmp_path) -> None:
    key = "d" * 64
    assert store_json_cache(tmp_path, "processing", key, {"value": 1})
    path = tmp_path / f".processing-cache-{key}.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    value["key"] = "e" * 64
    path.write_text(json.dumps(value), encoding="utf-8")
    assert load_json_cache(tmp_path, "processing", key) is None


def test_content_key_uses_model_version_and_hides_source_text() -> None:
    key = content_key(namespace="passage", model="model-a", model_version="v1", content="private sentence")

    assert len(key) == 64
    assert "private" not in key
    assert key != content_key(namespace="passage", model="model-a", model_version="v2", content="private sentence")
    assert key != content_key(namespace="passage", model="model-b", model_version="v1", content="private sentence")


def test_cache_byte_budget_is_shared_across_artifact_namespaces(monkeypatch, tmp_path):
    import app.services.artifact_cache as cache_module

    monkeypatch.setattr(cache_module, "MAX_CACHE_BYTES", 430)
    first_key, second_key, third_key = "1" * 64, "2" * 64, "3" * 64

    assert store_json_cache(tmp_path, "parsed", first_key, {"value": "a" * 100})
    assert store_json_cache(tmp_path, "markdown", second_key, {"value": "b" * 100})
    assert load_json_cache(tmp_path, "markdown", second_key) is not None
    assert store_json_cache(tmp_path, "mapping", third_key, {"value": "c" * 100})

    assert load_json_cache(tmp_path, "parsed", first_key) is None
    assert load_json_cache(tmp_path, "markdown", second_key) is not None
    assert load_json_cache(tmp_path, "mapping", third_key) is not None
    cached_bytes = sum(path.stat().st_size for path in tmp_path.glob(".*-cache-*.json"))
    assert cached_bytes <= cache_module.MAX_CACHE_BYTES


def test_cache_batch_stores_multiple_entries_and_prunes_once(tmp_path):
    items = [(f"{number:x}" * 64, {"value": number}) for number in (10, 11, 12)]

    assert store_json_cache_batch(tmp_path, "embedding", items) == 3
    assert [load_json_cache(tmp_path, "embedding", key) for key, _payload in items] == [
        {"value": 10},
        {"value": 11},
        {"value": 12},
    ]
