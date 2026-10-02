"""Build portable Markdown and PDF reports from persisted document snapshots."""
from __future__ import annotations

import io
import ipaddress
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote, urlsplit
from xml.sax.saxutils import escape

import mistune
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    HRFlowable,
    LongTable,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    TableStyle,
)

from app.services.document_security import download_name

ExportScope = Literal["analysis", "selected_answers", "conversation"]
ExportFormat = Literal["markdown", "pdf"]
MAX_EXPORT_CHARS = 2_000_000
MAX_EXPORT_SOURCES = 5_000
MAX_SOURCE_EXCERPT = 700


class ExportError(ValueError):
    """An export cannot be produced without changing the saved document."""


@dataclass(frozen=True)
class ExportSource:
    id: str
    text: str
    locator: dict[str, Any]
    ordinal: int
    is_derived: bool = False
    version: int = 0
    unavailable: bool = False


@dataclass(frozen=True)
class ExportAnswer:
    key: str
    question: str
    answer: str
    citations: tuple[ExportSource, ...] = ()


@dataclass(frozen=True)
class ExportAdditionalAnalysis:
    id: str
    mode: str
    answer: str
    citations: tuple[ExportSource, ...] = ()
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ExportMessage:
    role: str
    content: str
    citations: tuple[ExportSource, ...] = ()
    created_at: datetime | None = None
    model: str | None = None
    reasoning_effort: str | None = None


@dataclass(frozen=True)
class ExportSnapshot:
    filename: str
    file_type: str
    exported_at: datetime
    processing_version: int
    chunk_version: int
    model: str | None = None
    reasoning_effort: str | None = None
    analysis_source: str = "native_fallback"
    markdown_status: str = "legacy"
    markdown_converter_version: str | None = None
    ocr_status: str = "not_needed"
    ocr_language: str | None = None
    ocr_page_count: int | None = None
    ocr_confidence: float | None = None
    ocr_error: str | None = None
    insights: tuple[ExportAnswer, ...] = ()
    additional_analyses: tuple[ExportAdditionalAnalysis, ...] = ()
    messages: tuple[ExportMessage, ...] = ()
    warnings: tuple[str, ...] = ()


def safe_filename(filename: str, export_format: ExportFormat) -> str:
    suffix = ".md" if export_format == "markdown" else ".pdf"
    return download_name(filename, suffix)


def _safe_link(destination: str) -> bool:
    value = destination.strip().strip("<>")
    if len(value) > 2_048 or any(ord(char) < 0x20 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() == "mailto":
            return bool(parsed.path) and not parsed.netloc
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return False
        if parsed.username or parsed.password:
            return False
        hostname = parsed.hostname.rstrip(".").lower()
        if hostname in {"localhost", "localhost.localdomain"} or hostname.endswith(".localhost"):
            return False
        try:
            address = ipaddress.ip_address(hostname)
        except ValueError:
            return True
        return not (address.is_private or address.is_loopback or address.is_link_local
                    or address.is_reserved or address.is_unspecified)
    except ValueError:
        return False


# Markdown destinations may contain balanced parentheses, for example
# ``[label](https://example.test/path_(part))``. Matching the inner pair keeps
# a rejected link from leaving a dangling ``)`` in the exported text.
_MARKDOWN_LINK = re.compile(r"(!?)\[([^\]]*)\]\(((?:[^()]|\([^()]*\))*)\)")
_RAW_HTML = re.compile(r"<!--.*?-->|<![A-Za-z][^>]*>|</?[A-Za-z][^>]*>", re.DOTALL)
_SCHEME_AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]*:[^<>\s]+)>")


