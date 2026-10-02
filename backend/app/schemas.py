from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class OcrReprocessIn(BaseModel):
    language: Literal["rus", "eng", "rus+eng"] = "rus+eng"
    quality: Literal["fast", "balanced", "high"] = "balanced"
    pages: list[int] | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("pages")
    @classmethod
    def validate_pages(cls, pages: list[int] | None) -> list[int] | None:
        if pages is not None and (any(page < 1 for page in pages) or len(set(pages)) != len(pages)):
            raise ValueError("Укажите уникальные положительные номера страниц.")
        return sorted(pages) if pages is not None else None


class DocumentOut(BaseModel):
    active_version: int = 0
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
    ocr_status: Literal["not_needed", "processing", "ready", "partial", "failed"]
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
    line_offset: int
    total_chars: int
    total_lines: int
    checksum: str | None
    mapping_quality: dict[str, int]
    error: str | None


class DocumentSearchMatchOut(BaseModel):
    id: str
    text: str
    snippet: str
    locator: dict[str, Any]
    ordinal: int
    is_derived: bool = False
    match_start: int | None = None
    match_end: int | None = None
    markdown_start: int | None = None
    markdown_end: int | None = None


class DocumentSearchOut(BaseModel):
    document_id: str
    scope: Literal["original", "markdown"]
    query: str
    total: int
    offset: int
    limit: int
    matches: list[DocumentSearchMatchOut]


class TableFormulaCellOut(BaseModel):
    column_index: int
    formula: str
    has_cached_value: bool


class TablePreviewRowOut(BaseModel):
    number: int
    cells: list[str]
    formula_cells: list[TableFormulaCellOut] = Field(default_factory=list)


class TableFilterOut(BaseModel):
    column_index: int = Field(ge=0, le=499)
    kind: Literal["text", "number", "empty"]
    operator: Literal["contains", "equals", "gt", "gte", "lt", "lte", "is_empty", "is_not_empty"]
    value: str = ""


class TableFilterIn(TableFilterOut):
    @model_validator(mode="after")
    def validate_filter(self) -> TableFilterIn:
        allowed = {
            "text": {"contains", "equals"},
            "number": {"equals", "gt", "gte", "lt", "lte"},
            "empty": {"is_empty", "is_not_empty"},
        }
        if self.operator not in allowed[self.kind]:
            raise ValueError("Условие не соответствует типу фильтра.")
        if self.kind == "text" and not self.value.strip():
            raise ValueError("Введите текст для фильтра.")
        if self.kind == "number" and not self.value.strip():
            raise ValueError("Введите число для фильтра.")
        if self.kind == "empty" and self.value:
            raise ValueError("Фильтр пустых значений не принимает значение.")
        if len(self.value) > 256:
            raise ValueError("Значение фильтра не должно превышать 256 символов.")
        return self


class TableCalculationIn(BaseModel):
    sheet: str | None = Field(default=None, max_length=128)
    column_index: int = Field(ge=0, le=499)
    filter: TableFilterIn | None = None


class TablePreviewOut(BaseModel):
    columns: list[str]
    rows: list[TablePreviewRowOut]
    offset: int
    limit: int
    total_rows: int
    filtered_rows: int
    sheet: str | None = None
    available_sheets: list[str] = Field(default_factory=list)
    delimiter: str | None = None
    column_kinds: list[str] = Field(default_factory=list)
    formula_policy: Literal["not_applicable", "detected", "cached_values_only"]
    sort_column: int | None = None
    sort_direction: Literal["asc", "desc"] | None = None
    filter: TableFilterOut | None = None
    focus_row: int | None = None
    focus_row_visible: bool | None = None


class TableAggregateOut(BaseModel):
    count: int
    non_empty_count: int
    numeric_count: int
    nonnumeric_count: int
    formula_count: int
    formula_cache_missing_count: int
    sum: str | None
    average: str | None
    minimum: str | None
    maximum: str | None
    scope: Literal["document", "current_filter"]
    source_row_count: int
    source_row_start: int | None = None
    source_row_end: int | None = None


class TableCalculationOut(BaseModel):
    sheet: str | None = None
    available_sheets: list[str] = Field(default_factory=list)
    column_index: int
    column: str
    filter: TableFilterOut | None = None
    formula_policy: Literal["not_applicable", "detected", "cached_values_only"]
    rounding_rule: str
    document: TableAggregateOut
    filtered: TableAggregateOut
    document_source: SourceOut
    filtered_source: SourceOut


