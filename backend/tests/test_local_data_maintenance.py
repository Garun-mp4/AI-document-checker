from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api
from app.schemas import MaintenancePlanIn
from app.services import maintenance


def _age(path: Path, days: int = 2) -> Path:
    if not path.exists():
        path.write_bytes(b"stale")
    stamp = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp()
    os.utime(path, (stamp, stamp))
    return path


def test_cache_inventory_clears_generated_caches_and_preserves_local_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    uploads = tmp_path / "uploads"
    embeddings = tmp_path / "embeddings"
    uploads.mkdir()
    model_root = embeddings / maintenance.EMBEDDING_MODEL_CACHE_DIR
    model_root.mkdir(parents=True)
    monkeypatch.setattr(maintenance.settings, "upload_dir", str(uploads))
    monkeypatch.setattr(maintenance.settings, "embedding_cache_dir", str(embeddings))
    (uploads / "original.pdf").write_bytes(b"original")
    known = uploads / f".parsed-cache-{'a' * 64}.json"
    known.write_bytes(b"parser cache")
    (uploads / f".unknown-cache-{'b' * 64}.json").write_bytes(b"leave it")
    vector_cache = embeddings / f".embedding-cache-{'c' * 64}.json"
    vector_cache.write_bytes(b"generated vectors")
    model = model_root / "model.onnx"
    model.write_bytes(b"required model weights")
    blob = embeddings / "blobs" / "model-shard.bin"
    blob.parent.mkdir()
    blob.write_bytes(b"large ONNX blob")
    unrelated = embeddings / "unrelated" / "weights.bin"
    unrelated.parent.mkdir()
    unrelated.write_bytes(b"unrelated")
    (tmp_path / "outside.txt").write_bytes(b"outside")

    entries = maintenance._cache_entries(uploads, embeddings)

    assert {(entry.root, entry.relative) for entry in entries} == {
        ("document", known.name), ("embedding", vector_cache.name),
    }
    assert maintenance._sum_bytes(entries) == len(b"parser cache") + len(b"generated vectors")
    assert maintenance._model_cache_bytes(embeddings) == len(b"required model weights") + len(b"large ONNX blob")
    deleted_bytes, errors = maintenance._remove_cache_entries(entries)
    assert deleted_bytes == maintenance._sum_bytes(entries)
    assert errors == 0
    assert model.exists(), "The network-isolated processor still needs the downloaded model files"
    assert unrelated.exists()


