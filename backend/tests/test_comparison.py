from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.schemas import ChatComparisonCreateIn, ChatComparisonUpdateIn, ChatDocumentOut
from app.services.chat_library import build_chat_summary
from app.services.comparison import (
    citation_snapshot,
    comparison_output_schema,
    interleave_document_results,
    render_comparison_answer,
    validate_comparison_response,
)


def _response(document_ids: list[str], first_source: str, second_source: str) -> dict:
    return {
        "documents": [
            {"document_id": document_ids[0], "status": "supported", "answer": "Первый факт", "citations": [first_source]},
            {"document_id": document_ids[1], "status": "supported", "answer": "Второй факт", "citations": [second_source]},
        ],
        "comparison": {"status": "supported", "answer": "Факты различаются.", "citations": [first_source, second_source]},
    }


def test_comparison_request_requires_two_to_five_unique_documents_and_revision() -> None:
    ids = [uuid4() for _ in range(5)]
    assert len(ChatComparisonCreateIn(document_ids=ids[:2]).document_ids) == 2
    assert ChatComparisonUpdateIn(expected_revision=2, document_ids=ids[:5]).expected_revision == 2
    for invalid in (ids[:1], ids + [uuid4()]):
        with pytest.raises(ValidationError):
            ChatComparisonCreateIn(document_ids=invalid)
    with pytest.raises(ValidationError, match="повторяться"):
        ChatComparisonCreateIn(document_ids=[ids[0], ids[0]])
    with pytest.raises(ValidationError):
        ChatComparisonUpdateIn(expected_revision=0, document_ids=ids[:2])


def test_comparison_contract_accepts_only_selected_document_citations() -> None:
    document_ids = [str(uuid4()), str(uuid4())]
    source_ids = [str(uuid4()), str(uuid4())]
    validated = validate_comparison_response(
        _response(document_ids, *source_ids),
        selected_document_ids=document_ids,
        available_sources={source_ids[0]: document_ids[0], source_ids[1]: document_ids[1]},
    )

    assert [item.document_id for item in validated.documents] == document_ids
    assert comparison_output_schema()["additionalProperties"] is False

    wrong_owner = _response(document_ids, *source_ids)
    wrong_owner["documents"][0]["citations"] = [source_ids[1]]
    with pytest.raises(ValueError, match="не принадлежит"):
        validate_comparison_response(
            wrong_owner,
            selected_document_ids=document_ids,
            available_sources={source_ids[0]: document_ids[0], source_ids[1]: document_ids[1]},
        )


def test_comparison_requires_exact_document_set_and_cross_document_evidence() -> None:
    document_ids = [str(uuid4()), str(uuid4())]
    source_ids = [str(uuid4()), str(uuid4())]
    available = {source_ids[0]: document_ids[0], source_ids[1]: document_ids[1]}
    extra_document = _response(document_ids, *source_ids)
    extra_document["documents"].append({
        "document_id": str(uuid4()), "status": "not_found", "answer": "", "citations": [],
    })
    with pytest.raises(ValueError, match="ровно выбранные"):
        validate_comparison_response(extra_document, selected_document_ids=document_ids, available_sources=available)

    one_document_only = _response(document_ids, *source_ids)
    one_document_only["comparison"]["citations"] = [source_ids[0]]
    with pytest.raises(ValueError, match="минимум из двух"):
        validate_comparison_response(one_document_only, selected_document_ids=document_ids, available_sources=available)


def test_comparison_render_uses_server_document_names_and_stable_citation_numbers() -> None:
    document_ids = [str(uuid4()), str(uuid4())]
    source_ids = [str(uuid4()), str(uuid4())]
    validated = validate_comparison_response(
        _response(document_ids, *source_ids), selected_document_ids=document_ids,
        available_sources={source_ids[0]: document_ids[0], source_ids[1]: document_ids[1]},
    )

    answer, citations = render_comparison_answer(
        validated,
        selected_documents=[(document_ids[0], "первый.txt"), (document_ids[1], "второй.pdf")],
    )
    assert "**первый.txt**" in answer and "**второй.pdf**" in answer
    assert answer.count("〔1〕") == 2
    assert answer.count("〔2〕") == 2
    assert citations == source_ids


def test_retrieval_interleaves_results_so_one_source_cannot_take_all_slots() -> None:
    result = interleave_document_results([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]])
    assert result == ["a1", "b1", "c1", "a2", "c2", "a3"]
    assert interleave_document_results([[1, 2], [3, 4]], limit=3) == [1, 3, 2]


def test_citation_snapshot_keeps_safe_locator_only_and_no_excerpt() -> None:
    snapshot = citation_snapshot(
        str(uuid4()), document_id=str(uuid4()), filename="sample.pdf", source_version=4,
        locator={"page": 3, "row": 12, "secret": "must not persist", "source_text": "private excerpt"},
        ordinal=7, is_derived=False,
    )
    assert snapshot["locator"] == {"page": 3, "row": 12}
    assert "source_text" not in snapshot
    assert "private excerpt" not in str(snapshot)


def test_comparison_chat_summary_exposes_selected_sources_and_recoverable_title() -> None:
    moment = datetime(2026, 10, 4, tzinfo=timezone.utc)
    chat = SimpleNamespace(id="comparison-1", scope="comparison", created_at=moment, title=None, pinned_at=None, revision=3)
    documents = [
        ChatDocumentOut(id="doc-1", filename="first.txt", file_type="txt", status="ready", source_version=2),
        ChatDocumentOut(id="doc-2", filename="second.pdf", file_type="pdf", status="ready", source_version=5),
    ]

    summary = build_chat_summary(chat, None, [], comparison_documents=documents)

    assert summary.scope == "comparison"
    assert summary.document_id is None
    assert summary.documents == documents
    assert summary.title == "Сравнение: first.txt + 1"
    assert summary.revision == 3
