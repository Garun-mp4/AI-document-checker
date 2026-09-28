from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import func, select

from app.config import settings
from app.database import SessionLocal
from app.models import Chunk
from app.services.embeddings import embed_query


async def search_chunks(document_id: uuid.UUID, query: str, limit: int = 5) -> list[Chunk]:
    vector = await asyncio.to_thread(embed_query, query, settings.embedding_cache_dir)
    async with SessionLocal() as session:
        semantic = await session.execute(
            select(Chunk, Chunk.embedding.cosine_distance(vector).label("distance"))
            .where(Chunk.document_id == document_id, Chunk.embedding.is_not(None), Chunk.is_derived.is_(False))
            .order_by(Chunk.embedding.cosine_distance(vector))
            .limit(limit * 3)
        )
        full_text_vector = func.to_tsvector("russian", Chunk.text)
        full_text_query = func.plainto_tsquery("russian", query)
        lexical = await session.execute(
            select(Chunk, func.ts_rank(full_text_vector, full_text_query).label("rank"))
            .where(
                Chunk.document_id == document_id,
                Chunk.is_derived.is_(False),
                full_text_vector.op("@@")(full_text_query),
            )
            .order_by(func.ts_rank(full_text_vector, full_text_query).desc())
            .limit(limit * 3)
        )

    scores: dict[uuid.UUID, float] = {}
    chunks: dict[uuid.UUID, Chunk] = {}
    for rank, (chunk, _) in enumerate(semantic.all(), start=1):
        chunks[chunk.id] = chunk
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (60 + rank)
    for rank, (chunk, _) in enumerate(lexical.all(), start=1):
        chunks[chunk.id] = chunk
        scores[chunk.id] = scores.get(chunk.id, 0.0) + 1 / (60 + rank)
    ordered = sorted(scores, key=scores.__getitem__, reverse=True)
    return [chunks[chunk_id] for chunk_id in ordered[:limit]]
