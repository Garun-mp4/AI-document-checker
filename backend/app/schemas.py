from __future__ import annotations

from datetime import datetime
from typing import Any

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
    created_at: datetime
    updated_at: datetime


class SourceOut(BaseModel):
    id: str
    text: str
    locator: dict[str, Any]
    ordinal: int
    is_derived: bool


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


class SendMessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4_000)
