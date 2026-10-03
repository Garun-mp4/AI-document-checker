from __future__ import annotations

import os
import random
import re
import time
import zipfile
from io import BytesIO
from pathlib import Path

import httpx
import pytest
from openpyxl import Workbook

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
    assert "no-store" in page.headers.get("cache-control", ""), page.headers
    assert page.text.lstrip().lower().startswith("<!doctype html>")

    manifest_response = compose_client.get("/build-info.json")
    assert manifest_response.status_code == 200, manifest_response.text
    assert "no-store" in manifest_response.headers.get("cache-control", ""), manifest_response.headers
    manifest = manifest_response.json()
    assert manifest["build_id"]
    assert manifest["commit"]
    assert manifest["built_at"]
    assert any("pdf.worker" in asset and asset.endswith(".mjs") for asset in manifest["assets"])

    version_response = compose_client.get("/api/v1/version")
    assert version_response.status_code == 200, version_response.text
    assert "no-store" in version_response.headers.get("cache-control", ""), version_response.headers
    version = version_response.json()
    assert version["service"] == "api"
    assert {key: version[key] for key in ("build_id", "commit", "built_at")} == {
        key: manifest[key] for key in ("build_id", "commit", "built_at")
    }

    bundle_match = re.search(r'<script[^>]+src="([^"]+\.js)"', page.text)
    assert bundle_match, page.text
    bundle = compose_client.get(bundle_match.group(1))
    assert bundle.status_code == 200, bundle.text[:500]
    assert bundle.headers.get("content-type", "").startswith("application/javascript"), bundle.headers
    assert "immutable" in bundle.headers.get("cache-control", ""), bundle.headers

    worker_match = re.search(r'(/assets/[^"`]+\.mjs)', bundle.text)
    assert worker_match, "PDF.js worker is not present in the production bundle"
    worker = compose_client.get(f"{worker_match.group(1)}?v=pdfjs-4")
    assert worker.status_code == 200, worker.text[:500]
    assert worker.headers.get("content-type", "").startswith("application/javascript"), worker.headers
    assert "immutable" in worker.headers.get("cache-control", ""), worker.headers


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


