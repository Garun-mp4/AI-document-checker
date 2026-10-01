from __future__ import annotations

import re
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pypdf import PdfReader

try:  # Optional in the host test venv; the production Docker image installs both.
    from pdf2image import convert_from_path
    from pdf2image.exceptions import (
        PDFInfoNotInstalledError,
        PDFPageCountError,
        PDFPopplerTimeoutError,
        PopplerNotInstalledError,
    )
except ImportError:  # pragma: no cover - exercised by environments without OCR extras
    convert_from_path = None  # type: ignore[assignment]
    PDFInfoNotInstalledError = PDFPageCountError = PDFPopplerTimeoutError = PopplerNotInstalledError = RuntimeError  # type: ignore[misc,assignment]
try:
    import pytesseract
    from pytesseract import Output
except ImportError:  # pragma: no cover - exercised by environments without OCR extras
    pytesseract = None  # type: ignore[assignment]
    Output = None  # type: ignore[assignment]

from app.config import settings
from app.services.parsing import (
    DocumentParsingError,
    ParsedDocument,
    SourceBlock,
    _pdf_page_geometry,
)

OCR_COORDINATE_SCALE = 10_000
MAX_OCR_WORDS_PER_PAGE = 20_000
MAX_OCR_LINES_PER_PAGE = 5_000
MAX_OCR_SOURCE_CHARS = 900


class OCRProcessingError(DocumentParsingError):
    """A scanned document could not be converted into searchable text."""


class OCRPageMappingError(OCRProcessingError):
    """A page was read, but its OCR output cannot be mapped precisely."""


@dataclass(frozen=True)
class OCRResult:
    parsed: ParsedDocument
    markdown: str
    engine_version: str
    language: str
    confidence: float | None


def _normalise_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _blank_raster(image: Any) -> bool:
    """Classify empty pages cheaply from a small grayscale sample."""
    try:
        sample = image.convert("L")
        sample.thumbnail((320, 320))
        histogram = sample.histogram()
        pixel_count = sum(histogram)
        if pixel_count <= 0:
            return True
        foreground = sum(histogram[:235])
        # Prefer one inexpensive OCR pass over classifying faint page content
        # as blank and silently losing it.
        return foreground / pixel_count < 0.00002
    except (AttributeError, OSError, ValueError):
        # If an image implementation cannot be sampled, let Tesseract decide.
        return False


def _as_int(values: list[Any], key: str, index: int, default: int = 0) -> int:
    try:
        value = int(float(values[index]))
        return value if abs(value) < 2**31 else default
    except (IndexError, TypeError, ValueError, OverflowError):
        return default


def _normalised_edge(value: int, total: int) -> int:
    if total <= 0:
        return 0
    return max(0, min(OCR_COORDINATE_SCALE, round(value / total * OCR_COORDINATE_SCALE)))


def _page_words(data: dict[str, list[Any]], width: int, height: int) -> tuple[str, list[list[int]], list[list[int]], list[float]]:
    raw_words = data.get("text", [])
    if len(raw_words) > MAX_OCR_WORDS_PER_PAGE:
        raise OCRPageMappingError("На странице слишком много OCR-фрагментов для точной карты координат.")
    groups: OrderedDict[tuple[int, int, int], list[dict[str, Any]]] = OrderedDict()
    confidences: list[float] = []
    for index, raw_word in enumerate(raw_words):
        word = _normalise_line(str(raw_word))
        if not word:
            continue
        confidence_value = data.get("conf", [])
        try:
            confidence = float(confidence_value[index])
        except (IndexError, TypeError, ValueError, OverflowError):
            confidence = -1
        if confidence >= 0:
            confidences.append(confidence)
        key = (
            _as_int(data.get("block_num", []), "block_num", index),
            _as_int(data.get("par_num", []), "par_num", index),
            _as_int(data.get("line_num", []), "line_num", index),
        )
        left = _as_int(data.get("left", []), "left", index)
        top = _as_int(data.get("top", []), "top", index)
        box_width = _as_int(data.get("width", []), "width", index)
        box_height = _as_int(data.get("height", []), "height", index)
        if width <= 0 or height <= 0 or box_width <= 0 or box_height <= 0:
            raise OCRPageMappingError("Tesseract не вернул координаты распознанного слова.")
        x0 = max(0, min(width, left))
        y0 = max(0, min(height, top))
        x1 = max(x0, min(width, left + box_width))
        y1 = max(y0, min(height, top + box_height))
        if x0 == x1 or y0 == y1:
            raise OCRPageMappingError("Координаты распознанного слова выходят за границы страницы.")
        groups.setdefault(key, []).append({
            "text": word,
            "confidence": max(-1, min(100, round(confidence))) if confidence >= 0 else -1,
            "box": [
                _normalised_edge(x0, width), _normalised_edge(y0, height),
                _normalised_edge(x1, width), _normalised_edge(y1, height),
            ],
        })

    page_parts: list[str] = []
    word_boxes: list[list[int]] = []
    line_boxes: list[list[int]] = []
    cursor = 0
    for line_number, words in enumerate(groups.values(), start=1):
        if line_number > MAX_OCR_LINES_PER_PAGE:
            raise OCRPageMappingError("На странице слишком много OCR-строк для точной карты координат.")
        line_start = cursor
        line_words: list[str] = []
        for word in words:
            if line_words:
                page_parts.append(" ")
                cursor += 1
            text = word["text"]
            word_start = cursor
            page_parts.append(text)
            cursor += len(text)
            word_end = cursor
            word_boxes.append([*word["box"], word_start, word_end, line_number, word["confidence"]])
            line_words.append(text)
        line_end = cursor
        page_parts.append("\n")
        cursor += 1
        if words:
            boxes = [word["box"] for word in words]
            line_boxes.append([
                min(box[0] for box in boxes), min(box[1] for box in boxes),
                max(box[2] for box in boxes), max(box[3] for box in boxes),
                line_start, line_end, line_number,
                round(sum(max(0, word["confidence"]) for word in words) / len(words)),
            ])
    page_text = "".join(page_parts).rstrip("\n")
    return page_text, word_boxes, line_boxes, confidences


