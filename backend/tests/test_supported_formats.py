from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app import api
from app.config import Settings, settings
from app.services.document_formats import (
    MIME_TYPES,
    PACKAGE_PARTS,
    PREFERRED_MIME_TYPES,
    PREVIEW_RENDERERS,
    SUPPORTED_EXTENSIONS,
)
from app.services.document_security import validate_content, validate_mime
from app.services.parsing import DocumentParsingError, parse_document
from app.services.preview import _renderer_for

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).parent / "fixtures"
CAPABILITY_CATALOG = json.loads(
    (REPOSITORY_ROOT / "frontend/src/document-formats.json").read_text(encoding="utf-8")
)
FRONTEND_FORMATS = CAPABILITY_CATALOG["formats"]
EXPECTED_EXTENSIONS = {
    ".pdf", ".docx", ".txt", ".md", ".csv", ".xml", ".xlsx", ".xls",
    ".pptx", ".html", ".htm", ".json", ".epub",
}


def test_frontend_and_backend_capability_contracts_match() -> None:
    assert len(FRONTEND_FORMATS) == 13
    assert {item["extension"] for item in FRONTEND_FORMATS} == EXPECTED_EXTENSIONS
    assert {item["fileType"] for item in FRONTEND_FORMATS} == {extension[1:] for extension in EXPECTED_EXTENSIONS}
    assert SUPPORTED_EXTENSIONS == EXPECTED_EXTENSIONS
    assert set(MIME_TYPES) == EXPECTED_EXTENSIONS
    assert set(PREFERRED_MIME_TYPES) == EXPECTED_EXTENSIONS
    assert set(PREVIEW_RENDERERS) == {item["fileType"] for item in FRONTEND_FORMATS}

    for capability in FRONTEND_FORMATS:
        extension = capability["extension"]
        file_type = capability["fileType"]
        expected_mimes = set(capability["mimeTypes"])
        assert set(MIME_TYPES[extension]) == expected_mimes
        assert PREFERRED_MIME_TYPES[extension] == capability["mimeTypes"][0]
        assert PREFERRED_MIME_TYPES[extension] in MIME_TYPES[extension]
        assert PREVIEW_RENDERERS[file_type] == capability["previewRenderer"]
        assert _renderer_for(file_type.upper()) == capability["previewRenderer"]
        assert api.document_media_type(file_type, f"sample{extension}") == capability["mimeTypes"][0]

    assert set(PACKAGE_PARTS) == {".docx", ".xlsx", ".pptx", ".epub"}
    assert Settings.model_fields["max_upload_bytes"].default == CAPABILITY_CATALOG["maxUploadBytes"]


@pytest.mark.parametrize("capability", FRONTEND_FORMATS, ids=lambda item: item["fileType"])
def test_every_advertised_format_has_a_valid_native_parser_fixture(capability: dict[str, object]) -> None:
    file_type = str(capability["fileType"])
    extension = str(capability["extension"])
    data = (FIXTURES / f"sample.{file_type}").read_bytes()

    validate_content(f"SAMPLE{extension.upper()}", data)
    parsed = parse_document(f"SAMPLE{extension.upper()}", data)

    assert parsed.file_type == file_type
    assert parsed.blocks
    assert _renderer_for(file_type) == capability["previewRenderer"]


@pytest.mark.parametrize("capability", FRONTEND_FORMATS, ids=lambda item: item["fileType"])
def test_mime_contract_accepts_all_declared_and_missing_mimes_but_rejects_mismatch(capability: dict[str, object]) -> None:
    filename = f"sample{str(capability['extension']).upper()}"
    for mime in capability["mimeTypes"]:
        validate_mime(filename, str(mime))
    for mime in (None, "", "application/octet-stream", "binary/octet-stream"):
        validate_mime(filename, mime)
    with pytest.raises(DocumentParsingError, match="Тип содержимого"):
        validate_mime(filename, "application/x-invalid-document")


@pytest.mark.parametrize("capability", FRONTEND_FORMATS, ids=lambda item: item["fileType"])
def test_empty_fixture_for_each_supported_extension_is_rejected(capability: dict[str, object]) -> None:
    with pytest.raises(DocumentParsingError, match="пустой"):
        validate_content(f"empty{capability['extension']}", b"")


def test_empty_upload_is_removed_and_never_committed_for_every_supported_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "upload_dir", str(tmp_path))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))

    class EmptyUpload:
        def __init__(self, filename: str, content_type: str) -> None:
            self.filename = filename
            self.content_type = content_type
            self.closed = False

        async def read(self, _size: int) -> bytes:
            return b""

        async def close(self) -> None:
            self.closed = True

    for capability in FRONTEND_FORMATS:
        upload = EmptyUpload(f"empty{capability['extension']}", str(capability["mimeTypes"][0]))
        with pytest.raises(HTTPException) as error:
            asyncio.run(api.upload_document(request, upload))
        assert error.value.status_code == 400
        assert upload.closed
        assert list(tmp_path.iterdir()) == []


def test_unadvertised_format_families_are_not_in_the_backend_allowlist() -> None:
    assert not ({".xlsm", ".xlsb", ".png", ".jpg", ".zip"} & SUPPORTED_EXTENSIONS)
