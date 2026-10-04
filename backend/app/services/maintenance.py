"""Safe local-data maintenance plans, diagnostics and cleanup helpers."""
from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import io
import json
import logging
import os
import re
import secrets
import stat
import time
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select, text

from app.config import settings
from app.database import SessionLocal
from app.models import (
    AdditionalAnalysis,
    Chat,
    ChatDocument,
    Chunk,
    Document,
    DocumentBookmark,
    DocumentVersion,
    Insight,
    MaintenanceEvent,
    Message,
    ProcessingJob,
)
from app.schemas import MaintenanceExecuteIn, MaintenancePlanIn
from app.services.document_security import owned_storage, read_storage, remove_storage
from app.services.job_queue import ACTIVE, cancel, cleanup_files
from app.services.parsing import DocumentParsingError

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/maintenance", tags=["local data"])

SAFETY_AGE = timedelta(hours=24)
PLAN_TTL_SECONDS = 15 * 60
JOURNAL_RETENTION_DAYS = 90
JOURNAL_MAX_ROWS = 1000
CACHE_FILE_RE = re.compile(r"^\.(?:parsed|ocr|mapping|embedding)-cache-[0-9a-f]{64}\.json$")
CACHE_TEMP_RE = re.compile(r"^\.(?:parsed|ocr|mapping|embedding)-cache-tmp-[A-Za-z0-9_-]+$")
# FastEmbed downloads this required runtime model here. Keep it separate from
# disposable parser/vector-result caches: isolated indexing workers have no network.
EMBEDDING_MODEL_CACHE_DIR = "models--qdrant--paraphrase-multilingual-MiniLM-L12-v2-onnx-Q"
ATTEMPT_FILE_RE = re.compile(
    r"^(?P<document>[0-9a-f-]{36})\.v(?P<version>[1-9][0-9]*)\.(?P<attempt>[0-9a-f]{32})\.(?:markdown\.md|map\.json|embedding\.json)$"
)
DOCUMENT_ID_RE = re.compile(r"^[0-9a-f-]{36}$")
ERROR_CODE_RE = re.compile(r"^[a-z0-9_]{1,40}$")
SAFE_OPERATIONS = {"process", "analysis"}
SAFE_STAGES = {
    "queued", "extracting", "ocr", "indexing", "analysis_request", "waiting_analysis",
    "committing", "ready", "cancelled", "failed", "processing", "complete",
}
CONFIRMATIONS = {
    "clear_temp": "ОЧИСТИТЬ ВРЕМЕННЫЕ ФАЙЛЫ",
    "clear_cache": "ОЧИСТИТЬ КЭШ",
    "delete_selected": "УДАЛИТЬ ВЫБРАННЫЕ ДАННЫЕ",
    "delete_all": "УДАЛИТЬ ВСЮ БИБЛИОТЕКУ",
}


@dataclass(frozen=True)
class FileEntry:
    root: str
    relative: str
    kind: str
    size: int
    mtime_ns: int

    def fingerprint_value(self) -> tuple[str, str, str, int, int]:
        return (self.root, self.relative, self.kind, self.size, self.mtime_ns)


@dataclass
class MaintenancePlan:
    action: str
    confirmation: str
    expires_at: datetime
    created_monotonic: float
    document_ids: list[uuid.UUID]
    entries: list[FileEntry]
    signature: dict[str, Any]
    preview: dict[str, Any]


_plans: dict[str, MaintenancePlan] = {}
_plans_lock = asyncio.Lock()


def _root(path: str | Path) -> Path:
    candidate = Path(path).absolute()
    if candidate.is_symlink():
        raise ValueError("Хранилище настроено через символическую ссылку.")
    return candidate


def _entry(path: Path, root_name: str, root: Path, *, include_dirs: bool = False) -> FileEntry | None:
    try:
        info = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(info.st_mode):
        return FileEntry(root_name, path.relative_to(root).as_posix(), "symlink", 0, info.st_mtime_ns) if include_dirs else None
    if stat.S_ISDIR(info.st_mode):
        return FileEntry(root_name, path.relative_to(root).as_posix(), "directory", 0, info.st_mtime_ns) if include_dirs else None
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        return None
    return FileEntry(root_name, path.relative_to(root).as_posix(), "file", info.st_size, info.st_mtime_ns)


def _tree_entries(path: Path, root_name: str) -> list[FileEntry]:
    root = _root(path)
    if not root.exists():
        return []
    if not root.is_dir():
        raise ValueError("Каталог кэша недоступен.")
    result: list[FileEntry] = []
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                children = sorted(entries, key=lambda item: item.name)
        except OSError as exc:
            raise ValueError("Не удалось проверить каталог кэша.") from exc
        for child in children:
            candidate = Path(child.path)
            entry = _entry(candidate, root_name, root, include_dirs=True)
            if entry is None:
                continue
            result.append(entry)
            if entry.kind == "directory":
                stack.append(candidate)
    return result


def _cache_entries(upload_root: Path, embedding_root: Path) -> list[FileEntry]:
    result: list[FileEntry] = []
    root = _root(upload_root)
    if root.exists():
        try:
            with os.scandir(root) as entries:
                for child in entries:
                    if CACHE_FILE_RE.fullmatch(child.name):
                        item = _entry(Path(child.path), "document", root)
                        if item:
                            result.append(item)
        except OSError as exc:
            raise ValueError("Не удалось проверить кэш документов.") from exc
    # The embedding volume also contains the downloaded ONNX model used by
    # offline, network-isolated processing workers. Only clear generated cache
    # entries at the volume root; never recursively delete that runtime model.
    embedding = _root(embedding_root)
    if embedding.exists():
        try:
            with os.scandir(embedding) as entries:
                for child in entries:
                    if CACHE_FILE_RE.fullmatch(child.name) or CACHE_TEMP_RE.fullmatch(child.name):
                        item = _entry(Path(child.path), "embedding", embedding)
                        if item:
                            result.append(item)
        except OSError as exc:
            raise ValueError("Не удалось проверить кэш embeddings.") from exc
    return sorted(result, key=lambda item: (item.root, item.relative))