def sanitize_markdown(value: str) -> str:
    """Keep Markdown formatting while making embedded HTML and unsafe links inert."""
    text = value[:MAX_EXPORT_CHARS]

    def clean_link(match: re.Match[str]) -> str:
        image, label, raw_destination = match.groups()
        destination = raw_destination.strip().split(maxsplit=1)[0].strip("<>") if raw_destination.strip() else ""
        if not _safe_link(destination):
            return f"![{label}]" if image else f"[{label}]"
        # Parentheses and whitespace are encoded so the Markdown destination
        # cannot escape its own delimiter or add attributes.
        safe_destination = quote(destination, safe=":/?#[]@!$&'*,;=%+.-_~")
        return f"{image}[{label}]({safe_destination})"

    text = _MARKDOWN_LINK.sub(clean_link, text)
    text = _RAW_HTML.sub(lambda match: escape(match.group(0)), text)
    text = _SCHEME_AUTOLINK.sub(
        lambda match: match.group(0) if _safe_link(match.group(1)) else escape(match.group(0)),
        text,
    )
    return text


def _source_location(source: ExportSource) -> str:
    locator = source.locator or {}
    label = locator.get("label")
    parts = [label.strip()] if isinstance(label, str) and label.strip() else []
    if isinstance(locator.get("page"), int) and not any("Страница" in item for item in parts):
        parts.append(f"Страница {locator['page']}")
    if isinstance(locator.get("sheet"), str):
        row = locator.get("row_start", locator.get("row"))
        row_end = locator.get("row_end", row)
        parts.append(f"Лист «{locator['sheet']}»" + (f", строки {row}–{row_end}" if isinstance(row, int) else ""))
    if isinstance(locator.get("slide"), int) and not any("Слайд" in item for item in parts):
        parts.append(f"Слайд {locator['slide']}")
    if isinstance(locator.get("chapter"), (str, int)) and not any("Глава" in item for item in parts):
        parts.append(f"Глава {locator['chapter']}")
    if isinstance(locator.get("paragraph"), int) and not any("Абзац" in item for item in parts):
        parts.append(f"Абзац {locator['paragraph']}")
    if isinstance(locator.get("line_start"), int) and not any("Строк" in item for item in parts):
        end = locator.get("line_end", locator["line_start"])
        parts.append(f"Строки {locator['line_start']}–{end}")
    if locator.get("path") and not parts:
        parts.append(f"Узел {locator['path']}")
    location = ", ".join(parts) or f"Фрагмент {source.ordinal + 1}"
    if source.version:
        location += f" · версия {source.version}"
    return location


def _confirmation_type(source: ExportSource) -> str:
    if source.unavailable:
        return "Сохранённая ссылка на источник; оригинальный фрагмент недоступен"
    locator = source.locator or {}
    if source.is_derived or locator.get("source_type") == "calculation":
        return "Расчёт приложения"
    if locator.get("ocr") is True or locator.get("source_type") == "pdf_ocr":
        return "Распознанный текст (OCR)"
    quality = locator.get("match_quality")
    return {
        "exact": "Точное совпадение",
        "approximate": "Приблизительная привязка",
        "page_only": "Привязка к области документа",
        "not_found": "Точное место не найдено",
        "calculation": "Расчёт приложения",
    }.get(quality, "Источник документа")


def _excerpt(source: ExportSource) -> str:
    value = source.text.strip()
    value = re.sub(r"\s+", " ", value)
    if len(value) > MAX_SOURCE_EXCERPT:
        value = value[:MAX_SOURCE_EXCERPT].rstrip() + "…"
    return value or "Текст источника не сохранён."


def _warnings(snapshot: ExportSnapshot) -> list[str]:
    warnings = list(snapshot.warnings)
    if snapshot.markdown_status == "fallback":
        warnings.append("MarkItDown не завершил преобразование; анализ создан резервным локальным парсером.")
    elif snapshot.markdown_status == "legacy":
        warnings.append("Документ был обработан до включения версионированного Markdown-экспорта.")
    elif snapshot.markdown_status == "failed":
        warnings.append("Markdown-представление недоступно; отчёт собран из сохранённого индекса.")
    if snapshot.ocr_status == "partial":
        warnings.append("OCR распознал документ не полностью; часть текста может отсутствовать.")
    elif snapshot.ocr_status == "failed":
        warnings.append("OCR не смог распознать часть документа.")
    elif snapshot.ocr_status == "ready":
        confidence = f", средняя уверенность {snapshot.ocr_confidence:.0f}%" if snapshot.ocr_confidence is not None else ""
        pages = f" на {snapshot.ocr_page_count} стр." if snapshot.ocr_page_count is not None else ""
        warnings.append(f"Для текста сканированных страниц использован локальный OCR{pages}{confidence}.")
    return list(dict.fromkeys(warnings))


