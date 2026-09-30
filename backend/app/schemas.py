from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class DocumentOut(BaseModel):
    id: str
    filename: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None
    chunk_count: int
    metadata: dict[str, Any]
    markdown_status: Literal["pending", "ready", "fallback", "failed", "legacy"]
    analysis_source: Literal["markitdown", "native_fallback", "ocr"]
    markdown_error: str | None
    markdown_converter_version: str | None
    markdown_char_count: int
    markdown_line_count: int
    markdown_checksum: str | None
    markdown_mapping: dict[str, Any]
    ocr_status: Literal["not_needed", "processing", "ready", "failed"]
    ocr_language: str | None
    ocr_page_count: int | None
    ocr_confidence: float | None
    ocr_error: str | None
    ocr_engine_version: str | None
    ocr_char_count: int
    created_at: datetime
    updated_at: datetime


class SourceOut(BaseModel):
    id: str
    text: str
    locator: dict[str, Any]
    ordinal: int
    is_derived: bool


class PreviewBlockOut(BaseModel):
    """A visible document block that can be linked to a citation."""

    id: str
    source_id: str
    ordinal: int
    kind: Literal["page", "paragraph", "table", "row", "node", "text", "calculation"]
    text: str
    locator: dict[str, Any]
    rows: list[list[str]] | None = None


class DocumentPreviewOut(BaseModel):
    document_id: str
    file_type: str
    renderer: Literal["pdf", "docx", "text", "csv", "xml", "xlsx", "xls", "pptx", "html", "json", "epub"]
    layout: Literal["pdf", "paper", "table", "tree", "slides"]
    aspect_ratio: float
    page_count: int | None = None
    original_url: str | None = None
    encoding: str | None = None
    source_count: int
    blocks: list[PreviewBlockOut]
    total_blocks: int
    truncated: bool = False


class MarkdownOut(BaseModel):
    document_id: str
    status: Literal["pending", "ready", "fallback", "failed", "legacy"]
    source: Literal["markitdown", "native_fallback", "ocr"]
    converter_version: str | None
    markdown: str
    offset: int
    limit: int
    total_chars: int
    total_lines: int
    checksum: str | None
    mapping_quality: dict[str, int]
    error: str | None


class TablePreviewRowOut(BaseModel):
    number: int
    cells: list[str]


class TablePreviewOut(BaseModel):
    columns: list[str]
    rows: list[TablePreviewRowOut]
    offset: int
    limit: int
    total_rows: int


class InsightOut(BaseModel):
    id: str
    key: str
    question: str
    answer: str
    citations: list[SourceOut]


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[SourceOut]
    created_at: datetime


class ChatOut(BaseModel):
    id: str
    document_id: str


class ChatSummaryOut(BaseModel):
    """A durable library entry representing one document conversation."""

    id: str
    document_id: str
    title: str
    filename: str
    file_type: str
    file_size: int
    status: str
    error_message: str | None
    chunk_count: int
    metadata: dict[str, Any]
    created_at: datetime
    last_activity_at: datetime
    message_count: int
    last_message_at: datetime | None = None
    last_message_preview: str | None = None


class SendMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)


class CodexPreferencesIn(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    reasoning_effort: str = Field(min_length=1, max_length=20)