def test_delete_plan_signature_detects_replaced_original(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(maintenance.settings, "upload_dir", str(uploads))
    document_id = uuid.uuid4()
    original = uploads / f"{document_id}.txt"
    original.write_bytes(b"previewed original")
    document = SimpleNamespace(
        id=document_id,
        filename="original.txt",
        file_size=original.stat().st_size,
        storage_path=str(original),
        markdown_path=None,
        markdown_map_path=None,
    )
    counts = {"messages": 2, "chunks": 3, "insights": 1, "versions": 1}
    preview_signature = maintenance._document_plan_signature(document, [], counts)
    original.write_bytes(b"changed original!!")
    assert original.stat().st_size == document.file_size

    current_signature = maintenance._document_plan_signature(document, [], counts)

    assert current_signature != preview_signature


def test_temporary_scan_protects_originals_references_active_work_and_used_versions(tmp_path: Path) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    active_id = str(uuid.uuid4())
    stored_id = str(uuid.uuid4())
    old_temp = _age(uploads / ".artifact-abandoned.tmp")
    old_active_temp = _age(uploads / f".artifact-{active_id}.v2.build.tmp")
    original = _age(uploads / f"{uuid.uuid4()}.pdf")
    old_used = _age(uploads / f"{stored_id}.v1.{'1' * 32}.markdown.md")
    old_unused = _age(uploads / f"{stored_id}.v3.{'3' * 32}.map.json")
    old_orphan_markdown = _age(uploads / f"{uuid.uuid4()}.markdown.md")
    current_map = _age(uploads / f"{uuid.uuid4()}.map.json")
    just_written = uploads / ".artifact-recent.tmp"
    just_written.write_bytes(b"new")
    threshold = datetime.now(timezone.utc)

    entries = maintenance._temp_entries(
        uploads,
        referenced_names={old_used.name, current_map.name},
        referenced_prefixes={f"{stored_id}.v1.{'1' * 32}"},
        active_document_ids={active_id},
        document_ids={stored_id},
        now=threshold,
    )

    assert {entry.relative for entry in entries} == {old_temp.name, old_unused.name, old_orphan_markdown.name}
    assert old_active_temp.exists()
    assert original.exists(), "An unindexed original is retained for manual recovery"
    assert old_used.exists()
    assert current_map.exists()
    assert just_written.exists()


def test_temp_file_deletion_refuses_path_escape_and_changed_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    uploads = tmp_path / "uploads"
    uploads.mkdir()
    monkeypatch.setattr(maintenance.settings, "upload_dir", str(uploads))
    item_path = uploads / ".artifact-old.tmp"
    item_path.write_bytes(b"before")
    item = maintenance._entry(item_path, "document", uploads)
    assert item is not None

    item_path.write_bytes(b"changed after preview")
    with pytest.raises(ValueError, match="изменился"):
        maintenance._remove_entry(item)
    assert item_path.exists()

    escape = maintenance.FileEntry("document", "../outside.txt", "file", 1, item.mtime_ns)
    with pytest.raises(ValueError, match="изменился"):
        maintenance._remove_entry(escape)


def test_tree_cleanup_never_follows_symlinks(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    embeddings = tmp_path / "embeddings"
    embeddings.mkdir()
    external = tmp_path / "keep.txt"
    external.write_text("outside")
    link = embeddings / "link"
    try:
        link.symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip("Symlink creation is unavailable in this environment")
    monkeypatch.setattr(maintenance.settings, "embedding_cache_dir", str(embeddings))
    item = maintenance._entry(link, "embedding", embeddings, include_dirs=True)
    assert item is not None and item.kind == "symlink"

    assert maintenance._remove_tree_entry(item) == 0
    assert not link.exists()
    assert external.read_text() == "outside"


def test_maintenance_plan_requires_unique_normalized_document_ids() -> None:
    document_id = uuid.uuid4()
    body = MaintenancePlanIn(action="delete_selected", document_ids=[str(document_id)])
    assert body.document_ids == [str(document_id)]

    with pytest.raises(ValueError, match="повторы"):
        MaintenancePlanIn(action="delete_selected", document_ids=[str(document_id), str(document_id).upper()])
    with pytest.raises(ValueError, match="Выберите"):
        MaintenancePlanIn(action="delete_selected")
    with pytest.raises(ValueError, match="список документов не требуется"):
        MaintenancePlanIn(action="clear_cache", document_ids=[str(document_id)])


@pytest.mark.parametrize(
    ("provided", "expected", "matches"),
    [
        (" ОЧИСТИТЬ ВРЕМЕННЫЕ ФАЙЛЫ ", "ОЧИСТИТЬ ВРЕМЕННЫЕ ФАЙЛЫ", True),
        ("confirm", "confirm", True),
        ("ОЧИСТИТЬ КЭШ", "ОЧИСТИТЬ ВРЕМЕННЫЕ ФАЙЛЫ", False),
    ],
)
def test_maintenance_confirmation_supports_unicode(provided: str, expected: str, matches: bool) -> None:
    assert maintenance._confirmation_matches(provided, expected) is matches


@pytest.mark.parametrize("gate", ["maintenance_cache_cleanup", "maintenance_delete_all"])
def test_upload_is_rejected_while_destructive_maintenance_is_active(gate: str) -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(**{gate: True})))

    with pytest.raises(HTTPException) as raised:
        asyncio.run(api.upload_document(request, object()))

    assert raised.value.status_code == 409