def _replace_citation_markers(text: str) -> str:
    # Stored answers use the app's private visible citation marker. Exported
    # reports use portable Markdown footnote-style numbers instead.
    return re.sub(r"〔(\d+)〕", r"[\1]", text)


def _source_notes(sources: tuple[ExportSource, ...]) -> str:
    if not sources:
        return ""
    lines = ["**Источники**"]
    for index, source in enumerate(sources, start=1):
        lines.append(f"{index}. **{_source_location(source)} — {_confirmation_type(source)}.**")
        lines.append(f"   > {sanitize_markdown(_excerpt(source))}")
    return "\n".join(lines)


def build_markdown(
    snapshot: ExportSnapshot,
    scope: ExportScope,
    selected_keys: set[str] | None = None,
    selected_additional_ids: set[str] | None = None,
) -> str:
    insights = snapshot.insights
    if scope == "selected_answers":
        selected = selected_keys or set()
        insights = tuple(item for item in insights if item.key in selected)
        if not insights:
            raise ExportError("Выберите хотя бы один ответ для экспорта.")
    selected_additional = set(selected_additional_ids or ())
    additional_analyses = tuple(
        item for item in snapshot.additional_analyses if item.id in selected_additional
    ) if scope in {"analysis", "selected_answers"} else ()
    sections = [
        "# Отчёт по документу",
        "",
        f"**Документ:** {sanitize_markdown(snapshot.filename)}  ",
        f"**Формат оригинала:** {snapshot.file_type.upper()}  ",
        f"**Дата экспорта:** {snapshot.exported_at.astimezone().isoformat(timespec='minutes')}  ",
        f"**Версия обработки:** {snapshot.processing_version} (индекс источников {snapshot.chunk_version})  ",
        f"**Модель:** {sanitize_markdown(snapshot.model or 'не сохранена')}  ",
        f"**Уровень reasoning:** {sanitize_markdown(snapshot.reasoning_effort or 'не сохранён')}  ",
        f"**Источник анализа:** {'MarkItDown' if snapshot.analysis_source == 'markitdown' else 'резервный локальный парсер'}  ",
    ]
    if snapshot.markdown_converter_version:
        sections.append(f"**Версия MarkItDown:** {sanitize_markdown(snapshot.markdown_converter_version)}  ")
    sections.append("")
    warnings = _warnings(snapshot)
    if warnings:
        sections.extend(["## Примечания по обработке", ""])
        sections.extend(f"- {sanitize_markdown(item)}" for item in warnings)
        sections.append("")

    if scope in {"analysis", "selected_answers"}:
        sections.append("## Анализ документа" if scope == "analysis" else "## Выбранные ответы")
        sections.append("")
        for insight in insights:
            sections.extend([
                f"### {sanitize_markdown(insight.question)}",
                "",
                sanitize_markdown(_replace_citation_markers(insight.answer)),
                "",
            ])
            notes = _source_notes(insight.citations)
            if notes:
                sections.extend([notes, ""])
        if additional_analyses:
            sections.extend(["## Дополнительные результаты", ""])
            mode_titles = {"brief": "Кратко", "detailed": "Подробно", "tasks": "Задачи", "risks": "Риски и неясности"}
            for item in additional_analyses:
                sections.extend([
                    f"### {mode_titles.get(item.mode, 'Дополнительный анализ')}",
                    "",
                    sanitize_markdown(_replace_citation_markers(item.answer)),
                    "",
                ])
                if item.model or item.reasoning_effort:
                    sections.extend([
                        f"_Модель: {sanitize_markdown(item.model or 'не сохранена')} · reasoning: {sanitize_markdown(item.reasoning_effort or 'не сохранён')}._",
                        "",
                    ])
                notes = _source_notes(item.citations)
                if notes:
                    sections.extend([notes, ""])
    else:
        if not snapshot.messages:
            raise ExportError("В переписке пока нет сообщений для экспорта.")
        sections.extend(["## Переписка", ""])
        for message in snapshot.messages:
            author = "Вы" if message.role == "user" else "Ассистент"
            timestamp = message.created_at.astimezone().strftime("%d.%m.%Y %H:%M") if message.created_at else "время не сохранено"
            sections.extend([f"### {author} · {timestamp}", ""])
            sections.append(sanitize_markdown(_replace_citation_markers(message.content)))
            if message.role == "assistant":
                model = message.model or "не сохранена"
                effort = message.reasoning_effort or "не сохранён"
                sections.extend(["", f"_Модель: {sanitize_markdown(model)} · reasoning: {sanitize_markdown(effort)}._"])
            sections.append("")
            notes = _source_notes(message.citations)
            if notes:
                sections.extend([notes, ""])

    output = "\n".join(sections).rstrip() + "\n"
    if len(output) > MAX_EXPORT_CHARS:
        raise ExportError("Отчёт слишком велик. Выберите меньше ответов и повторите экспорт.")
    return output


