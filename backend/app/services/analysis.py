from __future__ import annotations

import json
import logging
import uuid
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import delete, select

from app.config import settings
from app.database import SessionLocal
from app.models import Chunk, Document, Insight
from app.services.citations import format_source_markers
from app.services.retrieval import search_chunks

if TYPE_CHECKING:
    from app.services.codex import CodexService

logger = logging.getLogger(__name__)

GENERAL_QUESTIONS = [
    {"key": "overview", "title": "О чём документ?", "question": "Кратко опиши тему документа и его содержание."},
    {"key": "purpose", "title": "Цель и задачи", "question": "Какова цель документа и какие задачи он решает?"},
    {"key": "people", "title": "Участники и роли", "question": "Кто упоминается и какие роли выполняет: автор, проверяющий, исполнитель, ответственный?"},
    {"key": "resources", "title": "Технологии и ресурсы", "question": "Какие технологии, инструменты, материалы или ресурсы используются?"},
    {"key": "decisions", "title": "Требования и выводы", "question": "Какие важные требования, решения и выводы указаны?"},
    {"key": "timeline", "title": "Даты и сроки", "question": "Какие даты, сроки и этапы важны?"},
    {"key": "gaps", "title": "Что осталось неясным", "question": "Каких сведений не хватает или что сформулировано неясно?"},
]

CSV_QUESTIONS = [
    {"key": "overview", "title": "Что показывает таблица?", "question": "Что представляют строки и каково общее назначение таблицы?"},
    {"key": "columns", "title": "Столбцы и поля", "question": "Какие столбцы есть и что в них содержится?"},
    {"key": "values", "title": "Заметные значения", "question": "Какие заметные значения, категории или примеры встречаются? Не вычисляй суммы или средние."},
    {"key": "metrics", "title": "Числовые показатели", "question": "Локально вычисленные агрегаты по числовым столбцам."},
    {"key": "patterns", "title": "Наблюдаемые закономерности", "question": "Какие осторожные закономерности или различия видны в приведённых строках? Не делай выводов за пределами примеров."},
    {"key": "timeline", "title": "Даты и сроки", "question": "Какие даты или временные периоды есть в таблице?"},
    {"key": "gaps", "title": "Пропуски и неясности", "question": "Какие поля или данные выглядят неполными или требуют пояснения?"},
]

NO_EVIDENCE = "В документе не найдено достаточно подтверждений для уверенного ответа."

ANALYSIS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "answer": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "not_found": {"type": "boolean"},
                },
                "required": ["key", "answer", "citations", "not_found"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["insights"],
    "additionalProperties": False,
}


def questions_for(file_type: str) -> list[dict[str, str]]:
    return CSV_QUESTIONS if file_type == "csv" else GENERAL_QUESTIONS


def _format_number(raw: str) -> str:
    try:
        value = Decimal(raw)
    except InvalidOperation:
        return raw
    text = format(value, "f")
    whole, dot, fraction = text.partition(".")
    sign = ""
    if whole.startswith("-"):
        sign, whole = "−", whole[1:]
    grouped = f"{int(whole):,}".replace(",", " ") if whole else "0"
    return sign + grouped + ("," + fraction if dot and fraction else "")


def _csv_metrics(metadata: dict[str, Any], derived: dict[str, uuid.UUID]) -> tuple[str, list[str]]:
    row_count = int(metadata.get("row_count", 0))
    column_count = int(metadata.get("column_count", 0))
    numeric = metadata.get("numeric_columns", [])
    answer = f"В таблице {row_count} строк данных и {column_count} столбцов."
    citations: list[str] = []
    if not numeric:
        answer += " Числовых столбцов, пригодных для точного расчёта, не найдено."
    else:
        answer += "\n" + "\n".join(
            f"• {item['name']}: {item['count']} числовых значений; сумма {_format_number(item['sum'])}; "
            f"среднее {_format_number(item['average'])}; минимум {_format_number(item['minimum'])}; "
            f"максимум {_format_number(item['maximum'])}."
            for item in numeric[:12]
        )
        if len(numeric) > 12:
            answer += f"\nПоказаны первые 12 из {len(numeric)} числовых столбцов."
    for item in numeric[:12]:
        chunk_id = derived.get(item["name"])
        if chunk_id:
            citations.append(str(chunk_id))
    if not citations and "__table__" in derived:
        citations.append(str(derived["__table__"]))
    return answer, citations


