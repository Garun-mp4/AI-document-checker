from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.markdown_mapping import map_markdown
from app.services.markitdown_service import MarkdownConversionError, MarkItDownService
from app.services.parsing import SourceBlock

FIXTURES = Path(__file__).parent / "fixtures"
SUPPORTED_FIXTURES = [
    "sample.pdf", "sample.docx", "sample.txt", "sample.md", "sample.csv", "sample.xml",
    "sample.xlsx", "sample.xls", "sample.pptx", "sample.html", "sample.json", "sample.epub",
]


@pytest.mark.parametrize("filename", SUPPORTED_FIXTURES)
def test_markitdown_creates_markdown_for_every_supported_fixture(filename: str, tmp_path: Path) -> None:
    source = FIXTURES / filename
    target = tmp_path / filename
    target.write_bytes(source.read_bytes())

    result = MarkItDownService(tmp_path).convert_local(target)

    assert result.markdown.strip()
    assert len(result.markdown) < 5_000_000


def test_markitdown_rejects_paths_outside_upload_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(MarkdownConversionError, match="хранилища"):
        MarkItDownService(tmp_path).convert_local(outside)


def test_markitdown_failure_is_a_domain_error(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    target = tmp_path / "sample.txt"
    target.write_text("Текст", encoding="utf-8")
    service = MarkItDownService(tmp_path)
    service._converter = SimpleNamespace(convert_local=lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))  # type: ignore[assignment]

    with pytest.raises(MarkdownConversionError, match="MarkItDown"):
        service.convert_local(target)


def test_mapping_keeps_original_locator_and_markdown_range() -> None:
    native = [
        SourceBlock("Автор документа — Алексей", {"kind": "docx", "label": "Абзац 1", "paragraph": 1}),
        SourceBlock("Срок проекта: 30 ноября 2026 года", {"kind": "docx", "label": "Абзац 2", "paragraph": 2}),
    ]

    mapped, sidecar = map_markdown("# Проект\n\nАвтор документа — Алексей\n\nСрок проекта: 30 ноября 2026 года\n", native)

    assert mapped
    assert any(block.locator.get("label") == "Абзац 1" for block in mapped)
    assert all(block.locator.get("markdown_line_start") for block in mapped)
    assert sidecar["quality"]["exact"] == 3


def test_mapping_reports_no_match_without_fabricating_source() -> None:
    mapped, sidecar = map_markdown("ZXQVV 12345\n", [SourceBlock("Оригинальный текст", {"kind": "txt", "line_start": 1})])

    assert mapped[0].confidence == "none"
    assert mapped[0].locator.get("source_text") is None
    assert sidecar["quality"]["none"] == 1