def _font_path(candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    return None


def _register_fonts() -> None:
    fonts = {
        "ExportSans": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "C:/Windows/Fonts/arial.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", "C:/Windows/Fonts/arialbd.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Oblique.ttf", "C:/Windows/Fonts/ariali.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSans-BoldOblique.ttf", "C:/Windows/Fonts/arialbi.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
        ),
        "ExportMono": (
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf", "C:/Windows/Fonts/consola.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf", "C:/Windows/Fonts/consolab.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Oblique.ttf", "C:/Windows/Fonts/consolai.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"),
            ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-BoldOblique.ttf", "C:/Windows/Fonts/consolaz.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf"),
        ),
    }
    for family, variants in fonts.items():
        selected = [_font_path(paths) for paths in variants]
        if any(path is None for path in selected):
            raise ExportError("Не найден шрифт для корректного отображения текста отчёта.")
        for suffix, path in zip(("", "-Bold", "-Italic", "-BoldItalic"), selected, strict=True):
            name = family + suffix
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
        pdfmetrics.registerFontFamily(family, normal=family, bold=family + "-Bold",
                                     italic=family + "-Italic", boldItalic=family + "-BoldItalic")


def _inline_markup(nodes: list[dict[str, Any]] | None) -> str:
    output: list[str] = []
    for node in nodes or []:
        kind = node.get("type")
        raw = str(node.get("raw", ""))
        if kind == "text":
            output.append(escape(_replace_citation_markers(raw)).replace("\n", "<br/>"))
        elif kind in {"softbreak", "linebreak"}:
            output.append("<br/>")
        elif kind in {"inline_html", "block_html"}:
            output.append(escape(raw))
        elif kind == "codespan":
            output.append(f'<font name="ExportMono" backColor="#f2f2f2">{escape(raw)}</font>')
        elif kind in {"strong", "emphasis", "strikethrough"}:
            tag = {"strong": "b", "emphasis": "i", "strikethrough": "strike"}[kind]
            output.append(f"<{tag}>{_inline_markup(node.get('children'))}</{tag}>")
        elif kind == "link":
            label = _inline_markup(node.get("children"))
            destination = str((node.get("attrs") or {}).get("url", ""))
            safe_href = escape(destination, {'"': "&quot;", "'": "&apos;"})
            output.append(f'<link href="{safe_href}">{label}</link>' if _safe_link(destination) else label)
        elif kind == "image":
            output.append(escape(" ".join(child.get("raw", "") for child in node.get("children", []))))
        else:
            output.append(_inline_markup(node.get("children")))
    return "".join(output)


