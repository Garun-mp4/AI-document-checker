"""Small process-local LRU for already-built document preview responses."""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from threading import RLock
from typing import Any

PREVIEW_CACHE_VERSION = "document-preview-m14-v1"
TABLE_PREVIEW_CACHE_VERSION = "table-preview-m14-v1"
MAX_PREVIEW_CACHE_ENTRIES = 16
MAX_PREVIEW_CACHE_BYTES = 16 * 1024 * 1024


def preview_cache_key(
    *,
    document_id: str,
    input_checksum: str | None,
    storage_path: str,
    file_type: str,
    processing_version: int,
    chunk_count: int,
    metadata: dict[str, Any],
) -> tuple[str, str, str, int, int, str]:
    metadata_hash = hashlib.sha256(
        json.dumps(metadata, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    ).hexdigest()
    return (
        document_id,
        input_checksum or storage_path,
        file_type.lower(),
        processing_version,
        chunk_count,
        f"{PREVIEW_CACHE_VERSION}:{metadata_hash}",
    )


def table_preview_cache_key(
    *,
    document_id: str,
    input_checksum: str | None,
    storage_path: str,
    file_type: str,
    offset: int,
    limit: int,
    sheet: str | None,
    sort_column: int | None,
    sort_direction: str,
    filter_column: int | None,
    filter_kind: str | None,
    filter_operator: str | None,
    filter_value: str | None,
    focus_row: int | None,
) -> tuple[Any, ...]:
    """Bind a virtualized table page to its source and every query parameter."""
    return (
        document_id,
        input_checksum or storage_path,
        file_type.lower(),
        TABLE_PREVIEW_CACHE_VERSION,
        offset,
        limit,
        sheet,
        sort_column,
        sort_direction,
        filter_column,
        filter_kind,
        filter_operator,
        filter_value,
        focus_row,
    )


def table_search_cache_key(
    *,
    document_id: str,
    input_checksum: str,
    file_type: str,
    processing_version: int,
    query: str,
    offset: int,
    limit: int,
) -> tuple[Any, ...]:
    """Keep table-search pages isolated by source, analysis version and query."""
    return (
        document_id,
        input_checksum,
        file_type.lower(),
        processing_version,
        f"{TABLE_PREVIEW_CACHE_VERSION}:search",
        query,
        offset,
        limit,
    )


class PreviewResponseCache:
    """Cache immutable JSON snapshots, bounded by both entry count and bytes."""

    def __init__(self, *, max_entries: int = MAX_PREVIEW_CACHE_ENTRIES, max_bytes: int = MAX_PREVIEW_CACHE_BYTES) -> None:
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._entries: OrderedDict[tuple[Any, ...], tuple[str, int]] = OrderedDict()
        self._bytes = 0
        self._lock = RLock()

    def get(self, key: tuple[Any, ...]) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            self._entries.move_to_end(key)
            snapshot, _weight = entry
        try:
            value = json.loads(snapshot)
        except (ValueError, TypeError):
            self.remove(key)
            return None
        return value if isinstance(value, dict) else None

    def put(self, key: tuple[Any, ...], payload: dict[str, Any]) -> None:
        snapshot = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        weight = len(snapshot.encode("utf-8"))
        with self._lock:
            previous = self._entries.pop(key, None)
            if previous is not None:
                self._bytes -= previous[1]
            if weight > self.max_bytes or self.max_entries < 1:
                return
            while self._entries and (len(self._entries) >= self.max_entries or self._bytes + weight > self.max_bytes):
                _old_key, (_old_snapshot, old_weight) = self._entries.popitem(last=False)
                self._bytes -= old_weight
            self._entries[key] = (snapshot, weight)
            self._bytes += weight

    def remove(self, key: tuple[Any, ...]) -> None:
        with self._lock:
            entry = self._entries.pop(key, None)
            if entry is not None:
                self._bytes -= entry[1]

    def remove_document(self, document_id: str) -> None:
        with self._lock:
            for key in [key for key in self._entries if key and key[0] == document_id]:
                _snapshot, weight = self._entries.pop(key)
                self._bytes -= weight

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._bytes = 0


preview_response_cache = PreviewResponseCache()
table_response_cache = PreviewResponseCache(max_entries=16, max_bytes=16 * 1024 * 1024)
table_search_response_cache = PreviewResponseCache(max_entries=32, max_bytes=8 * 1024 * 1024)
