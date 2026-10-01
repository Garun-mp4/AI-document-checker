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
                "left": ["10", "55", "10", "10"],
                "top": ["12", "12", "32", "52"],
                "width": ["42", "20", "45", "50"],
                "height": ["14", "14", "14", "14"],
            }

    monkeypatch.setattr(ocr, "PdfReader", lambda *_args, **_kwargs: FakePdf())
    monkeypatch.setattr(ocr, "convert_from_path", lambda *_args, **_kwargs: [SimpleNamespace(width=100, height=140)])
    monkeypatch.setattr(ocr, "pytesseract", FakeTesseract)
    monkeypatch.setattr(ocr, "Output", SimpleNamespace(DICT="dict"))

    progress: list[tuple[int, int]] = []
    result = OCRService(upload_dir).process(path, progress_callback=lambda completed, total: progress.append((completed, total)))

    assert result.parsed.metadata["ocr_used"] is True
    assert result.parsed.blocks[0].locator["page"] == 1
    assert result.parsed.blocks[0].locator["line_start"] == 1
    assert "Проверка OCR" in result.markdown
    assert result.confidence == pytest.approx((96 + 94 + 91) / 3, abs=0.01)
    assert progress == [(1, 1)]


def test_ocr_only_rasterizes_requested_pages_and_stores_compact_coordinate_maps(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from PIL import Image, ImageDraw

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "mixed.pdf"
    path.write_bytes(b"placeholder")

    class FakePage:
        mediabox = SimpleNamespace(width=612, height=792)
        cropbox = SimpleNamespace(left=18, bottom=18, right=594, top=774, width=576, height=756)
        rotation = 90

    class FakeTesseract:
        TesseractNotFoundError = RuntimeError

        @staticmethod
        def get_tesseract_version() -> str:
            return "5.3.0"

        @staticmethod
        def image_to_data(*_args, **_kwargs):
            return {
                "text": ["Проверка", "OCR", "Страница"],
                "conf": ["96", "94", "91"],
                "block_num": ["1", "1", "1"],
                "par_num": ["1", "1", "1"],
                "line_num": ["1", "1", "2"],
                "left": ["10", "48", "10"],
                "top": ["20", "20", "60"],
                "width": ["34", "19", "46"],
                "height": ["12", "12", "12"],
            }

    reader = SimpleNamespace(is_encrypted=False, pages=[FakePage() for _ in range(5)])
    rasterized: list[tuple[int, int, bool]] = []

    def render(_path, *, first_page, last_page, use_cropbox, **_kwargs):
        rasterized.append((first_page, last_page, use_cropbox))
        image = Image.new("RGB", (100, 140), "white")
        ImageDraw.Draw(image).rectangle((8, 15, 70, 80), fill="black")
        return [image]

    monkeypatch.setattr(ocr, "PdfReader", lambda *_args, **_kwargs: reader)
    monkeypatch.setattr(ocr, "convert_from_path", render)
    monkeypatch.setattr(ocr, "pytesseract", FakeTesseract)
    monkeypatch.setattr(ocr, "Output", SimpleNamespace(DICT="dict"))

    progress: list[tuple[int, int]] = []
    result = OCRService(upload_dir).process(
        path,
        pages=[2, 4],
        progress_callback=lambda completed, total: progress.append((completed, total)),
    )

    assert rasterized == [(2, 2, True), (4, 4, True)]
    assert progress == [(1, 2), (2, 2)]
    assert {block.locator["page"] for block in result.parsed.blocks} == {2, 4}
    locator = result.parsed.blocks[0].locator
    ocr_map = locator["ocr_map"]
    assert ocr_map["coordinate_space"] == "page-normalized-top-left"
    assert ocr_map["coordinate_scale"] == 10_000
    assert ocr_map["raster_width"] == 100
    assert ocr_map["raster_height"] == 140
    assert ocr_map["rotation"] == 90
    assert ocr_map["crop_box"] == [18.0, 18.0, 594.0, 774.0]
    assert ocr_map["word_boxes"][0][:4] == [1000, 1429, 4400, 2286]
    assert ocr_map["line_boxes"][0][4:7] == [0, len("Проверка OCR"), 1]
    assert len(ocr_map["word_boxes"]) == 3
    assert len(ocr_map["line_boxes"]) == 2
    assert locator["char_start"] == 0
    assert locator["char_end"] == len(result.parsed.blocks[0].text)


def test_ocr_refuses_a_text_result_without_reliable_word_coordinates() -> None:
    with pytest.raises(OCRProcessingError, match="координаты распознанного слова"):
        ocr._page_words(
            {"text": ["текст"], "conf": ["92"], "block_num": ["1"], "par_num": ["1"], "line_num": ["1"]},
            100,
            100,
        )


def test_page_timeout_does_not_discard_ocr_from_other_pages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from PIL import Image, ImageDraw

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    path = upload_dir / "mixed.pdf"
    path.write_bytes(b"placeholder")

    class FakeTesseract:
        class TesseractNotFoundError(Exception):
            pass

        calls = 0

        @classmethod
        def get_tesseract_version(cls) -> str:
            return "5.3.0"

        @classmethod
        def image_to_data(cls, *_args, **_kwargs):
            cls.calls += 1
            if cls.calls == 1:
                raise TimeoutError("synthetic page timeout")
            return {
                "text": ["Читаемая"], "conf": ["91"], "block_num": ["1"], "par_num": ["1"], "line_num": ["1"],
                "left": ["10"], "top": ["12"], "width": ["42"], "height": ["14"],
            }

    reader = SimpleNamespace(is_encrypted=False, pages=[object(), object()])

    def render(*_args, **_kwargs):
        image = Image.new("RGB", (100, 120), "white")
        ImageDraw.Draw(image).rectangle((8, 10, 72, 45), fill="black")
        return [image]

    monkeypatch.setattr(ocr, "PdfReader", lambda *_args, **_kwargs: reader)
    monkeypatch.setattr(ocr, "convert_from_path", render)
    monkeypatch.setattr(ocr, "pytesseract", FakeTesseract)
    monkeypatch.setattr(ocr, "Output", SimpleNamespace(DICT="dict"))

    result = OCRService(upload_dir).process(path, allow_empty=True)

    assert [page["classification"] for page in result.parsed.metadata["ocr_page_map"]] == ["unreadable", "ocr"]
    assert result.parsed.metadata["ocr_page_map"][0]["error"] == "recognition_failed"
    assert [(block.locator["page"], block.text) for block in result.parsed.blocks] == [(2, "Читаемая")]


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