def _paragraph_text(nodes: list[dict[str, Any]] | None) -> str:
    return _inline_markup(nodes) or " "


def _build_story(markdown: str, snapshot: ExportSnapshot) -> list[Any]:
    parser = mistune.create_markdown(renderer="ast", plugins=["table", "strikethrough", "task_lists", "url"])
    tree = parser(markdown)
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ExportTitle", parent=styles["Title"], fontName="ExportSans-Bold", fontSize=19,
                              leading=25, textColor=colors.HexColor("#0a0a0a"), alignment=TA_LEFT, spaceAfter=10))
    styles.add(ParagraphStyle(name="ExportHeading", parent=styles["Heading2"], fontName="ExportSans-Bold", fontSize=13,
                              leading=17, textColor=colors.HexColor("#111111"), spaceBefore=9, spaceAfter=5,
                              keepWithNext=True))
    styles.add(ParagraphStyle(name="ExportBody", parent=styles["BodyText"], fontName="ExportSans", fontSize=9.5,
                              leading=14, textColor=colors.HexColor("#242424"), spaceAfter=6, splitLongWords=1))
    styles.add(ParagraphStyle(name="ExportSmall", parent=styles["BodyText"], fontName="ExportSans", fontSize=8,
                              leading=11, textColor=colors.HexColor("#5b5b5b"), spaceAfter=3, splitLongWords=1))
    styles.add(ParagraphStyle(name="ExportCode", parent=styles["Code"], fontName="ExportMono", fontSize=7.5,
                              leading=10, leftIndent=7, rightIndent=7, borderPadding=6,
                              backColor=colors.HexColor("#f4f4f4"), splitLongWords=1))
    styles.add(ParagraphStyle(name="ExportQuote", parent=styles["BodyText"], fontName="ExportSans", fontSize=8.5,
                              leading=12, leftIndent=14, textColor=colors.HexColor("#4f4f4f"),
                              borderColor=colors.HexColor("#d8d8d8"), borderWidth=0.7, borderPadding=6, spaceAfter=5,
                              splitLongWords=1))
    styles.add(ParagraphStyle(name="ExportTable", parent=styles["BodyText"], fontName="ExportSans", fontSize=7.5,
                              leading=10, textColor=colors.HexColor("#202020"), splitLongWords=1))
    styles.add(ParagraphStyle(name="ExportTableHead", parent=styles["ExportTable"], fontName="ExportSans-Bold",
                              textColor=colors.HexColor("#111111")))
    styles.add(ParagraphStyle(name="ExportList", parent=styles["BodyText"], fontName="ExportSans", fontSize=9.5,
                              leading=14, leftIndent=11, firstLineIndent=-8, spaceAfter=3, splitLongWords=1))

    story: list[Any] = []
    table_width = A4[0] - 2 * 42

    def append_node(node: dict[str, Any], depth: int = 0) -> None:
        kind = node.get("type")
        children = node.get("children") or []
        if kind == "blank_line":
            return
        if kind == "heading":
            level = int((node.get("attrs") or {}).get("level", 2))
            story.append(Paragraph(_paragraph_text(children), styles["ExportTitle"] if level <= 1 else styles["ExportHeading"]))
        elif kind in {"paragraph", "block_text"}:
            story.append(Paragraph(_paragraph_text(children), styles["ExportBody"]))
        elif kind == "block_code":
            raw = str(node.get("raw", ""))
            if raw.strip():
                story.append(Preformatted(escape(raw[:100_000]), styles["ExportCode"], maxLineLength=120))
                story.append(Spacer(1, 5))
        elif kind in {"block_html", "inline_html"}:
            raw = str(node.get("raw", "")).strip()
            if raw:
                story.append(Paragraph(escape(raw[:10_000]).replace("\n", "<br/>"), styles["ExportBody"]))
        elif kind == "thematic_break":
            story.append(HRFlowable(width="100%", thickness=.5, color=colors.HexColor("#dddddd"), spaceBefore=4, spaceAfter=8))
        elif kind == "block_quote":
            inner = [Paragraph(_paragraph_text(item.get("children")), styles["ExportQuote"])
                     for item in children if item.get("type") in {"paragraph", "block_text"}]
            if inner:
                story.extend(inner)
            else:
                for child in children:
                    append_node(child, depth + 1)
        elif kind == "list":
            attrs = node.get("attrs") or {}
            ordered = bool(attrs.get("ordered"))
            start = int(attrs.get("start", 1))
            for offset, item in enumerate(children):
                item_children = item.get("children") or []
                prefix = f"{start + offset}." if ordered else "•"
                first_block = True
                for child in item_children:
                    if first_block and child.get("type") in {"block_text", "paragraph"}:
                        story.append(Paragraph(f"{prefix} {_inline_markup(child.get('children'))}", styles["ExportList"]))
                        first_block = False
                    else:
                        append_node(child, depth + 1)
                if first_block:
                    story.append(Paragraph(prefix, styles["ExportList"]))
        elif kind == "table":
            rows: list[list[Any]] = []
            for row in children:
                if row.get("type") == "table_head":
                    rows.append([Paragraph(_paragraph_text(cell.get("children")), styles["ExportTableHead"])
                                 for cell in row.get("children", [])])
                elif row.get("type") == "table_body":
                    for body_row in row.get("children", []):
                        rows.append([Paragraph(_paragraph_text(cell.get("children")), styles["ExportTable"])
                                     for cell in body_row.get("children", [])])
            if rows:
                columns = max(len(row) for row in rows)
                for row in rows:
                    row.extend([Paragraph("", styles["ExportTable"])] * (columns - len(row)))
                table = LongTable(rows, colWidths=[table_width / columns] * columns, repeatRows=1,
                                  hAlign="LEFT", splitByRow=1)
                table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f2f2f2")),
                    ("GRID", (0, 0), (-1, -1), .35, colors.HexColor("#dddddd")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 5), ("RIGHTPADDING", (0, 0), (-1, -1), 5),
                    ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.extend([table, Spacer(1, 7)])
        elif kind == "list_item":
            append_node({"type": "list", "attrs": {"ordered": False}, "children": [node]}, depth)
        else:
            for child in children:
                append_node(child, depth + 1)

    for node in tree:
        append_node(node)
    return story


