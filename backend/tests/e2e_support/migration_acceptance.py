"""Exercise the additive M16 migration against real isolated Compose data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("AI_CHECKER_BASE_URL", "").rstrip("/")
PROJECT = os.environ.get("E2E_COMPOSE_PROJECT", "")
COMPOSE_FILE = os.environ.get("E2E_COMPOSE_FILE", "")


def request(path: str) -> Any:
    with urllib.request.urlopen(f"{BASE_URL}{path}", timeout=15) as response:
        body = response.read()
    return json.loads(body) if body else None


def original_digest(document_id: str) -> str:
    with urllib.request.urlopen(f"{BASE_URL}/api/v1/documents/{document_id}/file", timeout=30) as response:
        return hashlib.sha256(response.read()).hexdigest()


def compose(*args: str) -> None:
    subprocess.run(
        ["docker", "compose", "-p", PROJECT, "-f", str(Path(COMPOSE_FILE).resolve()), *args],
        check=True,
        timeout=360,
    )


def migration_snapshot() -> dict[str, Any]:
    documents = {item["id"]: item for item in request("/api/v1/documents")}
    chats = request("/api/v1/chats")
    for chat in chats:
        document = documents.get(chat["document_id"])
        if not document or document["status"] != "ready" or document["file_type"] == "pdf":
            continue
        messages = request(f"/api/v1/chats/{chat['id']}/messages")
        cited = next((source for message in messages for source in message["citations"]), None)
        if cited is None:
            continue
        sources = request(f"/api/v1/documents/{document['id']}/chunks?offset=0&limit=200")
        legacy_source = next(
            (source for source in sources if source["id"] == cited["id"] and not any(
                key in source["locator"] for key in ("boxes", "word_boxes", "ocr_boxes")
            )),
            None,
        )
        if legacy_source is None:
            continue
        return {
            "document": {
                key: document[key]
                for key in ("id", "status", "active_version", "chunk_count", "markdown_status", "analysis_source")
            },
            "original_sha256": original_digest(document["id"]),
            "chat": {key: chat[key] for key in ("id", "document_id", "title", "pinned", "revision", "message_count")},
            "messages": [
                {
                    "id": item["id"],
                    "role": item["role"],
                    "source_version": item.get("source_version"),
                    "citations": [
                        {"id": source["id"], "locator": source["locator"]}
                        for source in item["citations"]
                    ],
                }
                for item in messages
            ],
            "legacy_source": {"id": legacy_source["id"], "locator": legacy_source["locator"]},
        }
    raise RuntimeError("No ready non-PDF document, cited chat and legacy source without OCR boxes were available for migration acceptance.")


def main() -> None:
    if not BASE_URL.startswith(("http://127.0.0.1:5175", "http://localhost:5175")):
        raise RuntimeError("M16 migration acceptance requires the isolated E2E origin on port 5175.")
    if re.fullmatch(r"document-checker-e2e-[0-9a-f]{8}", PROJECT) is None:
        raise RuntimeError("M16 migration acceptance requires the unique isolated E2E Compose project.")
    if not COMPOSE_FILE:
        raise RuntimeError("M16 migration acceptance requires the explicit isolated Compose file.")

    before = migration_snapshot()
    stopped = False
    upgraded = False
    try:
        compose("stop", "api", "worker")
        stopped = True
        compose("run", "--rm", "--no-deps", "api", "alembic", "downgrade", "0010_local_data_maintenance")
        compose("run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head")
        upgraded = True
    finally:
        if stopped:
            if not upgraded:
                # Leave the disposable project usable and at head even if the test itself fails.
                compose("run", "--rm", "--no-deps", "api", "alembic", "upgrade", "head")
            compose("up", "-d", "--wait", "--wait-timeout", "300", "api", "worker", "web")

    after = migration_snapshot()
    if before != after:
        raise AssertionError("The additive migration changed existing document, chat, message, citation, or original-file data.")
    for endpoint in (
        f"/api/v1/documents/{before['document']['id']}/bookmarks",
        f"/api/v1/documents/{before['document']['id']}/analysis/additional",
    ):
        assert isinstance(request(endpoint), list), "The new M09 routes did not become available after migration upgrade."
    print("M16 migration upgrade 0010 -> 0011 preserved existing documents, original bytes, chats, messages, citations and legacy locators.")


if __name__ == "__main__":
    main()
