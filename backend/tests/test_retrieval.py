from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import uuid4

from app.services import retrieval


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Session:
    def __init__(self, semantic, lexical):
        self._results = [_Result(semantic), _Result(lexical)]

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def execute(self, _statement):
        return self._results.pop(0)


def test_search_chunks_merges_semantic_and_lexical_rankings(monkeypatch) -> None:
    document_id = uuid4()
    first = SimpleNamespace(id=uuid4(), text="semantic first")
    second = SimpleNamespace(id=uuid4(), text="both semantic and lexical")
    third = SimpleNamespace(id=uuid4(), text="lexical only")
    session = _Session(
        semantic=[(first, 0.1), (second, 0.2)],
        lexical=[(second, 0.9), (third, 0.8)],
    )

    monkeypatch.setattr(retrieval, "embed_query", lambda *_args: [0.1, 0.2])
    monkeypatch.setattr(retrieval, "SessionLocal", lambda: session)

    result = asyncio.run(retrieval.search_chunks(document_id, "важный вопрос", limit=3))

    assert [chunk.id for chunk in result] == [second.id, first.id, third.id]
