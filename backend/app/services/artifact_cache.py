"""Small, bounded, content-addressed cache for reproducible document artifacts."""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from collections.abc import Iterable
from pathlib import Path
from typing import Any

CACHE_SCHEMA_VERSION = 1
MAX_CACHE_ENTRY_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = 512 * 1024 * 1024


def processing_cache_key(
    *,
    input_checksum: str,
    file_type: str,
    configuration: dict[str, Any],
    parser_version: str,
    converter_version: str,
) -> str:
    """Bind extraction, OCR, Markdown and source maps to every content input."""
    payload = {
        "schema": CACHE_SCHEMA_VERSION,
        "input_checksum": input_checksum,
        "file_type": file_type.lower(),
        "configuration": configuration,
        "parser_version": parser_version,
        "converter_version": converter_version,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def content_key(*, namespace: str, model: str, model_version: str, content: str) -> str:
    """Hash source text instead of storing it in a cache filename or index."""
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    encoded = f"{namespace}\0{model}\0{model_version}\0{digest}".encode()
    return hashlib.sha256(encoded).hexdigest()


def file_checksum(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while piece := stream.read(1024 * 1024):
            digest.update(piece)
    return digest.hexdigest()


def _cache_path(root: str | Path, namespace: str, key: str) -> Path:
    if not namespace.isascii() or not namespace.replace("-", "").isalnum():
        raise ValueError("Cache namespace must be a stable ASCII identifier")
    if len(key) != 64 or any(character not in "0123456789abcdef" for character in key):
        raise ValueError("Cache key must be a SHA-256 digest")
    directory = Path(root).resolve()
    return directory / f".{namespace}-cache-{key}.json"


def load_json_cache(root: str | Path, namespace: str, key: str) -> dict[str, Any] | None:
    path = _cache_path(root, namespace, key)
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_CACHE_ENTRY_BYTES:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(value, dict) or value.get("schema") != CACHE_SCHEMA_VERSION
                or value.get("key") != key or not isinstance(value.get("payload"), dict)):
            return None
        os.utime(path, None)
        return value["payload"]
    except (OSError, UnicodeError, ValueError, TypeError):
        return None


def store_json_cache(
    root: str | Path,
    namespace: str,
    key: str,
    payload: dict[str, Any],
) -> bool:
    """Write atomically; malformed, oversized, or full-cache entries are skipped."""
    return store_json_cache_batch(root, namespace, [(key, payload)]) == 1


def store_json_cache_batch(
    root: str | Path,
    namespace: str,
    items: Iterable[tuple[str, dict[str, Any]]],
) -> int:
    """Store a small artifact batch and prune once instead of rescanning per item."""
    directory = Path(root).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stored = 0
    keep: Path | None = None
    for key, payload in items:
        path = _cache_path(directory, namespace, key)
        encoded = json.dumps(
            {"schema": CACHE_SCHEMA_VERSION, "key": key, "payload": payload},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_CACHE_ENTRY_BYTES:
            continue
        descriptor, temporary = tempfile.mkstemp(prefix=f".{namespace}-cache-tmp-", dir=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encoded)
                stream.flush()
                os.fsync(stream.fileno())
            if path.is_symlink():
                continue
            os.replace(temporary, path)
            keep = path
            stored += 1
        finally:
            Path(temporary).unlink(missing_ok=True)
    if keep is not None:
        _prune_cache(directory, keep=keep)
    return stored


def _prune_cache(directory: Path, *, keep: Path) -> None:
    entries: list[tuple[float, Path, int]] = []
    total = 0
    try:
        for path in directory.iterdir():
            if path == keep or not path.name.startswith(".") or "-cache-" not in path.name or not path.name.endswith(".json"):
                continue
            try:
                info = path.lstat()
                if not path.is_symlink() and path.is_file():
                    entries.append((info.st_mtime, path, info.st_size))
                    total += info.st_size
            except OSError:
                continue
        keep_size = keep.stat().st_size
        total += keep_size
        for _modified, path, size in sorted(entries):
            if total <= MAX_CACHE_BYTES:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                continue
    except OSError:
        # A cache is an optimization. Filesystem cleanup must not block processing.
        return