def _model_cache_bytes(embedding_root: Path) -> int:
    root = _root(embedding_root)
    # Hugging Face stores large ONNX blobs separately from the model snapshot
    # metadata, and snapshot entries point to those blobs with symlinks.
    candidates = (
        root / EMBEDDING_MODEL_CACHE_DIR,
        root / "blobs",
        root / "huggingface" / "hub" / EMBEDDING_MODEL_CACHE_DIR,
        root / "huggingface" / "hub" / "blobs",
    )
    total = 0
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            total += sum(item.size for item in _tree_entries(candidate, "model") if item.kind == "file")
        except (OSError, ValueError):
            # Replaced/symlinked cache roots are not followed for accounting.
            continue
    return total


def _document_file_fingerprints(document: Document, versions: list[DocumentVersion]) -> dict[str, tuple | None]:
    root = _root(settings.upload_dir)
    names: set[str] = set()
    document_id = str(document.id)
    original_name: str | None = None
    for value in (document.storage_path, document.markdown_path, document.markdown_map_path):
        if value:
            try:
                name = owned_storage(value, document_id).name
                names.add(name)
                if value == document.storage_path:
                    original_name = name
            except DocumentParsingError:
                continue
    for version in versions:
        snapshot = version.snapshot or {}
        for key in ("markdown_path", "markdown_map_path"):
            value = snapshot.get(key)
            if isinstance(value, str):
                try:
                    names.add(owned_storage(value, document_id).name)
                except DocumentParsingError:
                    continue
        prefix = snapshot.get("attempt_prefix")
        if isinstance(prefix, str) and re.fullmatch(re.escape(document_id) + r"\.v[1-9][0-9]*\.[0-9a-f]{32}", prefix):
            try:
                with os.scandir(root) as entries:
                    names.update(entry.name for entry in entries
                                 if entry.name.startswith(prefix + ".") or entry.name.startswith(".artifact-" + prefix + "."))
            except OSError:
                continue
    fingerprints: dict[str, tuple | None] = {}
    for name in sorted(names):
        target = root / name
        try:
            info = target.lstat()
        except FileNotFoundError:
            fingerprints[name] = None
            continue
        kind = "file" if stat.S_ISREG(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode) else "other"
        fingerprint: tuple[Any, ...] = (kind, info.st_dev, info.st_ino, info.st_nlink, info.st_size, info.st_mtime_ns)
        if name == original_name and kind == "file":
            try:
                original = read_storage(root / name, settings.max_upload_bytes)
            except (DocumentParsingError, OSError) as exc:
                raise ValueError("Не удалось проверить целостность оригинала перед удалением.") from exc
            fingerprint += (hashlib.sha256(original).hexdigest(),)
        fingerprints[name] = fingerprint
    return fingerprints


def _document_plan_signature(document: Document, versions: list[DocumentVersion], counts: dict[str, int]) -> dict[str, Any]:
    return {
        "filename": document.filename,
        "file_size": document.file_size,
        "storage_path": document.storage_path,
        "markdown_path": document.markdown_path,
        "markdown_map_path": document.markdown_map_path,
        **counts,
        "file_fingerprints": _document_file_fingerprints(document, versions),
    }


def _temp_entries(
    upload_root: Path,
    *,
    referenced_names: set[str],
    referenced_prefixes: set[str],
    active_document_ids: set[str],
    document_ids: set[str],
    now: datetime | None = None,
) -> list[FileEntry]:
    root = _root(upload_root)
    if not root.exists():
        return []
    now = now or datetime.now(timezone.utc)
    threshold = now.timestamp() - SAFETY_AGE.total_seconds()
    result: list[FileEntry] = []
    try:
        with os.scandir(root) as entries:
            children = list(entries)
    except OSError as exc:
        raise ValueError("Не удалось проверить временные файлы.") from exc
    for child in children:
        name = child.name
        item = _entry(Path(child.path), "document", root)
        if not item or item.kind != "file" or item.mtime_ns / 1_000_000_000 > threshold:
            continue
        if name in referenced_names:
            continue
        if name.startswith(".artifact-"):
            matched_active = any(document_id in name for document_id in active_document_ids)
            if not matched_active:
                result.append(item)
            continue
        if CACHE_TEMP_RE.fullmatch(name):
            if not active_document_ids:
                result.append(item)
            continue
        match = ATTEMPT_FILE_RE.fullmatch(name)
        if match:
            document_id = match.group("document")
            prefix = f"{document_id}.v{match.group('version')}.{match.group('attempt')}"
            if document_id not in active_document_ids and prefix not in referenced_prefixes:
                result.append(item)
            continue
        match = re.fullmatch(r"(?P<document>[0-9a-f-]{36})\.(?:markdown\.md|map\.json)", name)
        if match and match.group("document") not in document_ids and match.group("document") not in active_document_ids:
            result.append(item)
    return sorted(result, key=lambda item: item.relative)


def _remove_entry(item: FileEntry) -> int:
    base = _root(settings.upload_dir if item.root == "document" else settings.embedding_cache_dir)
    target = (base / item.relative).absolute()
    if target.parent != base or target.is_symlink():
        raise ValueError("Путь временного файла изменился; повторите предварительный просмотр.")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return 0
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != item.size or info.st_mtime_ns != item.mtime_ns:
        raise ValueError("Файл изменился после предварительного просмотра.")
    target.unlink()
    return info.st_size


