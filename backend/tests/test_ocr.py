from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import ClassVar

import pytest

from app.services import ocr
from app.services.ocr import OCRProcessingError, OCRService


def test_ocr_rejects_paths_outside_upload_volume(tmp_path: Path) -> None:
    service = OCRService(tmp_path / "uploads")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-")
    with pytest.raises(OCRProcessingError, match="сохранённого файла"):
        service.process(outside)


def test_ocr_builds_page_locators_and_markdown(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "scan.pdf"
    path.write_bytes(b"placeholder")

    class FakePdf:
        is_encrypted = False
        pages: ClassVar = [object()]

    class FakeTesseract:
        TesseractNotFoundError = RuntimeError

        @staticmethod
        def get_tesseract_version() -> str:
            return "5.3.0"

        @staticmethod
        def image_to_data(*_args, **_kwargs):
            return {
                "text": ["Проверка", "OCR", "", "Document"],
                "conf": ["96", "94", "-1", "91"],
                "block_num": ["1", "1", "1", "2"],
                "par_num": ["1", "1", "1", "1"],
                "line_num": ["1", "1", "1", "1"],
            }

    monkeypatch.setattr(ocr, "PdfReader", lambda *_args, **_kwargs: FakePdf())
    monkeypatch.setattr(ocr, "convert_from_path", lambda *_args, **_kwargs: [SimpleNamespace(width=100, height=140)])
    monkeypatch.setattr(ocr, "pytesseract", FakeTesseract)
    monkeypatch.setattr(ocr, "Output", SimpleNamespace(DICT="dict"))

    result = OCRService(upload_dir).process(path)

    assert result.parsed.metadata["ocr_used"] is True
    assert result.parsed.blocks[0].locator["page"] == 1
    assert result.parsed.blocks[0].locator["line_start"] == 1
    assert "Проверка OCR" in result.markdown
    assert result.confidence == pytest.approx((96 + 94 + 91) / 3, abs=0.01)


def test_ocr_reports_unreadable_scan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "blank.pdf"
    path.write_bytes(b"placeholder")

    monkeypatch.setattr(ocr, "PdfReader", lambda *_args, **_kwargs: SimpleNamespace(is_encrypted=False, pages=[object()]))
    monkeypatch.setattr(ocr, "convert_from_path", lambda *_args, **_kwargs: [SimpleNamespace(width=100, height=100)])
    fake = SimpleNamespace(
        TesseractNotFoundError=RuntimeError,
        get_tesseract_version=lambda: "5.3.0",
        image_to_data=lambda *_args, **_kwargs: {"text": [], "conf": [], "block_num": [], "par_num": [], "line_num": []},
    )
    monkeypatch.setattr(ocr, "pytesseract", fake)
    monkeypatch.setattr(ocr, "Output", SimpleNamespace(DICT="dict"))

    with pytest.raises(OCRProcessingError, match="не нашёл читаемого текста"):
        OCRService(upload_dir).process(path)
