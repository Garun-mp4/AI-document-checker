from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(Text, nullable=False)
    file_type: Mapped[str] = mapped_column(String(12), nullable=False)
    file_size: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="queued", index=True)
    error_message: Mapped[str | None] = mapped_column(Text)
    chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    active_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    next_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    input_checksum: Mapped[str | None] = mapped_column(String(64))
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    markdown_status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    analysis_source: Mapped[str] = mapped_column(String(24), nullable=False, default="native_fallback")
    markdown_path: Mapped[str | None] = mapped_column(Text)
    markdown_map_path: Mapped[str | None] = mapped_column(Text)
    markdown_error: Mapped[str | None] = mapped_column(Text)
    markdown_converter_version: Mapped[str | None] = mapped_column(String(32))
    markdown_char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    markdown_line_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    markdown_checksum: Mapped[str | None] = mapped_column(String(64))
    markdown_mapping_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    ocr_status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_needed", index=True)
    ocr_language: Mapped[str | None] = mapped_column(String(32))
    ocr_page_count: Mapped[int | None] = mapped_column(Integer)
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_error: Mapped[str | None] = mapped_column(Text)
    ocr_engine_version: Mapped[str | None] = mapped_column(String(32))
    ocr_char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())

    chunks: Mapped[list[Chunk]] = relationship(back_populates="document", cascade="all, delete-orphan")
    chat: Mapped[Chat | None] = relationship(back_populates="document", cascade="all, delete-orphan", uselist=False)
    insights: Mapped[list[Insight]] = relationship(back_populates="document", cascade="all, delete-orphan")


class Chunk(Base):
    __tablename__ = "chunks"
    __table_args__ = (UniqueConstraint('document_id', 'version', 'ordinal', name='uq_chunks_version_ordinal'),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(384))
    is_derived: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    content_source: Mapped[str] = mapped_column(String(24), nullable=False, default="native")
    markdown_line_start: Mapped[int | None] = mapped_column(Integer)
    markdown_line_end: Mapped[int | None] = mapped_column(Integer)
    markdown_char_start: Mapped[int | None] = mapped_column(Integer)
    markdown_char_end: Mapped[int | None] = mapped_column(Integer)
    mapping_confidence: Mapped[str | None] = mapped_column(String(16))

    document: Mapped[Document] = relationship(back_populates="chunks")


class Chat(Base):
    __tablename__ = "chats"
    __table_args__ = (
        CheckConstraint("scope IN ('document','application')", name="ck_chats_scope"),
        CheckConstraint(
            "(scope = 'document' AND document_id IS NOT NULL) OR "
            "(scope = 'application' AND document_id IS NULL)",
            name="ck_chats_scope_document",
        ),
        Index(
            "uq_chats_one_document_chat",
            "document_id",
            unique=True,
            postgresql_where=text("scope = 'document' AND document_id IS NOT NULL"),
            sqlite_where=text("scope = 'document' AND document_id IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    scope: Mapped[str] = mapped_column(String(16), nullable=False, default="document", server_default="document")
    document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=True)
    codex_thread_id: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(String(72))
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default='1')
    context_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())

    document: Mapped[Document | None] = relationship(back_populates="chat")
    messages: Mapped[list[Message]] = relationship(back_populates="chat", cascade="all, delete-orphan", order_by="Message.created_at")


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("generation_status IN ('complete','streaming','interrupted')", name='ck_messages_generation_status'),
        Index('ix_messages_chat_epoch', 'chat_id', 'context_epoch', 'created_at'),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    chat_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chats.id", ondelete="CASCADE"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    model: Mapped[str | None] = mapped_column(String(120))
    reasoning_effort: Mapped[str | None] = mapped_column(String(20))
    context_epoch: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    reply_to_message_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey('messages.id', ondelete='CASCADE'), index=True)
    generation_status: Mapped[str] = mapped_column(String(16), nullable=False, default='complete', server_default='complete')
    generation_error: Mapped[str | None] = mapped_column(Text)
    source_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())

    chat: Mapped[Chat] = relationship(back_populates="messages")


class Insight(Base):
    __tablename__ = "insights"
    __table_args__ = (UniqueConstraint('document_id', 'version', 'key', name='uq_insights_version_key'),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default='0')
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())

    document: Mapped[Document] = relationship(back_populates="insights")


class DocumentBookmark(Base):
    __tablename__ = "document_bookmarks"
    __table_args__ = (UniqueConstraint("document_id", "source_id", name="uq_document_bookmarks_source"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("chunks.id", ondelete="CASCADE"), nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now, server_default=func.now())


class AdditionalAnalysis(Base):
    __tablename__ = "additional_analyses"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "analysis_version"],
            ["document_versions.document_id", "document_versions.number"],
            ondelete="CASCADE",
            name="fk_additional_analyses_document_version",
        ),
        CheckConstraint("mode IN ('brief','detailed','tasks','risks')", name="ck_additional_analyses_mode"),
        Index("ix_additional_analyses_document_version_created", "document_id", "analysis_version", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    analysis_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    mode: Mapped[str] = mapped_column(String(24), nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
    citations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    model: Mapped[str] = mapped_column(String(120), nullable=False)
    reasoning_effort: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())


class DocumentVersion(Base):
    __tablename__ = 'document_versions'
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), primary_key=True)
    number: Mapped[int] = mapped_column(Integer, primary_key=True)
    chunk_version: Mapped[int] = mapped_column(Integer, nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='staging')
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    analysis_model: Mapped[str | None] = mapped_column(String(120))
    analysis_reasoning_effort: Mapped[str | None] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())


class ProcessingJob(Base):
    __tablename__ = 'processing_jobs'
    __table_args__ = (
        Index('uq_jobs_active_document', 'document_id', unique=True,
              postgresql_where=text("state IN ('queued','running','cancelling')")),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('documents.id', ondelete='CASCADE'), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    input_version: Mapped[str | None] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16), nullable=False, default='queued', index=True)
    stage: Mapped[str] = mapped_column(String(32), nullable=False, default='queued')
    progress: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=3)
    heartbeat: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stage_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    owner: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    error: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(40))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MaintenanceEvent(Base):
    """Privacy-safe audit record for local data maintenance operations."""

    __tablename__ = 'maintenance_events'
    __table_args__ = (Index('ix_maintenance_events_created_at', 'created_at'),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_changed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utc_now, server_default=func.now())