def _remove_tree_entry(item: FileEntry) -> int:
    base = _root(settings.embedding_cache_dir)
    target = (base / item.relative).absolute()
    if not target.is_relative_to(base):
        raise ValueError("Каталог кэша вышел за пределы хранилища.")
    try:
        info = target.lstat()
    except FileNotFoundError:
        return 0
    if stat.S_ISLNK(info.st_mode):
        if item.kind != "symlink" or info.st_mtime_ns != item.mtime_ns:
            raise ValueError("Запись кэша изменилась после предварительного просмотра.")
        target.unlink()
        return 0
    if stat.S_ISREG(info.st_mode):
        if item.kind != "file" or info.st_nlink != 1 or info.st_size != item.size or info.st_mtime_ns != item.mtime_ns:
            raise ValueError("Общий файл кэша не будет удалён автоматически.")
        target.unlink()
        return info.st_size
    if stat.S_ISDIR(info.st_mode):
        if item.kind != "directory":
            raise ValueError("Каталог кэша изменился после предварительного просмотра.")
        target.rmdir()
        return 0
    raise ValueError("Тип файла кэша изменился.")


def _sum_bytes(items: list[FileEntry]) -> int:
    return sum(item.size for item in items)


def _remove_cache_entries(entries: list[FileEntry]) -> tuple[int, int]:
    deleted_bytes = errors = 0
    for item in sorted(entries, key=lambda entry: (entry.root, -entry.relative.count("/"), entry.relative)):
        try:
            if item.root == "document":
                deleted_bytes += _remove_entry(item)
            else:
                deleted_bytes += _remove_tree_entry(item)
        except (OSError, ValueError):
            errors += 1
    return deleted_bytes, errors


def _remove_temp_entries(entries: list[FileEntry]) -> tuple[int, int]:
    deleted_bytes = errors = 0
    for item in entries:
        try:
            deleted_bytes += _remove_entry(item)
        except (OSError, ValueError):
            errors += 1
    return deleted_bytes, errors


def _entries_signature(items: list[FileEntry]) -> list[tuple[str, str, str, int, int]]:
    return [item.fingerprint_value() for item in items]


async def _document_protection(session) -> tuple[set[str], set[str], set[str], set[str]]:
    documents = (await session.execute(select(Document))).scalars().all()
    versions = (await session.execute(select(DocumentVersion))).scalars().all()
    jobs = (await session.execute(select(ProcessingJob.document_id).where(ProcessingJob.state.in_(ACTIVE)))).scalars().all()
    referenced_names: set[str] = set()
    document_ids = {str(document.id) for document in documents}
    referenced_prefixes: set[str] = set()
    for document in documents:
        for value in (document.storage_path, document.markdown_path, document.markdown_map_path):
            if value:
                referenced_names.add(Path(value).name)
    for version in versions:
        snapshot = version.snapshot or {}
        for key in ("markdown_path", "markdown_map_path"):
            value = snapshot.get(key)
            if isinstance(value, str):
                referenced_names.add(Path(value).name)
        prefix = snapshot.get("attempt_prefix")
        if isinstance(prefix, str) and re.fullmatch(r"[0-9a-f-]{36}\.v[1-9][0-9]*\.[0-9a-f]{32}", prefix):
            referenced_prefixes.add(prefix)
    return referenced_names, referenced_prefixes, {str(value) for value in jobs}, document_ids


async def _append_event(action: str, outcome: str, item_count: int, bytes_changed: int, details: dict[str, Any]) -> None:
    safe_details = {key: value for key, value in details.items() if key in {
        "documents", "messages", "chunks", "insights", "versions", "originals", "markdown", "maps",
        "cache_entries", "temporary_entries", "embedding_cache_entries", "parser_cache_entries", "cancelled_jobs",
    } and isinstance(value, (int, float, bool, str))}
    async with SessionLocal() as session, session.begin():
        session.add(MaintenanceEvent(action=action, outcome=outcome, item_count=item_count,
                                     bytes_changed=max(0, bytes_changed), details=safe_details))
        await session.execute(text("DELETE FROM maintenance_events WHERE created_at < now() - interval '90 days'"))
        await session.execute(text(
            "DELETE FROM maintenance_events WHERE id IN (SELECT id FROM maintenance_events "
            "ORDER BY created_at DESC OFFSET 1000)"
        ))


def _document_file_size(document: Document, versions: list[DocumentVersion]) -> tuple[int, dict[str, int]]:
    categories = {"originals": 0, "markdown": 0, "maps": 0}
    names: dict[str, str] = {}
    for value, category in ((document.storage_path, "originals"), (document.markdown_path, "markdown"), (document.markdown_map_path, "maps")):
        if value:
            names[Path(value).name] = category
    for version in versions:
        snapshot = version.snapshot or {}
        for key, category in (("markdown_path", "markdown"), ("markdown_map_path", "maps")):
            value = snapshot.get(key)
            if isinstance(value, str):
                names[Path(value).name] = category
    root = _root(settings.upload_dir)
    for name, category in names.items():
        candidate = root / name
        if candidate.parent != root or candidate.is_symlink():
            continue
        try:
            info = candidate.lstat()
        except OSError:
            continue
        if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            categories[category] += info.st_size
    return sum(categories.values()), categories