@pytest.mark.integration
def test_compose_preserves_xlsx_formula_states_through_worker_and_citations(compose_client: httpx.Client) -> None:
    _require_disconnected_codex(compose_client)
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Данные"
    sheet.append(["Товар", "Количество", "Сохранённый итог", "Итог без кэша"])
    sheet.append(["A", 0, "=B2", "=B2+1"])
    stream = BytesIO()
    workbook.save(stream)
    workbook.close()
    patched = BytesIO()
    with zipfile.ZipFile(BytesIO(stream.getvalue())) as source, zipfile.ZipFile(patched, "w") as target:
        for entry in source.infolist():
            contents = source.read(entry.filename)
            if entry.filename == "xl/worksheets/sheet1.xml":
                contents = contents.replace(b"<f>B2</f><v></v>", b"<f>B2</f><v>0</v>")
            target.writestr(entry, contents)
    original = patched.getvalue()

    response = compose_client.post(
        "/api/v1/documents",
        files={"file": ("formula-states.xlsx", original, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    try:
        document = _wait_for_document(compose_client, document_id)
        assert document["status"] == "needs_auth", document.get("error_message")
        assert document["markdown_status"] == "ready"
        assert document["analysis_source"] == "markitdown"
        assert document["metadata"]["formula_count"] == 2
        assert document["metadata"]["formula_cache_missing_count"] == 1

        chunks_response = compose_client.get(f"/api/v1/documents/{document_id}/chunks?limit=200")
        assert chunks_response.status_code == 200, chunks_response.text
        chunks = chunks_response.json()
        cached = next(chunk for chunk in chunks if chunk["locator"].get("cell") == "C2")
        uncached = next(chunk for chunk in chunks if chunk["locator"].get("cell") == "D2")
        assert cached["locator"]["sheet"] == uncached["locator"]["sheet"] == "Данные"
        assert cached["locator"]["row_start"] == uncached["locator"]["row_start"] == 2
        assert cached["locator"]["mapping_confidence"] == uncached["locator"]["mapping_confidence"] == "exact"
        assert cached["locator"]["formula_has_cached_value"] is True
        assert uncached["locator"]["formula_has_cached_value"] is False
        assert "сохранённое значение: 0" in cached["text"]
        assert "не вычисляло" in uncached["text"]

        markdown = compose_client.get(f"/api/v1/documents/{document_id}/markdown")
        assert markdown.status_code == 200, markdown.text
        assert "выражение `=B2`" in markdown.json()["markdown"]
        assert "выражение `=B2+1`" in markdown.json()["markdown"]
        assert "Нет сохранённого результата" in markdown.json()["markdown"]
        markdown_download = compose_client.get(f"/api/v1/documents/{document_id}/markdown/download")
        assert markdown_download.status_code == 200, markdown_download.text
        assert markdown_download.content.decode("utf-8") == markdown.json()["markdown"]

        table = compose_client.get(f"/api/v1/documents/{document_id}/preview/table?offset=0&limit=10")
        assert table.status_code == 200, table.text
        formula_row = next(row for row in table.json()["rows"] if row["number"] == 2)
        assert formula_row["cells"][2:4] == ["0", ""]
        assert [item["has_cached_value"] for item in formula_row["formula_cells"]] == [True, False]

        original_response = compose_client.get(f"/api/v1/documents/{document_id}/file")
        assert original_response.status_code == 200, original_response.text
        assert original_response.content == original
    finally:
        delete_response = compose_client.delete(f"/api/v1/documents/{document_id}")
        assert delete_response.status_code == 204, delete_response.text


@pytest.mark.integration
def test_compose_indexes_mixed_pdf_and_persists_page_coordinate_map(compose_client: httpx.Client) -> None:
    _require_disconnected_codex(compose_client)
    content = (FIXTURES / "mixed.pdf").read_bytes()
    response = compose_client.post(
        "/api/v1/documents",
        files={"file": ("mixed.pdf", content, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    try:
        document = _wait_for_document(compose_client, document_id)
        assert document["status"] == "needs_auth", document.get("error_message")
        assert document["analysis_source"] == "ocr"
        assert document["ocr_status"] == "ready"
        assert document["ocr_page_count"] == 2

        page_map = document["metadata"]["pdf_page_map"]
        assert [page["classification"] for page in page_map] == ["native", "ocr", "blank", "ocr"]
        assert page_map[3]["rotation"] == 90
        assert page_map[3]["crop_box"]

        chunks_response = compose_client.get(f"/api/v1/documents/{document_id}/chunks?limit=200")
        assert chunks_response.status_code == 200, chunks_response.text
        chunks = chunks_response.json()
        assert any("Project: Document Checker Sample" in chunk["text"] for chunk in chunks)
        assert not any(chunk["locator"].get("page") == 3 for chunk in chunks)
        ocr_chunks = [chunk for chunk in chunks if chunk["locator"].get("ocr") is True]
        assert {chunk["locator"]["page"] for chunk in ocr_chunks} == {2, 4}
        assert all(chunk["locator"].get("ocr_map", {}).get("word_boxes") for chunk in ocr_chunks)
        assert all(chunk["locator"].get("ocr_map", {}).get("line_boxes") for chunk in ocr_chunks)
        for chunk in ocr_chunks:
            locator = chunk["locator"]
            coordinate_map = locator["ocr_map"]
            assert locator["char_end"] > locator["char_start"]
            assert coordinate_map["coordinate_scale"] == 10_000
            assert coordinate_map["raster_width"] > 0 and coordinate_map["raster_height"] > 0
            for box in coordinate_map["word_boxes"]:
                assert 0 <= box[0] < box[2] <= coordinate_map["coordinate_scale"]
                assert 0 <= box[1] < box[3] <= coordinate_map["coordinate_scale"]
                assert box[4] < box[5]

        markdown = compose_client.get(f"/api/v1/documents/{document_id}/markdown")
        assert markdown.status_code == 200, markdown.text
        markdown_text = markdown.json()["markdown"]
        assert "## Страница 1" in markdown_text
        assert "## Страница 2" in markdown_text
        assert "## Страница 3" not in markdown_text
        assert "## Страница 4" in markdown_text
        original = compose_client.get(f"/api/v1/documents/{document_id}/file")
        assert original.status_code == 200
        assert original.content == content
    finally:
        delete_response = compose_client.delete(f"/api/v1/documents/{document_id}")
        assert delete_response.status_code == 204, delete_response.text


@pytest.mark.integration
def test_compose_ocr_reprocess_creates_isolated_version_and_preserves_active_document_on_analysis_failure(
    compose_client: httpx.Client,
) -> None:
    _require_disconnected_codex(compose_client)
    original_bytes = (FIXTURES / "mixed.pdf").read_bytes()
    response = compose_client.post(
        "/api/v1/documents",
        files={"file": ("mixed.pdf", original_bytes, "application/pdf")},
    )
    assert response.status_code == 202, response.text
    document_id = response.json()["id"]
    try:
        initial = _wait_for_document(compose_client, document_id)
        assert initial["active_version"] > 0
        old_version = initial["active_version"]
        old_chunks = compose_client.get(f"/api/v1/documents/{document_id}/chunks?limit=200").json()
        original_ocr_ids = {chunk["id"] for chunk in old_chunks if chunk["locator"].get("ocr") is True}
        assert original_ocr_ids
        for invalid_settings in (
            {"language": "fra", "quality": "balanced"},
            {"language": "rus", "quality": "high", "pages": [5]},
            {"language": "eng", "quality": "fast", "pages": list(range(1, 102))},
        ):
            invalid = compose_client.post(
                f"/api/v1/documents/{document_id}/ocr/reprocess",
                json=invalid_settings,
            )
            assert invalid.status_code == 422, invalid.text

        reprocess = compose_client.post(
            f"/api/v1/documents/{document_id}/ocr/reprocess",
            json={"language": "eng", "quality": "high", "pages": [2]},
        )
        assert reprocess.status_code == 202, reprocess.text
        assert reprocess.json()["active_version"] == old_version

        jobs_response = compose_client.get(f"/api/v1/documents/{document_id}/jobs")
        assert jobs_response.status_code == 200, jobs_response.text
        retry_job = next(job for job in jobs_response.json() if job["version"] > old_version)
        assert retry_job["parameters"]["ocr"] == {
            "enabled": True,
            "languages": "eng",
            "dpi": 300,
            "quality": "high",
            "max_pages": 100,
        }
        assert retry_job["parameters"]["ocr_pages"] == [2]

        deadline = time.monotonic() + 180
        terminal_job = None
        while time.monotonic() < deadline:
            jobs = compose_client.get(f"/api/v1/documents/{document_id}/jobs").json()
            terminal_job = next(job for job in jobs if job["version"] == retry_job["version"])
            if terminal_job["state"] in {"failed", "cancelled", "succeeded"}:
                break
            time.sleep(0.5)
        assert terminal_job is not None and terminal_job["state"] == "failed", terminal_job
        assert terminal_job["error"]

        active = compose_client.get(f"/api/v1/documents/{document_id}").json()
        assert active["active_version"] == old_version
        assert active["chunk_count"] == initial["chunk_count"]
        assert active["metadata"]["pdf_page_map"] == initial["metadata"]["pdf_page_map"]
        active_chunks = compose_client.get(f"/api/v1/documents/{document_id}/chunks?limit=200").json()
        assert {chunk["id"] for chunk in active_chunks if chunk["locator"].get("ocr") is True} == original_ocr_ids
        original = compose_client.get(f"/api/v1/documents/{document_id}/file")
        assert original.status_code == 200
        assert original.content == original_bytes
        assert compose_client.get(f"/api/v1/documents/{document_id}/chat").status_code == 200
    finally:
        delete_response = compose_client.delete(f"/api/v1/documents/{document_id}")
        assert delete_response.status_code == 204, delete_response.text


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


@pytest.mark.integration
def test_compose_exports_saved_analysis_and_conversation_without_new_model_calls(
    compose_client: httpx.Client,
) -> None:
    provider = compose_client.post("/api/v1/__e2e/provider", json={"mode": "ready", "reset_counters": True})
    assert provider.status_code == 200, provider.text
    uploaded = compose_client.post(
        "/api/v1/documents",
        files={"file": ("export-sample.txt", (FIXTURES / "sample.txt").read_bytes(), "text/plain")},
    )
    assert uploaded.status_code == 202, uploaded.text
    document_id = uploaded.json()["id"]
    try:
        document = _wait_for_document(compose_client, document_id)
        assert document["status"] == "ready", document
        insights_response = compose_client.get(f"/api/v1/documents/{document_id}/insights")
        assert insights_response.status_code == 200, insights_response.text
        insights = insights_response.json()
        assert len(insights) == 7
        settings = compose_client.get("/api/v1/codex/status").json()

        source = insights[0]["citations"][0]
        source_version = insights[0]["source_version"]
        bookmark = compose_client.post(
            f"/api/v1/documents/{document_id}/bookmarks",
            json={"source_id": source["id"], "source_version": source_version, "note": " Проверить источник "},
        )
        assert bookmark.status_code == 201, bookmark.text
        assert bookmark.json()["source"]["id"] == source["id"]
        assert bookmark.json()["note"] == "Проверить источник"
        duplicate_bookmark = compose_client.post(
            f"/api/v1/documents/{document_id}/bookmarks",
            json={"source_id": source["id"], "source_version": source_version},
        )
        assert duplicate_bookmark.status_code == 409
        stale_bookmark = compose_client.post(
            f"/api/v1/documents/{document_id}/bookmarks",
            json={"source_id": source["id"], "source_version": source_version + 1},
        )
        assert stale_bookmark.status_code == 409
        updated_bookmark = compose_client.patch(
            f"/api/v1/documents/{document_id}/bookmarks/{bookmark.json()['id']}",
            json={"note": "Проверить оригинал"},
        )
        assert updated_bookmark.status_code == 200, updated_bookmark.text
        assert updated_bookmark.json()["note"] == "Проверить оригинал"
        assert compose_client.get(f"/api/v1/documents/{document_id}/bookmarks").json()[0]["note"] == "Проверить оригинал"

        additional_results = {}
        for mode in ("brief", "detailed", "tasks", "risks"):
            result = compose_client.post(
                f"/api/v1/documents/{document_id}/analysis/additional",
                json={
                    "mode": mode,
                    "model": settings["model"],
                    "reasoning_effort": settings["reasoning_effort"],
                    "analysis_version": insights[0]["version"],
                    "expected_source_version": source_version,
                },
                timeout=90,
            )
            assert result.status_code == 201, result.text
            additional_results[mode] = result.json()
            assert result.json()["mode"] == mode
            assert result.json()["analysis_version"] == insights[0]["version"]
            assert result.json()["source_version"] == source_version
            assert result.json()["model"] == settings["model"]
            assert result.json()["reasoning_effort"] == settings["reasoning_effort"]
            assert result.json()["citations"]
            assert all(item["id"] == source["id"] for item in result.json()["citations"])

        listed_additional = compose_client.get(f"/api/v1/documents/{document_id}/analysis/additional")
        assert listed_additional.status_code == 200, listed_additional.text
        assert {item["mode"] for item in listed_additional.json()} == {"brief", "detailed", "tasks", "risks"}
        stale_analysis = compose_client.post(
            f"/api/v1/documents/{document_id}/analysis/additional",
            json={
                "mode": "brief", "model": settings["model"], "reasoning_effort": settings["reasoning_effort"],
                "analysis_version": insights[0]["version"], "expected_source_version": source_version + 1,
            },
        )
        assert stale_analysis.status_code == 409

        markdown = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={"scope": "analysis", "format": "markdown"},
        )
        assert markdown.status_code == 200, markdown.text
        assert markdown.headers["content-type"].startswith("text/markdown")
        assert "attachment" in markdown.headers.get("content-disposition", "")
        assert "filename*=UTF-8''" in markdown.headers["content-disposition"]
        assert markdown.headers.get("x-content-type-options") == "nosniff"
        report = markdown.text
        assert "export-sample.txt" in report
        assert "Версия обработки" in report
        assert settings["model"] in report
        assert settings["reasoning_effort"] in report
        assert "Источники" in report
        assert "localhost" not in report
        assert "codex_thread_id" not in report
        assert "Authorization" not in report

        selected_additional = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={
                "scope": "analysis", "format": "markdown",
                "selected_additional_analysis_ids": [additional_results["tasks"]["id"]],
            },
        )
        assert selected_additional.status_code == 200, selected_additional.text
        assert "## Дополнительные результаты" in selected_additional.text
        assert "### Задачи" in selected_additional.text
        assert "### Кратко" not in selected_additional.text
        invalid_conversation_extra = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={
                "scope": "conversation", "format": "markdown",
                "selected_additional_analysis_ids": [additional_results["tasks"]["id"]],
            },
        )
        assert invalid_conversation_extra.status_code == 422

        pdf = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={"scope": "analysis", "format": "pdf"},
        )
        assert pdf.status_code == 200, pdf.text
        assert pdf.headers["content-type"].startswith("application/pdf")
        assert pdf.content.startswith(b"%PDF-")

        selected = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={"scope": "selected_answers", "format": "markdown", "selected_keys": [insights[0]["key"]]},
        )
        assert selected.status_code == 200, selected.text
        assert insights[0]["question"] in selected.text
        assert all(item["question"] not in selected.text for item in insights[1:])
        invalid_selection = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={"scope": "selected_answers", "format": "markdown", "selected_keys": ["missing-answer"]},
        )
        assert invalid_selection.status_code == 422

        chat = compose_client.get(f"/api/v1/documents/{document_id}/chat").json()
        sent = compose_client.post(
            f"/api/v1/chats/{chat['id']}/messages",
            json={"text": "Перечисли подтверждённые факты"},
            timeout=90,
        )
        assert sent.status_code == 200, sent.text
        saved_messages = compose_client.get(f"/api/v1/chats/{chat['id']}/messages").json()
        assistant = next(item for item in saved_messages if item["role"] == "assistant")
        assert assistant["model"] == settings["model"]
        assert assistant["reasoning_effort"] == settings["reasoning_effort"]
        conversation = compose_client.post(
            f"/api/v1/documents/{document_id}/export",
            json={"scope": "conversation", "format": "markdown"},
        )
        assert conversation.status_code == 200, conversation.text
        assert conversation.text.index("### Вы ·") < conversation.text.index("### Ассистент ·")
        assert "Синтетический ответ" in conversation.text
        assert "reasoning:" in conversation.text

        before_exports = compose_client.post("/api/v1/__e2e/provider", json={"mode": "ready"}).json()
        assert before_exports["complete_calls"] == 5
        assert before_exports["chat_calls"] == 1
        active_after_export = compose_client.get(f"/api/v1/documents/{document_id}").json()
        assert active_after_export["active_version"] == document["active_version"]
        original = compose_client.get(f"/api/v1/documents/{document_id}/file")
        assert original.status_code == 200
        assert original.content == (FIXTURES / "sample.txt").read_bytes()
        removed_bookmark = compose_client.delete(
            f"/api/v1/documents/{document_id}/bookmarks/{bookmark.json()['id']}"
        )
        assert removed_bookmark.status_code == 204
        assert compose_client.get(f"/api/v1/documents/{document_id}/bookmarks").json() == []
    finally:
        deleted = compose_client.delete(f"/api/v1/documents/{document_id}")
        assert deleted.status_code == 204, deleted.text
