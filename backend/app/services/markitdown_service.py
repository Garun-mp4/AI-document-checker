from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from pathlib import Path

from markitdown import MarkItDown

from app.config import settings
from app.services.artifact_cache import (
    file_checksum,
    load_json_cache,
    processing_cache_key,
    store_json_cache,
)

MARKITDOWN_VERSION = "0.1.8"


class MarkdownConversionError(ValueError):
    """A safe, user-facing MarkItDown conversion failure."""


@dataclass(frozen=True)
class MarkdownResult:
    markdown: str
    title: str | None
    converter_version: str = MARKITDOWN_VERSION


class MarkItDownService:
    """Run MarkItDown against a validated local upload.

    The converter is intentionally kept behind this small service so the API
    never shells out to a host CLI or accepts arbitrary URLs from a client.
    """

    def __init__(self, upload_root: str | Path | None = None) -> None:
        self.upload_root = Path(upload_root or settings.upload_dir).resolve()
        self._converter: MarkItDown | None = None

    @property
    def converter(self) -> MarkItDown:
        if self._converter is None:
            self._converter = MarkItDown(enable_plugins=False)
        return self._converter

    def convert_local(self, path: Path) -> MarkdownResult:
        from app.services.document_security import storage_path
        from app.services.parsing import DocumentParsingError
        try:
            resolved = storage_path(path, self.upload_root)
        except DocumentParsingError as exc:
            raise MarkdownConversionError('Путь к документу находится за пределами хранилища.') from exc
        if not resolved.is_file():
            raise MarkdownConversionError("Исходный файл документа недоступен.")
        try:
            result = self.converter.convert_local(str(resolved))
            markdown = self._normalize(result.markdown)
        except MarkdownConversionError:
            raise
        except Exception as exc:
            raise MarkdownConversionError(self._message(exc)) from exc
        if not markdown:
            raise MarkdownConversionError("MarkItDown не извлёк текст из документа.")
        if len(markdown) > settings.markdown_max_chars:
            raise MarkdownConversionError("Сформированный Markdown превышает безопасный размер.")
        return MarkdownResult(markdown=markdown, title=getattr(result, "title", None))

    async def convert(self, path: Path, *, cache_checksum: str | None = None) -> MarkdownResult:
        from app.services.isolated_documents import run_document_operation
        from app.services.parsing import DocumentParsingError
        checksum = cache_checksum or await asyncio.to_thread(file_checksum, path)
        key = processing_cache_key(
            input_checksum=checksum,
            file_type=path.suffix.lower().lstrip("."),
            configuration={"plugins": False, "max_chars": settings.markdown_max_chars},
            parser_version="markitdown-wrapper-m14-v1",
            converter_version=MARKITDOWN_VERSION,
        )
        cached = load_json_cache(settings.upload_dir, "markdown", key)
        if cached is not None:
            try:
                result = MarkdownResult(**cached)
                if result.markdown:
                    return result
            except (TypeError, ValueError):
                pass
        try:
            result = MarkdownResult(**await run_document_operation('markdown', path, timeout=settings.markdown_timeout_seconds))
        except DocumentParsingError as exc:
            raise MarkdownConversionError(str(exc)) from exc
        if result.markdown:
            store_json_cache(settings.upload_dir, "markdown", key, {
                "markdown": result.markdown,
                "title": result.title,
                "converter_version": result.converter_version,
            })
        return result

    @staticmethod
    def _normalize(value: str | None) -> str:
        text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip() + ("\n" if text.strip() else "")

    @staticmethod
    def _message(exc: Exception) -> str:
        message = str(exc).strip().lower()
        if "password" in message or "encrypted" in message:
            return "MarkItDown не смог открыть защищённый документ."
        if "pdf" in message and ("text" in message or "extract" in message):
            return "MarkItDown не нашёл извлекаемый текст в PDF. Возможно, это скан без OCR."
        return "MarkItDown не смог преобразовать документ в Markdown."