async def _query_doc_counts(session, document_ids: list[uuid.UUID]) -> dict[str, dict[str, int]]:
    if not document_ids:
        return {}
    count_fields = ("messages", "chunks", "insights", "versions", "bookmarks", "additional_analyses")
    output = {str(document_id): {field: 0 for field in count_fields} for document_id in document_ids}
    statements = (
        ("messages", select(Chat.document_id, func.count(Message.id)).join(Message, Message.chat_id == Chat.id).where(Chat.document_id.in_(document_ids)).group_by(Chat.document_id)),
        ("chunks", select(Chunk.document_id, func.count(Chunk.id)).where(Chunk.document_id.in_(document_ids)).group_by(Chunk.document_id)),
        ("insights", select(Insight.document_id, func.count(Insight.id)).where(Insight.document_id.in_(document_ids)).group_by(Insight.document_id)),
        ("versions", select(DocumentVersion.document_id, func.count()).where(DocumentVersion.document_id.in_(document_ids)).group_by(DocumentVersion.document_id)),
        ("bookmarks", select(DocumentBookmark.document_id, func.count(DocumentBookmark.id)).where(DocumentBookmark.document_id.in_(document_ids)).group_by(DocumentBookmark.document_id)),
        ("additional_analyses", select(AdditionalAnalysis.document_id, func.count(AdditionalAnalysis.id)).where(AdditionalAnalysis.document_id.in_(document_ids)).group_by(AdditionalAnalysis.document_id)),
    )
    for field, statement in statements:
        for document_id, count in (await session.execute(statement)).all():
            output[str(document_id)][field] = int(count)
    return output


async def _get_docs_for_plan(session, ids: list[uuid.UUID]) -> tuple[list[Document], list[DocumentVersion], dict[str, dict[str, int]]]:
    docs = (await session.execute(select(Document).where(Document.id.in_(ids)).order_by(Document.created_at, Document.id))).scalars().all() if ids else []
    versions = (await session.execute(select(DocumentVersion).where(DocumentVersion.document_id.in_(ids)))) .scalars().all() if ids else []
    return docs, versions, await _query_doc_counts(session, ids)


def _group_versions(versions: list[DocumentVersion]) -> dict[uuid.UUID, list[DocumentVersion]]:
    result: dict[uuid.UUID, list[DocumentVersion]] = {}
    for version in versions:
        result.setdefault(version.document_id, []).append(version)
    return result


async def _active_chat_tasks(request: Request, chat_ids: list[str]) -> int:
    tasks = getattr(request.app.state, "chat_generation_tasks", {})
    if not chat_ids:
        return 0
    async with SessionLocal() as session:
        message_ids = (await session.execute(select(Message.id).where(
            Message.chat_id.in_([uuid.UUID(value) for value in chat_ids]), Message.generation_status == "streaming"
        ))).scalars().all()
    running = [tasks[str(message_id)] for message_id in message_ids
               if str(message_id) in tasks and not tasks[str(message_id)].done()]
    for task in running:
        task.cancel()
    if running:
        try:
            await asyncio.wait_for(asyncio.gather(*running, return_exceptions=True), timeout=max(5, settings.queue_heartbeat_seconds + 3))
        except TimeoutError:
            raise HTTPException(status_code=409, detail="Ответ в чате ещё завершает работу. Повторите удаление позже.")
    return len(running)


async def _stop_document_jobs(ids: list[uuid.UUID]) -> int:
    async with SessionLocal() as session:
        job_ids = (await session.execute(select(ProcessingJob.id).where(
            ProcessingJob.document_id.in_(ids), ProcessingJob.state.in_(ACTIVE)
        ))).scalars().all() if ids else []
    for document_id in ids:
        try:
            await cancel(document_id)
        except KeyError:
            continue
    deadline = asyncio.get_running_loop().time() + max(5, settings.queue_heartbeat_seconds + 3)
    while True:
        async with SessionLocal() as session:
            active = (await session.execute(select(ProcessingJob.document_id, ProcessingJob.state).where(
                ProcessingJob.document_id.in_(ids), ProcessingJob.state.in_(ACTIVE)
            ))).all() if ids else []
        if not active:
            break
        if asyncio.get_running_loop().time() >= deadline:
            raise HTTPException(status_code=409, detail="Обработка документа ещё завершается. Данные не удалены; повторите позже.")
        await asyncio.sleep(0.1)
    if not job_ids:
        return 0
    async with SessionLocal() as session:
        return int(await session.scalar(select(func.count()).select_from(ProcessingJob).where(
            ProcessingJob.id.in_(job_ids), ProcessingJob.state == "cancelled"
        )) or 0)


async def delete_documents(request: Request, document_ids: list[uuid.UUID], *, action: str = "delete_selected", expected: dict[str, Any] | None = None) -> dict[str, Any]:
    ids = list(dict.fromkeys(document_ids))
    if not ids and action != "delete_all":
        return {"deleted_documents": 0, "deleted_bytes": 0, "deleted_records": {},
                "file_cleanup_errors": 0, "cancelled_chat_tasks": 0, "cancelled_jobs": 0}
    deleting = getattr(request.app.state, "maintenance_deleting_documents", None)
    if deleting is None:
        deleting = set()
        request.app.state.maintenance_deleting_documents = deleting
    if deleting.intersection(map(str, ids)):
        raise HTTPException(status_code=409, detail="Удаление этих документов уже выполняется.")
    if action == "delete_all" and getattr(request.app.state, "maintenance_delete_all", False):
        raise HTTPException(status_code=409, detail="Очистка библиотеки уже выполняется.")
    deleting.update(map(str, ids))
    if action == "delete_all":
        request.app.state.maintenance_delete_all = True
    try:
        return await _delete_documents_locked(request, ids, action=action, expected=expected)
    finally:
        deleting.difference_update(map(str, ids))
        if action == "delete_all":
            request.app.state.maintenance_delete_all = False


