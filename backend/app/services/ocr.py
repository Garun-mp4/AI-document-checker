from __future__ import annotations

import re
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
from app.services.parsing import DocumentParsingError, ParsedDocument, SourceBlock


class OCRProcessingError(DocumentParsingError):
    """A scanned document could not be converted into searchable text."""


@dataclass(frozen=True)
class OCRResult:
    parsed: ParsedDocument
    markdown: str
    engine_version: str
    language: str
    confidence: float | None


def _normalise_line(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


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

    def process(self, path: Path, progress_callback: Callable[[int, int], None] | None = None) -> OCRResult:
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
        if page_count > self.max_pages:
            raise OCRProcessingError(f"В PDF {page_count} страниц. Для OCR поддерживается не более {self.max_pages} страниц.")
        if convert_from_path is None or pytesseract is None or Output is None:
            raise OCRProcessingError("OCR недоступен: зависимости не установлены в backend-контейнере.")
        def page_images():
            for page_number, page in enumerate(reader.pages, start=1):
                box = getattr(page, 'mediabox', None)
                if box is not None:
                    import math
                    pixels = float(box.width) * float(box.height) * (self.dpi / 72) ** 2
                    if not math.isfinite(pixels) or pixels <= 0 or pixels > 20_000_000:
                        raise OCRProcessingError('Размер страницы PDF превышает безопасный предел OCR.')
                try:
                    images = convert_from_path(
                        str(safe_path), dpi=self.dpi, first_page=page_number, last_page=page_number,
                        fmt='png', thread_count=1, timeout=self.timeout,
                    )
                except (PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError,
                        PopplerNotInstalledError, TimeoutError, OSError) as exc:
                    raise OCRProcessingError('Не удалось подготовить страницу PDF для OCR.') from exc
                try:
                    for image in images:
                        yield page_number, image
                finally:
                    for image in images:
                        if hasattr(image, 'close'):
                            image.close()

        blocks: list[SourceBlock] = []
        page_confidences: list[float] = []
        markdown_parts: list[str] = []
        total_chars = 0
        page_dimensions = None
        for page_number, image in page_images():
            page_dimensions = page_dimensions or (float(image.width), float(image.height))
            try:
                data: dict[str, list[Any]] = pytesseract.image_to_data(
                    image, lang=self.languages, config="--psm 3", output_type=Output.DICT, timeout=self.timeout,
                )
            except pytesseract.TesseractNotFoundError as exc:
                raise OCRProcessingError("OCR недоступен: в контейнере не найден Tesseract.") from exc
            except (RuntimeError, OSError) as exc:
                raise OCRProcessingError("Tesseract не смог распознать страницу PDF.") from exc
            lines: OrderedDict[tuple[int, int, int], list[tuple[str, float]]] = OrderedDict()
            confidences: list[float] = []
            words = data.get("text", [])
            for index, raw_word in enumerate(words):
                word = _normalise_line(str(raw_word))
                if not word:
                    continue
                try:
                    confidence = float(data.get("conf", ["-1"])[index])
                except (ValueError, TypeError, IndexError):
                    confidence = -1
                if confidence >= 0:
                    confidences.append(confidence)
                key = (
                    int(data.get("block_num", [0])[index]),
                    int(data.get("par_num", [0])[index]),
                    int(data.get("line_num", [0])[index]),
                )
                lines.setdefault(key, []).append((word, confidence))
            page_lines = [_normalise_line(" ".join(word for word, _ in values)) for values in lines.values()]
            page_lines = [line for line in page_lines if line]
            page_text = "\n".join(page_lines)
            if not page_text:
                if progress_callback:
                    progress_callback(page_number, page_count)
                continue
            total_chars += len(page_text)
            if total_chars > self.max_chars:
                raise OCRProcessingError(f"Результат OCR превышает безопасный предел {self.max_chars:,} символов.")
            confidence = sum(confidences) / len(confidences) if confidences else None
            if confidence is not None:
                page_confidences.append(confidence)
            locator = {
                "kind": "pdf", "page": page_number, "label": f"Страница {page_number}",
                "line_start": 1, "line_end": len(page_lines), "char_start": 0, "char_end": len(page_text),
                "source_text": page_text, "ocr": True,
            }
            if confidence is not None:
                locator["ocr_confidence"] = round(confidence, 2)
            blocks.append(SourceBlock(page_text, locator))
            markdown_parts.extend([f"## Страница {page_number}\n", page_text, "\n\n"])
            if progress_callback:
                progress_callback(page_number, page_count)

        if not blocks:
            raise OCRProcessingError("OCR не нашёл читаемого текста. Попробуйте более чёткий скан или PDF с текстовым слоем.")
        average_confidence = sum(page_confidences) / len(page_confidences) if page_confidences else None
        metadata: dict[str, Any] = {
            "page_count": page_count, "ocr_used": True, "ocr_language": self.languages,
            "ocr_page_count": len(blocks), "ocr_char_count": total_chars,
            "ocr_confidence": round(average_confidence, 2) if average_confidence is not None else None,
        }
        if page_dimensions:
            metadata['page_width'], metadata['page_height'] = page_dimensions
        return OCRResult(
            parsed=ParsedDocument("pdf", blocks, metadata), markdown="".join(markdown_parts).rstrip() + "\n",
            engine_version=engine_version, language=self.languages, confidence=metadata["ocr_confidence"],
        )
