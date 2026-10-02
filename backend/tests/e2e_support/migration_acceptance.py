"""Exercise the additive APP-M02 migration against real isolated Compose data."""

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


def post(path: str, payload: dict[str, Any]) -> Any:
    body = json.dumps(payload).encode("utf-8")
    call = urllib.request.Request(
        f"{BASE_URL}{path}", data=body, headers={"Content-Type": "application/json"}, method="POST",
    )
    with urllib.request.urlopen(call, timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def delete(path: str) -> Any:
    call = urllib.request.Request(f"{BASE_URL}{path}", method="DELETE")
    with urllib.request.urlopen(call, timeout=15) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


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
        raise RuntimeError("APP-M02 migration acceptance requires the isolated E2E origin on port 5175.")
    if re.fullmatch(r"document-checker-e2e-[0-9a-f]{8}", PROJECT) is None:
        raise RuntimeError("APP-M02 migration acceptance requires the unique isolated E2E Compose project.")
    if not COMPOSE_FILE:
        raise RuntimeError("APP-M02 migration acceptance requires the explicit isolated Compose file.")

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
    app_chat = post("/api/v1/chats/application", {})
    assert app_chat["scope"] == "application" and app_chat["document_id"] is None
    assert request(f"/api/v1/chats/{app_chat['id']}")["scope"] == "application"
    assert request(f"/api/v1/chats/{app_chat['id']}/messages") == []
    try:
        refused_downgrade = subprocess.run(
            ["docker", "compose", "-p", PROJECT, "-f", str(Path(COMPOSE_FILE).resolve()),
             "run", "--rm", "--no-deps", "api", "alembic", "downgrade", "0011_bookmarks_analysis"],
            check=False, capture_output=True, text=True, timeout=360,
        )
        downgrade_output = f"{refused_downgrade.stdout}\n{refused_downgrade.stderr}"
        assert refused_downgrade.returncode != 0 and "refusing to delete saved conversations" in downgrade_output.lower()
        assert request(f"/api/v1/chats/{app_chat['id']}")["scope"] == "application"
    finally:
        delete(f"/api/v1/chats/{app_chat['id']}")
    print("APP-M02 migration upgrade preserved existing document chats, messages, citations and original bytes; application chats work without documents and unsafe downgrade is refused.")


if __name__ == "__main__":
    main()