async def _delete_documents_locked(request: Request, ids: list[uuid.UUID], *, action: str,
                                   expected: dict[str, Any] | None) -> dict[str, Any]:
    """Cancel live work, lock records, then delete DB and owned files consistently."""
    async with SessionLocal() as session:
        docs, versions, counts = await _get_docs_for_plan(session, ids)
        if len(docs) != len(ids):
            raise HTTPException(status_code=404, detail="Один из выбранных документов больше не существует.")
        chat_ids = [str(value) for value in (await session.execute(
            select(Chat.id).where(Chat.document_id.in_(ids))
            .union(select(ChatDocument.chat_id).where(ChatDocument.document_id.in_(ids)))
        )).scalars().all()]
    version_map = _group_versions(versions)
    pre_signatures = {str(document.id): _document_plan_signature(
        document, version_map.get(document.id, []), counts[str(document.id)]) for document in docs}
    if expected is not None:
        changed = pre_signatures != expected
        if changed:
            raise HTTPException(status_code=409, detail="Содержимое библиотеки изменилось после предварительного просмотра. Проверьте план удаления заново.")
    # Validate the preview before cancellation, so a stale plan cannot change
    # work state and then fail without performing the requested deletion.
    cancelled_chats = await _active_chat_tasks(request, chat_ids)
    cancelled_jobs = await _stop_document_jobs(ids)
    file_bytes = 0
    file_counts = {"documents": len(docs), "messages": 0, "chunks": 0, "insights": 0, "versions": 0,
                   "bookmarks": 0, "additional_analyses": 0, "originals": 0, "markdown": 0, "maps": 0}
    for document in docs:
        file_counts["messages"] += counts[str(document.id)]["messages"]
        file_counts["chunks"] += counts[str(document.id)]["chunks"]
        file_counts["insights"] += counts[str(document.id)]["insights"]
        file_counts["versions"] += counts[str(document.id)]["versions"]
        file_counts["bookmarks"] += counts[str(document.id)]["bookmarks"]
        file_counts["additional_analyses"] += counts[str(document.id)]["additional_analyses"]
        _, categories = _document_file_size(document, version_map.get(document.id, []))
        for category, size in categories.items():
            file_counts[category] += 1 if size else 0
            file_bytes += size
    snapshots_by_id = {document.id: list(version_map.get(document.id, [])) for document in docs}
    paths_by_id = {document.id: (document.storage_path, document.markdown_path, document.markdown_map_path) for document in docs}
    async with SessionLocal() as session, session.begin():
        if action == "delete_all":
            await session.execute(text("SELECT pg_advisory_xact_lock(73402105)"))
            current_ids = set((await session.execute(select(Document.id))).scalars().all())
            if current_ids != set(ids):
                raise HTTPException(status_code=409, detail="В библиотеке появились или изменились документы. Постройте план очистки заново.")
        locked = (await session.execute(select(Document).where(Document.id.in_(ids)).order_by(Document.id).with_for_update())).scalars().all()
        if len(locked) != len(ids):
            raise HTTPException(status_code=409, detail="Содержимое библиотеки изменилось. Проверьте план удаления заново.")
        active = (await session.execute(select(ProcessingJob.id).where(ProcessingJob.document_id.in_(ids), ProcessingJob.state.in_(ACTIVE)))).first()
        if active:
            raise HTTPException(status_code=409, detail="Для выбранного документа снова запущена обработка. Данные не удалены.")
        await session.execute(select(DocumentVersion).where(DocumentVersion.document_id.in_(ids)).with_for_update())
        for document in locked:
            await session.delete(document)
    errors = 0
    from app.services.document_search import original_search_source_cache
    from app.services.preview_cache import (
        preview_response_cache,
        table_response_cache,
        table_search_response_cache,
    )
    for document_id in ids:
        original_search_source_cache.remove_document(str(document_id))
        preview_response_cache.remove_document(str(document_id))
        table_response_cache.remove_document(str(document_id))
        table_search_response_cache.remove_document(str(document_id))
        for version in snapshots_by_id[document_id]:
            try:
                cleanup_files(version.snapshot or {}, document_id)
            except (OSError, ValueError, DocumentParsingError):
                errors += 1
                logger.warning("Refused unsafe or unavailable version artifact deletion")
        for path in paths_by_id[document_id]:
            if not path:
                continue
            try:
                remove_storage(owned_storage(path, document_id))
            except (OSError, DocumentParsingError):
                errors += 1
                logger.warning("Refused unsafe or unavailable artifact deletion")
    action_name = "delete_all" if action == "delete_all" else "delete_selected"
    await _append_event(action_name, "partial" if errors else "complete", len(ids), file_bytes,
                        {**file_counts, "cancelled_jobs": cancelled_jobs})
    return {"deleted_documents": len(ids), "deleted_bytes": file_bytes, "deleted_records": file_counts,
            "file_cleanup_errors": errors, "cancelled_chat_tasks": cancelled_chats, "cancelled_jobs": cancelled_jobs}


def _plan_response(action: str, plan_id: str, plan: MaintenancePlan) -> dict[str, Any]:
    return {"plan_id": plan_id, "action": action, "expires_at": plan.expires_at.isoformat(),
            "confirmation_phrase": plan.confirmation, **plan.preview}


async def _store_plan(plan: MaintenancePlan) -> str:
    async with _plans_lock:
        expired = [key for key, value in _plans.items() if value.created_monotonic + PLAN_TTL_SECONDS < time.monotonic()]
        for key in expired:
            _plans.pop(key, None)
        plan_id = secrets.token_urlsafe(24)
        _plans[plan_id] = plan
    return plan_id


def _confirmation_matches(provided: str, expected: str) -> bool:
    # compare_digest accepts arbitrary UTF-8 bytes; str inputs are restricted to ASCII.
    return secrets.compare_digest(provided.strip().encode("utf-8"), expected.encode("utf-8"))


