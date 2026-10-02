from __future__ import annotations

from app.services.preview_cache import (
    PreviewResponseCache,
    preview_cache_key,
    table_preview_cache_key,
    table_search_cache_key,
)


def make_key(*, version: int = 1, metadata: dict | None = None):
    return preview_cache_key(
        document_id="document-1",
        input_checksum="a" * 64,
        storage_path="/uploads/document-1/source.pdf",
        file_type="pdf",
        processing_version=version,
        chunk_count=2,
        metadata=metadata or {"page_count": 1},
    )


def test_preview_cache_reuses_immutable_snapshot_and_invalidates_on_document_version() -> None:
    cache = PreviewResponseCache(max_entries=4, max_bytes=4096)
    key = make_key()
    cache.put(key, {"blocks": [{"text": "original"}]})

    returned = cache.get(key)
    assert returned == {"blocks": [{"text": "original"}]}
    returned["blocks"][0]["text"] = "mutated"
    assert cache.get(key) == {"blocks": [{"text": "original"}]}
    assert cache.get(make_key(version=2)) is None
    assert cache.get(make_key(metadata={"page_count": 2})) is None


def test_preview_cache_evicts_least_recently_used_entry_to_stay_bounded() -> None:
    cache = PreviewResponseCache(max_entries=2, max_bytes=4096)
    first = make_key()
    second = preview_cache_key(
        document_id="document-2", input_checksum="b" * 64, storage_path="/uploads/2.pdf",
        file_type="pdf", processing_version=1, chunk_count=1, metadata={},
    )
    third = preview_cache_key(
        document_id="document-3", input_checksum="c" * 64, storage_path="/uploads/3.pdf",
        file_type="pdf", processing_version=1, chunk_count=1, metadata={},
    )
    cache.put(first, {"value": 1})
    cache.put(second, {"value": 2})
    assert cache.get(first) == {"value": 1}
    cache.put(third, {"value": 3})

    assert cache.get(second) is None
    assert cache.get(first) == {"value": 1}
    assert cache.get(third) == {"value": 3}


def test_preview_cache_removes_all_entries_for_deleted_document() -> None:
    cache = PreviewResponseCache(max_entries=4, max_bytes=4096)
    first = make_key()
    second = preview_cache_key(
        document_id="document-1", input_checksum="a" * 64, storage_path="/uploads/document-1/source.pdf",
        file_type="pdf", processing_version=2, chunk_count=3, metadata={"ocr": "updated"},
    )
    cache.put(first, {"value": 1})
    cache.put(second, {"value": 2})
    cache.remove_document("document-1")

    assert cache.get(first) is None
    assert cache.get(second) is None


def make_table_key(**overrides):
    values = {
        "document_id": "table-1",
        "input_checksum": "a" * 64,
        "storage_path": "/uploads/table-1.csv",
        "file_type": "csv",
        "offset": 0,
        "limit": 100,
        "sheet": None,
        "sort_column": None,
        "sort_direction": "asc",
        "filter_column": None,
        "filter_kind": None,
        "filter_operator": None,
        "filter_value": None,
        "focus_row": None,
    }
    values.update(overrides)
    return table_preview_cache_key(**values)


def test_table_preview_cache_key_includes_source_and_all_query_parameters():
    key = make_table_key()

    assert key == make_table_key()
    assert key != make_table_key(input_checksum="b" * 64)
    assert key != make_table_key(offset=100)
    assert key != make_table_key(limit=50)
    assert key != make_table_key(sheet="Archive")
    assert key != make_table_key(sort_column=2, sort_direction="desc")
    assert key != make_table_key(filter_column=1, filter_kind="text", filter_operator="contains", filter_value="ready")
    assert key != make_table_key(focus_row=240)


def test_table_search_cache_key_isolates_version_query_and_page():
    values = {
        "document_id": "table-1",
        "input_checksum": "a" * 64,
        "file_type": "csv",
        "processing_version": 3,
        "query": "revenue",
        "offset": 0,
        "limit": 50,
    }
    key = table_search_cache_key(**values)

    assert key == table_search_cache_key(**values)
    assert key != table_search_cache_key(**{**values, "input_checksum": "b" * 64})
    assert key != table_search_cache_key(**{**values, "processing_version": 4})
    assert key != table_search_cache_key(**{**values, "query": "profit"})
    assert key != table_search_cache_key(**{**values, "offset": 50})
