from __future__ import annotations

import os
import random
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
TERMINAL_STATES = {"ready", "needs_auth", "model_unavailable", "error"}
EXPECTED_SOURCES = {
    "sample.pdf": ("Project: Document Checker Sample", "pdf"),
    "sample.docx": ("Тестовый проект", "docx"),
    "sample.txt": ("Проект: Проверка документов", "txt"),
    "sample.md": ("Тестовый проект", "md"),
    "sample.csv": ("Стоимость: 125.25", "csv"),
    "sample.xlsx": ("Альфа", "xlsx"),
    "sample.xls": ("Альфа", "xls"),
    "sample.pptx": ("Тестовый проект", "pptx"),
    "sample.html": ("Тестовый проект", "html"),
    "sample.json": ("Тестовый проект", "json"),
    "sample.epub": ("Тестовый проект", "epub"),
    "sample.xml": ("Алексей Пример", "xml"),
}
EXPECTED_PREVIEW_LAYOUTS = {
    "pdf": "pdf",
    "docx": "paper",
    "txt": "paper",
    "md": "paper",
    "csv": "table",
    "xlsx": "table",
    "xls": "table",
    "pptx": "slides",
    "html": "paper",
    "json": "tree",
    "epub": "paper",
    "xml": "tree",
}


@pytest.fixture(scope="module")
def compose_client():
    base_url = os.environ.get("AI_CHECKER_BASE_URL")
    if not base_url:
        pytest.skip("Set AI_CHECKER_BASE_URL to run Docker Compose integration tests")
    with httpx.Client(base_url=base_url.rstrip("/"), timeout=60, trust_env=False) as client:
        deadline = time.monotonic() + 60
        response = None
        while time.monotonic() < deadline:
            try:
                response = client.get("/api/v1/documents")
                if response.status_code == 200:
                    break
                if response.status_code not in {502, 503}:
                    pytest.fail(f"The API returned {response.status_code}: {response.text}")
            except httpx.RequestError:
                pass
            time.sleep(0.5)
        assert response is not None and response.status_code == 200, (
            f"The web proxy or API was not available within 60s: {response}"
        )
        yield client


def _require_disconnected_codex(client: httpx.Client) -> None:
    response = client.get("/api/v1/codex/status")
    assert response.status_code == 200, response.text
    status = response.json()
    if status.get("authenticated"):
        pytest.skip("Integration tests avoid sending even synthetic fixture text to a connected cloud model")


@pytest.mark.integration
def test_compose_serves_frontend_assets_with_browser_mime_types(compose_client: httpx.Client) -> None:
    page = compose_client.get("/")
    assert page.status_code == 200, page.text
    assert page.headers.get("content-type", "").startswith("text/html"), page.headers
    assert page.text.lstrip().lower().startswith("<!doctype html>")


@pytest.mark.integration
def test_compose_returns_codex_status_contract(compose_client: httpx.Client) -> None:
    response = compose_client.get("/api/v1/codex/status")

    assert response.status_code == 200, response.text
    status = response.json()
    assert isinstance(status["authenticated"], bool)
    assert status["model"]
    assert status["reasoning_effort"]
    assert isinstance(status["model_available"], bool)
    assert isinstance(status["reasoning_available"], bool)
    assert isinstance(status["model_label"], str)
    assert isinstance(status["models"], list)
    for model in status["models"]:
        assert model["id"]
        assert model["label"]
        assert isinstance(model["reasoning_efforts"], list)


@pytest.mark.integration
def test_compose_rejects_a_model_that_is_not_in_the_catalog(compose_client: httpx.Client) -> None:
    response = compose_client.post(
        "/api/v1/codex/preferences",
        json={"model": "definitely-not-a-codex-model", "reasoning_effort": "medium"},
    )
    assert response.status_code in {409, 422}, response.text