@router.get("/documents")
async def local_documents(offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=100), q: str = Query(default="", max_length=120)) -> dict[str, Any]:
    async with SessionLocal() as session:
        statement = select(Document).order_by(Document.updated_at.desc(), Document.id)
        if q.strip():
            statement = statement.where(Document.filename.ilike(f"%{q.strip()}%"))
        total = int(await session.scalar(select(func.count()).select_from(statement.subquery())) or 0)
        docs = (await session.execute(statement.offset(offset).limit(limit))).scalars().all()
        ids = [doc.id for doc in docs]
        versions = (await session.execute(select(DocumentVersion).where(DocumentVersion.document_id.in_(ids)))).scalars().all() if ids else []
        counts = await _query_doc_counts(session, ids)
        grouped = _group_versions(versions)
        items = []
        for doc in docs:
            _, sizes = _document_file_size(doc, grouped.get(doc.id, []))
            items.append({"id": str(doc.id), "filename": doc.filename, "file_type": doc.file_type,
                          "status": doc.status, "file_size": doc.file_size, "markdown_bytes": sizes["markdown"],
                          "map_bytes": sizes["maps"], "chunk_count": counts[str(doc.id)]["chunks"],
                          "message_count": counts[str(doc.id)]["messages"], "updated_at": doc.updated_at.isoformat()})
        return {"items": items, "total": total, "offset": offset, "limit": limit, "has_more": offset + len(items) < total}


@router.get("/summary")
async def local_data_summary() -> dict[str, Any]:
    async with SessionLocal() as session:
        document_count = int(await session.scalar(select(func.count()).select_from(Document)) or 0)
        chat_count = int(await session.scalar(select(func.count()).select_from(Chat)) or 0)
        message_count = int(await session.scalar(select(func.count()).select_from(Message)) or 0)
        chunk_count = int(await session.scalar(select(func.count()).select_from(Chunk)) or 0)
        version_count = int(await session.scalar(select(func.count()).select_from(DocumentVersion)) or 0)
        documents = (await session.execute(select(Document))).scalars().all()
        versions = (await session.execute(select(DocumentVersion))).scalars().all()
        referenced_names, referenced_prefixes, active_ids, document_ids = await _document_protection(session)
        indexes_bytes = int(await session.scalar(text("SELECT pg_total_relation_size('chunks')")) or 0)
    grouped = _group_versions(versions)
    sizes = {"originals_bytes": 0, "markdown_bytes": 0, "maps_bytes": 0}
    for doc in documents:
        _, doc_sizes = _document_file_size(doc, grouped.get(doc.id, []))
        sizes["originals_bytes"] += doc_sizes["originals"]
        sizes["markdown_bytes"] += doc_sizes["markdown"]
        sizes["maps_bytes"] += doc_sizes["maps"]
    cache_files = await asyncio.to_thread(_cache_entries, _root(settings.upload_dir), _root(settings.embedding_cache_dir))
    temporary_files = await asyncio.to_thread(
        _temp_entries, _root(settings.upload_dir), referenced_names=referenced_names,
        referenced_prefixes=referenced_prefixes, active_document_ids=active_ids, document_ids=document_ids,
    )
    return {
        "counts": {"documents": document_count, "chats": chat_count, "messages": message_count,
                   "chunks": chunk_count, "versions": version_count},
        "storage": {**sizes, "indexes_bytes": indexes_bytes,
                    "parser_cache_bytes": sum(item.size for item in cache_files if item.root == "document"),
                    "embedding_cache_bytes": sum(item.size for item in cache_files if item.root == "embedding"),
                    "model_cache_bytes": await asyncio.to_thread(_model_cache_bytes, _root(settings.embedding_cache_dir)),
                    "cache_entries": sum(item.kind == "file" for item in cache_files),
                    "temporary_bytes": _sum_bytes(temporary_files), "temporary_entries": len(temporary_files)},
        "protection": {"codex_authorization": "protected", "active_work": len(active_ids)},
        "codex_data_flow": {
            "local": "Оригиналы, история чатов и индексы хранятся в локальных Docker volumes.",
            "sent": "Для анализа отправляются вопросы по темам и ограниченные фрагменты. Для чата — ваш вопрос, до 32 предыдущих сообщений и найденные фрагменты. Полный оригинал документа не передаётся.",
            "authorization": "Авторизация Codex хранится отдельно и не затрагивается очисткой кэша и библиотеки.",
        },
    }


@router.get("/journal")
async def maintenance_journal(limit: int = Query(default=20, ge=1, le=100)) -> list[dict[str, Any]]:
    async with SessionLocal() as session:
        rows = (await session.execute(select(MaintenanceEvent).order_by(MaintenanceEvent.created_at.desc()).limit(limit))).scalars().all()
        return [{"action": row.action, "outcome": row.outcome, "item_count": row.item_count,
                 "bytes_changed": row.bytes_changed, "details": row.details or {}, "created_at": row.created_at.isoformat()}
                for row in rows]