def build_pdf(
    snapshot: ExportSnapshot,
    scope: ExportScope,
    selected_keys: set[str] | None = None,
    selected_additional_ids: set[str] | None = None,
) -> bytes:
    markdown = build_markdown(snapshot, scope, selected_keys, selected_additional_ids)
    _register_fonts()
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=42, leftMargin=42, topMargin=44, bottomMargin=42,
                            title=f"Отчёт — {Path(snapshot.filename).name}", author="AI Document Checker",
                            pageCompression=1, allowSplitting=1)
    story = _build_story(markdown, snapshot)

    def footer(canvas, document) -> None:
        canvas.saveState()
        canvas.setFont("ExportSans", 7)
        canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawString(42, 24, "AI Document Checker · локальный экспорт")
        canvas.drawRightString(A4[0] - 42, 24, str(document.page))
        canvas.restoreState()

    try:
        doc.build(story, onFirstPage=footer, onLaterPages=footer)
    except ExportError:
        raise
    except Exception as exc:
        raise ExportError("Не удалось сформировать PDF. Попробуйте Markdown или повторите экспорт.") from exc
    content = buffer.getvalue()
    if len(content) > MAX_EXPORT_CHARS * 2:
        raise ExportError("PDF-отчёт превысил безопасный размер.")
    return content


def render_export(snapshot: ExportSnapshot, scope: ExportScope, export_format: ExportFormat,
                  selected_keys: set[str] | None = None,
                  selected_additional_ids: set[str] | None = None) -> bytes:
    if export_format == "markdown":
        return build_markdown(snapshot, scope, selected_keys, selected_additional_ids).encode("utf-8")
    return build_pdf(snapshot, scope, selected_keys, selected_additional_ids)