class InsightOut(BaseModel):
    id: str
    key: str
    question: str
    answer: str
    citations: list[SourceOut]
    version: int | None = None
    source_version: int | None = None
    model: str | None = None
    reasoning_effort: str | None = None


class MessageOut(BaseModel):
    id: str
    role: str
    content: str
    citations: list[SourceOut]
    model: str | None = None
    reasoning_effort: str | None = None
    created_at: datetime
    context_epoch: int = 0
    reply_to_message_id: str | None = None
    generation_status: Literal["complete", "streaming", "interrupted"] = "complete"
    generation_error: str | None = None
    source_version: int | None = None


class DocumentExportIn(BaseModel):
    scope: Literal["analysis", "selected_answers", "conversation"]
    format: Literal["markdown", "pdf"] = "markdown"
    selected_keys: list[str] = Field(default_factory=list, max_length=7)

    @field_validator("selected_keys")
    @classmethod
    def validate_selected_keys(cls, keys: list[str]) -> list[str]:
        if len(keys) != len(set(keys)) or any(not key or len(key) > 40 for key in keys):
            raise ValueError("Укажите уникальные ключи ответов.")
        return keys

    @model_validator(mode="after")
    def validate_scope(self) -> DocumentExportIn:
        if self.scope == "selected_answers" and not self.selected_keys:
            raise ValueError("Выберите хотя бы один ответ для экспорта.")
        if self.scope != "selected_answers" and self.selected_keys:
            raise ValueError("Список ответов допустим только для выбранного экспорта.")
        return self


class ChatOut(BaseModel):
    id: str
    document_id: str
    context_epoch: int = 0


class StartChatContextOut(BaseModel):
    context_epoch: int
    preserved_message_count: int


class DeleteMessagesOut(BaseModel):
    deleted_ids: list[str]


class ReanalyzeIn(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    reasoning_effort: str = Field(min_length=1, max_length=20)
    expected_source_version: int = Field(ge=1)


class DocumentAnalysisVersionOut(BaseModel):
    number: int
    source_version: int
    state: str
    model: str | None = None
    reasoning_effort: str | None = None
    created_at: datetime
    is_active: bool


class ChatSummaryOut(BaseModel):
    """A durable library entry representing one document conversation."""

    id: str
    document_id: str
    title: str
    custom_title: str | None = None
    pinned: bool = False
    revision: int = 1
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
    search_snippet: str | None = None
    search_message_id: str | None = None


class ChatLibraryPageOut(BaseModel):
    items: list[ChatSummaryOut]
    total: int
    offset: int
    limit: int
    has_more: bool


class ChatSettingsOut(BaseModel):
    id: str
    document_id: str
    title: str
    custom_title: str | None
    pinned: bool
    revision: int
    context_epoch: int = 0


class ChatUpdateIn(BaseModel):
    expected_revision: int = Field(ge=1)
    title: str | None = Field(default=None, max_length=72)
    pinned: bool | None = None

    @field_validator("title")
    @classmethod
    def normalize_title(cls, title: str | None) -> str | None:
        if title is None:
            return None
        normalized = title.strip()
        if not normalized:
            raise ValueError("Название чата не может быть пустым.")
        if len(normalized) > 72:
            raise ValueError("Название чата не должно превышать 72 символа.")
        return normalized

    @model_validator(mode="after")
    def require_an_update(self) -> ChatUpdateIn:
        has_title_update = "title" in self.model_fields_set
        has_pin_update = "pinned" in self.model_fields_set and self.pinned is not None
        if not has_title_update and not has_pin_update:
            raise ValueError("Укажите новое название или состояние закрепления.")
        return self


class SendMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)


class CodexPreferencesIn(BaseModel):
    model: str = Field(min_length=1, max_length=100)
    reasoning_effort: str = Field(min_length=1, max_length=20)


class ProcessingJobOut(BaseModel):
    id: str
    operation: Literal['process', 'analysis']
    version: int
    input_version: str | None
    state: Literal['queued', 'running', 'cancelling', 'cancelled', 'succeeded', 'failed']
    stage: str
    progress: dict[str, Any]
    attempts: int
    max_attempts: int
    heartbeat: datetime | None
    queued_at: datetime
    started_at: datetime | None
    stage_started_at: datetime | None
    lease_until: datetime | None
    queue_position: int | None
    queue_wait_seconds: int | None
    stage_elapsed_seconds: int | None
    error: str | None
    error_code: str | None
    parameters: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


class AppVersionOut(BaseModel):
    service: Literal['api'] = 'api'
    version: str
    build_id: str
    commit: str
    built_at: str