@router.post("/plans")
async def create_maintenance_plan(body: MaintenancePlanIn, request: Request) -> dict[str, Any]:
    action = body.action
    ids = [uuid.UUID(value) for value in body.document_ids]
    entries: list[FileEntry] = []
    signature: dict[str, Any] = {}
    preview: dict[str, Any]
    if action == "clear_temp":
        async with SessionLocal() as session:
            names, prefixes, active_ids, docs = await _document_protection(session)
        entries = await asyncio.to_thread(
            _temp_entries, _root(settings.upload_dir), referenced_names=names, referenced_prefixes=prefixes,
            active_document_ids=active_ids, document_ids=docs,
        )
        by_kind: dict[str, int] = {}
        for entry in entries:
            kind = "atomic_write" if entry.relative.startswith(".artifact-") else "unused_generated_artifact" if ATTEMPT_FILE_RE.fullmatch(entry.relative) or entry.relative.endswith((".markdown.md", ".map.json")) else "stale_temporary_cache_write"
            by_kind[kind] = by_kind.get(kind, 0) + 1
        preview = {"items": len(entries), "bytes": _sum_bytes(entries), "contents": by_kind,
                   "scope": "Только временные и неиспользуемые производные файлы старше 24 часов. Оригиналы без записи в БД сохраняются для ручного восстановления.",
                   "protected_active_jobs": len(active_ids)}
        signature = {"entries": _entries_signature(entries)}
    elif action == "clear_cache":
        async with SessionLocal() as session:
            active = int(await session.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.state.in_(ACTIVE))) or 0)
        active_chat_tasks = any(not task.done() for task in getattr(request.app.state, "chat_generation_tasks", {}).values())
        if active or active_chat_tasks:
            raise HTTPException(status_code=409, detail="Очистка кэша доступна после завершения активной обработки и ответа в чате.")
        entries = await asyncio.to_thread(_cache_entries, _root(settings.upload_dir), _root(settings.embedding_cache_dir))
        parser_count = sum(entry.root == "document" for entry in entries)
        embedding_count = sum(entry.root == "embedding" for entry in entries)
        preview = {"items": len(entries), "bytes": _sum_bytes(entries),
                   "contents": {"parser_cache_entries": parser_count, "embedding_cache_entries": embedding_count},
                   "scope": "Только повторно создаваемый кэш парсеров и векторов. Файлы локальной модели embeddings остаются на месте: изолированная обработка использует их без сетевого доступа. Индекс, документы, переписка и Codex-авторизация сохраняются.",
                   "model_redownload_required": False}
        signature = {"entries": _entries_signature(entries), "active_jobs": 0}
    else:
        async with SessionLocal() as session:
            if action == "delete_all":
                ids = list((await session.execute(select(Document.id).order_by(Document.id))).scalars().all())
            docs, versions, counts = await _get_docs_for_plan(session, ids)
            if len(docs) != len(ids):
                raise HTTPException(status_code=404, detail="Один из выбранных документов больше не существует.")
            grouped = _group_versions(versions)
            names = []
            signature = {}
            totals = {"messages": 0, "chunks": 0, "insights": 0, "versions": 0,
                      "bookmarks": 0, "additional_analyses": 0, "originals": 0, "markdown": 0, "maps": 0}
            total_bytes = 0
            for doc in docs:
                _, categories = _document_file_size(doc, grouped.get(doc.id, []))
                total_bytes += sum(categories.values())
                names.append({"id": str(doc.id), "filename": doc.filename, "file_type": doc.file_type,
                              "original_bytes": categories["originals"], "markdown_bytes": categories["markdown"],
                              "map_bytes": categories["maps"], **counts[str(doc.id)]})
                signature[str(doc.id)] = _document_plan_signature(doc, grouped.get(doc.id, []), counts[str(doc.id)])
                for field in ("messages", "chunks", "insights", "versions", "bookmarks", "additional_analyses"):
                    totals[field] += counts[str(doc.id)][field]
                for field, category in (("originals", "originals"), ("markdown", "markdown"), ("maps", "maps")):
                    totals[field] += 1 if categories[category] else 0
            preview = {"items": len(docs), "bytes": total_bytes, "records": {"documents": len(docs), **totals},
                       "documents": names[:200], "documents_truncated": len(names) > 200,
                       "scope": "Удалятся оригиналы и производные файлы, индексы, карточки анализа, версии, чаты и сообщения выбранных документов."}
    plan = MaintenancePlan(action, CONFIRMATIONS[action], datetime.now(timezone.utc) + timedelta(seconds=PLAN_TTL_SECONDS),
                           time.monotonic(), ids, entries, signature, preview)
    plan_id = await _store_plan(plan)
    return _plan_response(action, plan_id, plan)