async def analyze_document(document_id: uuid.UUID, codex: CodexService) -> None:
    async with SessionLocal() as session:
        document = await session.get(Document, document_id)
        if document is None:
            return
        document.status = "analyzing"
        document.error_message = None
        await session.commit()
        file_type = document.file_type
        metadata = dict(document.metadata_json or {})

    questions = questions_for(file_type)
    search_questions = [question for question in questions if question["key"] != "metrics"]
    sources_by_question: dict[str, list[Chunk]] = {}
    unique_chunks: dict[uuid.UUID, Chunk] = {}
    for question in search_questions:
        chunks = await search_chunks(document_id, question["question"], limit=4)
        sources_by_question[question["key"]] = chunks
        for chunk in chunks:
            unique_chunks.setdefault(chunk.id, chunk)

    if file_type == "csv":
        async with SessionLocal() as session:
            derived_rows = (await session.execute(
                select(Chunk).where(Chunk.document_id == document_id, Chunk.is_derived.is_(True))
            )).scalars().all()
        derived = {str(chunk.locator.get("column")): chunk.id for chunk in derived_rows if chunk.locator.get("column")}
        table_source = next((chunk.id for chunk in derived_rows if chunk.locator.get("column") is None), None)
        if table_source:
            derived["__table__"] = table_source
        metric_answer, metric_citations = _csv_metrics(metadata, derived)
    else:
        metric_answer, metric_citations = "", []

    source_labels: dict[str, uuid.UUID] = {}
    label_by_chunk: dict[uuid.UUID, str] = {}
    sources: list[dict[str, str]] = []
    used_chars = 0
    context_budget = settings.model_context_chars
    for chunk in unique_chunks.values():
        if used_chars >= context_budget:
            break
        excerpt = chunk.text[: min(1_200, context_budget - used_chars)]
        if not excerpt:
            continue
        label = f"S{len(sources) + 1:02d}"
        source_labels[label] = chunk.id
        label_by_chunk[chunk.id] = label
        sources.append({"label": label, "location": str(chunk.locator.get("label", "Фрагмент документа")), "excerpt": excerpt})
        used_chars += len(excerpt) + 80

    question_payload = []
    allowed_by_question: dict[str, set[str]] = {}
    for question in questions:
        related = sources_by_question.get(question["key"], [])
        labels = [label_by_chunk[chunk.id] for chunk in related if chunk.id in label_by_chunk]
        allowed_by_question[question["key"]] = set(labels)
        question_payload.append({
            "key": question["key"],
            "question": question["question"],
            "available_sources": labels,
        })

    model_metadata = {
        key: value for key, value in metadata.items()
        if key in {"page_count", "line_count", "row_count", "column_count", "columns", "root", "element_count", "paragraph_count", "table_count"}
    }
    payload = {
        "purpose": "Ответь отдельно на каждый вопрос. Утверждения подтверждай только метками источников из available_sources данного вопроса.",
        "format": file_type,
        "document_structure": model_metadata,
        "questions": question_payload,
        "sources": sources,
    }

    model_rows: dict[str, dict[str, Any]] = {}
    if search_questions and sources:
        raw = await codex.complete(payload, ANALYSIS_SCHEMA)
        try:
            parsed = json.loads(raw)
            model_rows = {item["key"]: item for item in parsed.get("insights", []) if isinstance(item, dict) and isinstance(item.get("key"), str)}
        except (json.JSONDecodeError, TypeError) as exc:
            logger.warning("Codex returned invalid insight JSON for document %s", document_id)
            raise RuntimeError("Модель вернула некорректный формат анализа. Повторите обработку.") from exc

    insights: list[Insight] = []
    for question in questions:
        key = question["key"]
        if key == "metrics" and file_type == "csv":
            answer, citations = metric_answer, metric_citations
        else:
            generated = model_rows.get(key, {})
            valid_labels = list(dict.fromkeys(
                value for value in generated.get("citations", [])
                if isinstance(value, str) and value in allowed_by_question.get(key, set())
            ))
            citation_ids = list(dict.fromkeys(str(source_labels[value]) for value in valid_labels))
            answer = str(generated.get("answer", "")).strip()[:4_000]
            if generated.get("not_found") is True or not answer or not citation_ids:
                answer = NO_EVIDENCE
                citation_ids = []
            else:
                answer = format_source_markers(answer, valid_labels)
            citations = citation_ids
        insights.append(Insight(
            document_id=document_id,
            key=key,
            question=question["title"],
            answer=answer,
            citations=citations,
        ))

    async with SessionLocal() as session:
        await session.execute(delete(Insight).where(Insight.document_id == document_id))
        session.add_all(insights)
        document = await session.get(Document, document_id)
        if document:
            document.status = "ready"
            document.error_message = None
        await session.commit()