def _source_blocks_for_page(
    page_number: int,
    page_text: str,
    word_boxes: list[list[int]],
    line_boxes: list[list[int]],
    *,
    raster_width: int,
    raster_height: int,
    geometry: dict[str, Any],
    dpi: int,
    language: str,
    engine_version: str,
) -> list[SourceBlock]:
    if not page_text:
        return []
    blocks: list[SourceBlock] = []
    start = 0
    while start < len(page_text):
        end = min(start + MAX_OCR_SOURCE_CHARS, len(page_text))
        if end < len(page_text):
            boundary = page_text.rfind(" ", start + MAX_OCR_SOURCE_CHARS // 2, end)
            newline = page_text.rfind("\n", start + MAX_OCR_SOURCE_CHARS // 2, end)
            boundary = max(boundary, newline)
            if boundary > start:
                end = boundary
        text = page_text[start:end].strip()
        if text:
            left_trim = len(page_text[start:end]) - len(page_text[start:end].lstrip())
            right_trim = len(page_text[start:end]) - len(page_text[start:end].rstrip())
            char_start = start + left_trim
            char_end = end - right_trim
            word_subset = [box for box in word_boxes if box[4] < char_end and box[5] > char_start]
            line_subset = [box for box in line_boxes if box[4] < char_end and box[5] > char_start]
            line_numbers = [box[6] for box in word_subset]
            confidences = [box[7] for box in word_subset if box[7] >= 0]
            locator = {
                "kind": "pdf",
                "page": page_number,
                "label": f"Страница {page_number}",
                "line_start": min(line_numbers) if line_numbers else 1,
                "line_end": max(line_numbers) if line_numbers else 1,
                "char_start": char_start,
                "char_end": char_end,
                "source_text": text,
                "ocr": True,
                "ocr_confidence": round(sum(confidences) / len(confidences), 2) if confidences else None,
                "ocr_map": {
                    "coordinate_space": "page-normalized-top-left",
                    "coordinate_scale": OCR_COORDINATE_SCALE,
                    "raster_width": raster_width,
                    "raster_height": raster_height,
                    "dpi": dpi,
                    "rotation": geometry["rotation"],
                    "crop_box": geometry["crop_box"],
                    "language": language,
                    "engine_version": engine_version,
                    "word_boxes": word_subset,
                    "line_boxes": line_subset,
                },
            }
            blocks.append(SourceBlock(text, locator))
        start = max(end, start + 1)
        while start < len(page_text) and page_text[start].isspace():
            start += 1
    return blocks


class OCRService:
    """Run local Tesseract OCR on a scanned PDF without touching the original."""

    def __init__(self, upload_dir: str | Path) -> None:
        self.upload_root = Path(upload_dir).resolve()
        self.enabled = settings.ocr_enabled
        self.languages = settings.ocr_languages
        self.dpi = max(120, min(settings.ocr_dpi, 300))
        self.max_pages = max(1, min(settings.ocr_max_pages, 500))
        self.timeout = max(10, settings.ocr_timeout_seconds)
        self.max_chars = max(10_000, settings.ocr_max_chars)

    def _safe_path(self, path: Path) -> Path:
        from app.services.document_security import storage_path
        try:
            resolved = storage_path(path, self.upload_root)
        except DocumentParsingError as exc:
            raise OCRProcessingError("OCR разрешён только для сохранённого файла документа.") from exc
        if not resolved.is_file():
            raise OCRProcessingError("Оригинальный PDF недоступен для OCR.")
        return resolved

    @staticmethod
    def _engine_version() -> str:
        if pytesseract is None:
            raise OCRProcessingError("OCR недоступен: зависимости не установлены в backend-контейнере.")
        try:
            return f"tesseract-{pytesseract.get_tesseract_version()}"[:32]
        except (pytesseract.TesseractNotFoundError, RuntimeError, OSError) as exc:
            raise OCRProcessingError("OCR недоступен: в контейнере не найден Tesseract.") from exc

    def process(
        self,
        path: Path,
        progress_callback: Callable[[int, int], None] | None = None,
        *,
        pages: list[int] | None = None,
        allow_empty: bool = False,
    ) -> OCRResult:
        if not self.enabled:
            raise OCRProcessingError("OCR отключён настройками приложения.")
        safe_path = self._safe_path(path)
        engine_version = self._engine_version()
        try:
            reader = PdfReader(str(safe_path), strict=False)
            if reader.is_encrypted:
                raise OCRProcessingError("PDF защищён паролем. Сначала снимите пароль и повторите попытку.")
            page_count = len(reader.pages)
        except OCRProcessingError:
            raise
        except Exception as exc:
            raise OCRProcessingError("Не удалось подготовить PDF для OCR.") from exc
        if pages is None:
            selected_pages = list(range(1, page_count + 1))
        elif not isinstance(pages, list) or any(type(page) is not int for page in pages):
            raise OCRProcessingError("Для OCR указаны неверные номера страниц PDF.")
        else:
            selected_pages = sorted(set(pages))
        if not selected_pages or any(page < 1 or page > page_count for page in selected_pages):
            raise OCRProcessingError("Для OCR указаны неверные номера страниц PDF.")
        if len(selected_pages) > self.max_pages:
            raise OCRProcessingError(
                f"Для OCR выбрано {len(selected_pages)} страниц. Поддерживается не более {self.max_pages} страниц за задание."
            )
        if convert_from_path is None or pytesseract is None or Output is None:
            raise OCRProcessingError("OCR недоступен: зависимости не установлены в backend-контейнере.")
        blocks: list[SourceBlock] = []
        page_confidences: list[float] = []
        markdown_parts: list[str] = []
        total_chars = 0
        page_results: list[dict[str, Any]] = []
        overall_deadline = time.monotonic() + max(1, settings.document_worker_timeout_seconds - 2)
        for processed_count, page_number in enumerate(selected_pages, start=1):
            page = reader.pages[page_number - 1]
            geometry = _pdf_page_geometry(page)
            box = getattr(page, "cropbox", None)
            remaining = overall_deadline - time.monotonic()
            if remaining <= 1:
                page_results.append({
                    "page": page_number, "classification": "unreadable", "error": "document_timeout",
                    "raster_width": None, "raster_height": None, "dpi": self.dpi,
                    "rotation": geometry["rotation"], "crop_box": geometry["crop_box"],
                    "language": self.languages, "engine_version": engine_version,
                    "word_count": 0, "line_count": 0, "confidence": None,
                })
                if progress_callback:
                    progress_callback(processed_count, len(selected_pages))
                continue
            if box is not None:
                import math
                pixels = float(box.width) * float(box.height) * (self.dpi / 72) ** 2
                if not math.isfinite(pixels) or pixels <= 0 or pixels > 20_000_000:
                    raise OCRProcessingError("Размер страницы PDF превышает безопасный предел OCR.")
            try:
                images = convert_from_path(
                    str(safe_path), dpi=self.dpi, first_page=page_number, last_page=page_number,
                    fmt="png", thread_count=1, timeout=max(1, min(self.timeout, int(remaining))), use_cropbox=True,
                )
            except (PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError,
                    PopplerNotInstalledError, TimeoutError, OSError):
                page_results.append({
                    "page": page_number, "classification": "unreadable", "error": "page_render_failed",
                    "raster_width": None, "raster_height": None, "dpi": self.dpi,
                    "rotation": geometry["rotation"], "crop_box": geometry["crop_box"],
                    "language": self.languages, "engine_version": engine_version,
                    "word_count": 0, "line_count": 0, "confidence": None,
                })
                if progress_callback:
                    progress_callback(processed_count, len(selected_pages))
                continue
            if len(images) != 1:
                for image in images:
                    if hasattr(image, "close"):
                        image.close()
                page_results.append({
                    "page": page_number, "classification": "unreadable", "error": "page_render_failed",
                    "raster_width": None, "raster_height": None, "dpi": self.dpi,
                    "rotation": geometry["rotation"], "crop_box": geometry["crop_box"],
                    "language": self.languages, "engine_version": engine_version,
                    "word_count": 0, "line_count": 0, "confidence": None,
                })
                if progress_callback:
                    progress_callback(processed_count, len(selected_pages))
                continue
            image = images[0]
            page_width, page_height = int(image.width), int(image.height)
            page_status: dict[str, Any] = {
                "page": page_number,
                "classification": "blank" if _blank_raster(image) else "ocr_candidate",
                "raster_width": page_width,
                "raster_height": page_height,
                "dpi": self.dpi,
                "rotation": geometry["rotation"],
                "crop_box": geometry["crop_box"],
                "language": self.languages,
                "engine_version": engine_version,
                "word_count": 0,
                "line_count": 0,
                "confidence": None,
            }
            try:
                if page_status["classification"] != "blank":
                    remaining = overall_deadline - time.monotonic()
                    if remaining <= 1:
                        raise OCRProcessingError("OCR превысил общий лимит времени обработки документа.")
                    data: dict[str, list[Any]] = pytesseract.image_to_data(
                        image, lang=self.languages, config="--psm 3", output_type=Output.DICT,
                        timeout=max(1, min(self.timeout, int(remaining))),
                    )
                    page_text, word_boxes, line_boxes, confidences = _page_words(data, page_width, page_height)
                    if page_text:
                        if total_chars + len(page_text) > self.max_chars:
                            raise OCRProcessingError(
                                f"Результат OCR превышает безопасный предел {self.max_chars:,} символов."
                            )
                        total_chars += len(page_text)
                        confidence = sum(confidences) / len(confidences) if confidences else None
                        if confidence is not None:
                            page_confidences.append(confidence)
                        page_blocks = _source_blocks_for_page(
                            page_number, page_text, word_boxes, line_boxes,
                            raster_width=page_width, raster_height=page_height,
                            geometry=geometry, dpi=self.dpi, language=self.languages,
                            engine_version=engine_version,
                        )
                        blocks.extend(page_blocks)
                        markdown_parts.extend([f"## Страница {page_number}\n", page_text, "\n\n"])
                        page_status.update({
                            "classification": "ocr",
                            "word_count": len(word_boxes),
                            "line_count": len(line_boxes),
                            "confidence": round(confidence, 2) if confidence is not None else None,
                            "char_count": len(page_text),
                        })
                    else:
                        page_status.update({"classification": "unreadable", "error": "no_text_recognized"})
            except OCRPageMappingError:
                page_status.update({"classification": "unreadable", "error": "coordinate_map_failed"})
            except pytesseract.TesseractNotFoundError as exc:
                raise OCRProcessingError("OCR недоступен: в контейнере не найден Tesseract.") from exc
            except (RuntimeError, OSError):
                # A single unreadable page should not discard successful OCR
                # from other pages in the same mixed PDF.
                page_status.update({"classification": "unreadable", "error": "recognition_failed"})
            finally:
                if hasattr(image, "close"):
                    image.close()
                for rendered in images:
                    if rendered is not image and hasattr(rendered, "close"):
                        rendered.close()
            page_results.append(page_status)
            if progress_callback:
                progress_callback(processed_count, len(selected_pages))

        if not blocks and not allow_empty:
            raise OCRProcessingError("OCR не нашёл читаемого текста. Попробуйте более чёткий скан или PDF с текстовым слоем.")
        average_confidence = sum(page_confidences) / len(page_confidences) if page_confidences else None
        metadata: dict[str, Any] = {
            "page_count": page_count,
            "ocr_used": bool(blocks),
            "ocr_language": self.languages,
            "ocr_dpi": self.dpi,
            "ocr_config": "--psm 3",
            "ocr_max_page_seconds": self.timeout,
            "ocr_max_chars": self.max_chars,
            "ocr_engine_version": engine_version,
            "ocr_page_count": sum(page["classification"] == "ocr" for page in page_results),
            "ocr_char_count": total_chars,
            "ocr_confidence": round(average_confidence, 2) if average_confidence is not None else None,
            "ocr_page_map": page_results,
        }
        if page_results:
            metadata["raster_width"] = page_results[0]["raster_width"]
            metadata["raster_height"] = page_results[0]["raster_height"]
        return OCRResult(
            parsed=ParsedDocument("pdf", blocks, metadata), markdown="".join(markdown_parts).rstrip() + "\n",
            engine_version=engine_version, language=self.languages, confidence=metadata["ocr_confidence"],
        )