@router.post("/execute")
async def execute_maintenance_plan(body: MaintenanceExecuteIn, request: Request) -> dict[str, Any]:
    async with _plans_lock:
        plan = _plans.pop(body.plan_id, None)
    if plan is None or plan.created_monotonic + PLAN_TTL_SECONDS < time.monotonic():
        raise HTTPException(status_code=409, detail="Предварительный просмотр истёк. Постройте план заново.")
    if not _confirmation_matches(body.confirmation, plan.confirmation):
        # A wrong phrase must not consume the plan. Reinsert while preserving expiry.
        async with _plans_lock:
            _plans[body.plan_id] = plan
        raise HTTPException(status_code=400, detail="Подтверждение не совпадает с фразой из плана.")
    if plan.action in {"delete_selected", "delete_all"}:
        result = await delete_documents(request, plan.document_ids, action=plan.action, expected=plan.signature)
        return {"action": plan.action, "outcome": "partial" if result["file_cleanup_errors"] else "complete", **result}
    if plan.action == "clear_cache":
        if getattr(request.app.state, "maintenance_cache_cleanup", False):
            raise HTTPException(status_code=409, detail="Очистка кэша уже выполняется.")
        request.app.state.maintenance_cache_cleanup = True
        try:
            async with SessionLocal() as session:
                active = int(await session.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.state.in_(ACTIVE))) or 0)
            tasks = [task for task in getattr(request.app.state, "chat_generation_tasks", {}).values() if not task.done()]
            if active or tasks:
                raise HTTPException(status_code=409, detail="Кэш используется активной обработкой или ответом в чате. Данные не изменены.")
            current = await asyncio.to_thread(_cache_entries, _root(settings.upload_dir), _root(settings.embedding_cache_dir))
            if _entries_signature(current) != plan.signature.get("entries"):
                raise HTTPException(status_code=409, detail="Кэш изменился после предварительного просмотра. Постройте план заново.")
            deleted_bytes, errors = await asyncio.to_thread(_remove_cache_entries, plan.entries)
            from app.services.embeddings import _model
            _model.cache_clear()
            await _append_event("clear_cache", "partial" if errors else "complete", len(plan.entries), deleted_bytes,
                                {"cache_entries": len(plan.entries),
                                 "parser_cache_entries": sum(item.root == "document" for item in plan.entries),
                                 "embedding_cache_entries": sum(item.root == "embedding" and item.kind == "file" for item in plan.entries)})
            return {"action": "clear_cache", "outcome": "partial" if errors else "complete", "deleted_entries": len(plan.entries) - errors,
                    "deleted_bytes": deleted_bytes, "errors": errors, "model_redownload_required": False}
        finally:
            request.app.state.maintenance_cache_cleanup = False
    # Re-scan both DB references and file metadata immediately before unlinking.
    stage = "load_protection"
    try:
        async with SessionLocal() as session:
            names, prefixes, active_ids, docs = await _document_protection(session)
        stage = "scan_files"
        current = await asyncio.to_thread(
            _temp_entries, _root(settings.upload_dir), referenced_names=names, referenced_prefixes=prefixes,
            active_document_ids=active_ids, document_ids=docs,
        )
        stage = "verify_plan"
        if _entries_signature(current) != plan.signature.get("entries"):
            raise HTTPException(status_code=409, detail="Временные файлы или ссылки изменились. Постройте план заново.")
        stage = "remove_files"
        deleted_bytes, errors = await asyncio.to_thread(_remove_temp_entries, plan.entries)
        stage = "write_journal"
        await _append_event("clear_temp", "partial" if errors else "complete", len(plan.entries), deleted_bytes,
                            {"temporary_entries": len(plan.entries)})
        return {"action": "clear_temp", "outcome": "partial" if errors else "complete", "deleted_entries": len(plan.entries) - errors,
                "deleted_bytes": deleted_bytes, "errors": errors}
    except HTTPException:
        raise
    except Exception as exc:
        # Keep failures diagnosable without logging file names, contents, or exception text.
        logger.error("Local data clear_temp failed at stage=%s exception_type=%s", stage, type(exc).__name__)
        raise


@router.get("/diagnostics")
async def diagnostics() -> Response:
    started = time.perf_counter()
    async with SessionLocal() as session:
        await session.execute(text("SELECT 1"))
        states = (await session.execute(select(ProcessingJob.operation, ProcessingJob.stage, ProcessingJob.state,
                                               ProcessingJob.error_code, ProcessingJob.started_at,
                                               ProcessingJob.finished_at, ProcessingJob.created_at,
                                               ProcessingJob.lease_until).order_by(ProcessingJob.created_at.desc()).limit(1000))).all()
    grouped: dict[str, int] = {}
    error_codes: dict[str, int] = {}
    timings: dict[str, list[float]] = {}
    active = stale = 0
    now = datetime.now(timezone.utc)
    for operation, stage, state, code, began, ended, created, lease in states:
        operation_key = operation if operation in SAFE_OPERATIONS else "other"
        stage_key = stage if stage in SAFE_STAGES else "other"
        state_key = state if state in {"queued", "running", "cancelling", "cancelled", "succeeded", "failed"} else "other"
        for key, value in ((f"{operation_key}:{stage_key}:{state_key}", 1),):
            grouped[key] = grouped.get(key, 0) + value
        if code and ERROR_CODE_RE.fullmatch(str(code)):
            error_codes[str(code)] = error_codes.get(str(code), 0) + 1
        if state in ACTIVE:
            active += 1
            if lease and lease <= now:
                stale += 1
        if began and ended and ended >= began:
            timings.setdefault(operation_key, []).append(max(0.0, (ended - began).total_seconds()))
    safe_versions = {}
    for name in ("fastapi", "sqlalchemy", "alembic", "pydantic", "uvicorn", "markitdown", "fastembed", "pgvector"):
        try:
            safe_versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
    timing_summary = {}
    for name, values in timings.items():
        ordered = sorted(values)
        timing_summary[name] = {"samples": len(ordered), "median_seconds": round(ordered[(len(ordered) - 1) // 2], 3),
                                "p95_seconds": round(ordered[min(len(ordered) - 1, int(len(ordered) * .95))], 3)}
    payload = {
        "schema_version": 1,
        "generated_at": now.isoformat(),
        "build": {"id": settings.app_build_id, "commit": settings.app_build_commit, "built_at": settings.app_build_time},
        "runtime": {"python": f"{os.sys.version_info.major}.{os.sys.version_info.minor}.{os.sys.version_info.micro}", "components": safe_versions},
        "health": {"api": "ok", "database": "ok", "worker_queue": "stalled" if stale else "active" if active else "idle",
                   "worker_container_healthcheck": "not_available_from_application",
                   "active_jobs": active, "expired_leases": stale},
        "jobs": {"counts_by_operation_stage_state": grouped, "error_code_counts": error_codes},
        "timings": {"completed_job_seconds": timing_summary},
        "diagnostic_collection_ms": round((time.perf_counter() - started) * 1000, 3),
        "privacy": "Aggregate diagnostics only. Document and chat text, filenames, paths, credentials, environment and database URLs are excluded.",
    }
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        bundle.writestr("diagnostics.json", json.dumps(payload, ensure_ascii=False, indent=2))
    archive.seek(0)
    return StreamingResponse(archive, media_type="application/zip", headers={
        "Content-Disposition": 'attachment; filename="document-checker-diagnostics.zip"',
        "Cache-Control": "no-store",
    })