@pytest.mark.integration
def test_compose_chat_library_keeps_session_after_reloading(compose_client: httpx.Client) -> None:
    """A document session must be discoverable again by a fresh client request."""

    _require_disconnected_codex(compose_client)
    response = compose_client.post(
        "/api/v1/documents",
        files={"file": ("session.txt", (FIXTURES / "sample.txt").read_bytes(), "text/plain")},
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    try:
        document = _wait_for_document(compose_client, document_id)
        assert document["status"] == "needs_auth", document.get("error_message")

        first_library = compose_client.get("/api/v1/chats")
        assert first_library.status_code == 200, first_library.text
        first_chat = next(item for item in first_library.json() if item["document_id"] == document_id)
        assert first_chat["filename"] == "session.txt"
        assert first_chat["title"] == "session.txt"
        assert first_chat["message_count"] == 0
        assert first_chat["last_message_at"] is None

        # A second request models a browser reload: the server is the source of truth.
        second_library = compose_client.get("/api/v1/chats")
        assert second_library.status_code == 200, second_library.text
        second_chat = next(item for item in second_library.json() if item["document_id"] == document_id)
        assert second_chat["id"] == first_chat["id"]
        assert second_chat["status"] == "needs_auth"
    finally:
        delete_response = compose_client.delete(f"/api/v1/documents/{document_id}")
        assert delete_response.status_code == 204, delete_response.text


def _wait_for_document(client: httpx.Client, document_id: str, timeout: float = 300.0) -> dict:
    deadline = time.monotonic() + timeout
    latest: dict = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/v1/documents/{document_id}")
        assert response.status_code == 200, response.text
        latest = response.json()
        if latest["status"] in TERMINAL_STATES:
            return latest
        time.sleep(0.5)
    pytest.fail(f"Document processing did not finish in {timeout:g}s: {latest}")


def _upload_and_assert_indexed(
    client: httpx.Client,
    filename: str,
    content: bytes,
    expected_text: str,
    expected_kind: str,
) -> dict:
    response = client.post(
        "/api/v1/documents",
        files={"file": (filename, content, "application/octet-stream")},
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    try:
        document = _wait_for_document(client, document_id)
        assert document["status"] == "needs_auth", document.get("error_message")
        assert document["chunk_count"] > 0

        chunks_response = client.get(f"/api/v1/documents/{document_id}/chunks?limit=200")
        assert chunks_response.status_code == 200, chunks_response.text
        chunks = chunks_response.json()
        all_text = "\n".join(chunk["text"] for chunk in chunks)
        assert expected_text in all_text
        assert any(chunk["locator"].get("kind") == expected_kind for chunk in chunks)

        preview_response = client.get(f"/api/v1/documents/{document_id}/preview")
        assert preview_response.status_code == 200, preview_response.text
        preview = preview_response.json()
        assert preview["layout"] == EXPECTED_PREVIEW_LAYOUTS[document["file_type"]]
        assert preview["total_blocks"] == document["chunk_count"]
        assert preview["blocks"]
        assert preview["blocks"][0]["source_id"] == preview["blocks"][0]["id"]
        assert preview["original_url"].endswith(f"/documents/{document_id}/file")

        markdown_response = client.get(f"/api/v1/documents/{document_id}/markdown")
        assert markdown_response.status_code == 200, markdown_response.text
        markdown = markdown_response.json()
        assert markdown["status"] == "ready"
        assert markdown["source"] == "markitdown"
        assert markdown["total_chars"] > 0
        assert markdown["total_lines"] > 0
        assert markdown["markdown"]
        assert len(markdown["checksum"]) == 64
        download_response = client.get(f"/api/v1/documents/{document_id}/markdown/download")
        assert download_response.status_code == 200, download_response.text
        assert download_response.content
        assert "attachment" in download_response.headers.get("content-disposition", "")

        if document["file_type"] in {"csv", "xlsx", "xls"}:
            table_response = client.get(f"/api/v1/documents/{document_id}/preview/table?offset=0&limit=1")
            assert table_response.status_code == 200, table_response.text
            table = table_response.json()
            assert table["columns"]
            assert len(table["rows"]) == 1
            assert table["total_rows"] >= 1

        original_response = client.get(f"/api/v1/documents/{document_id}/file")
        assert original_response.status_code == 200, original_response.text
        assert original_response.content
        assert "inline" in original_response.headers.get("content-disposition", "")

        if document["file_type"] == "csv":
            metrics = {item["name"]: item for item in document["metadata"]["numeric_columns"]}
            assert metrics["Стоимость"]["sum"] == "300.00"
            assert metrics["Стоимость"]["average"] == "100.00"
            assert metrics["Часы"]["sum"] == "60"
        return document
    finally:
        delete_response = client.delete(f"/api/v1/documents/{document_id}")
        assert delete_response.status_code == 204, delete_response.text


@pytest.mark.integration
def test_compose_indexes_every_supported_fixture_through_the_real_api(compose_client: httpx.Client) -> None:
    _require_disconnected_codex(compose_client)

    for filename in sorted(EXPECTED_SOURCES):
        expected_text, expected_kind = EXPECTED_SOURCES[filename]
        document = _upload_and_assert_indexed(
            compose_client,
            filename,
            (FIXTURES / filename).read_bytes(),
            expected_text,
            expected_kind,
        )
        assert document["status"] == "needs_auth"


def _large_docx() -> bytes:
    source = (FIXTURES / "sample.docx").read_bytes()
    expanded = BytesIO()
    with zipfile.ZipFile(BytesIO(source)) as source_archive, zipfile.ZipFile(expanded, "w") as target_archive:
        for entry in source_archive.infolist():
            target_archive.writestr(entry, source_archive.read(entry.filename))
        payload = random.Random(42).randbytes(2_800_000)
        target_archive.writestr("word/media/synthetic-payload.bin", payload, compress_type=zipfile.ZIP_STORED)
    return expanded.getvalue()


@pytest.mark.integration
def test_compose_indexes_a_docx_similar_in_size_to_the_reported_failure(compose_client: httpx.Client) -> None:
    _require_disconnected_codex(compose_client)
    content = _large_docx()
    assert 2_700_000 < len(content) < 25 * 1024 * 1024

    document = _upload_and_assert_indexed(
        compose_client,
        "synthetic-2.8mb.docx",
        content,
        "Тестовый проект",
        "docx",
    )

    assert document["file_size"] == len(content)


@pytest.mark.integration
def test_compose_rejects_unsupported_and_over_limit_uploads_without_persisting_them(
    compose_client: httpx.Client,
) -> None:
    initial_ids = {item["id"] for item in compose_client.get("/api/v1/documents").json()}

    unsupported = compose_client.post(
        "/api/v1/documents",
        files={"file": ("notes.exe", b"synthetic", "application/octet-stream")},
    )
    oversized = compose_client.post(
        "/api/v1/documents",
        files={"file": ("too-large.txt", b"x" * (25 * 1024 * 1024 + 1), "text/plain")},
        timeout=120,
    )

    assert unsupported.status_code == 415
    assert oversized.status_code == 413
    final_ids = {item["id"] for item in compose_client.get("/api/v1/documents").json()}
    assert final_ids == initial_ids
