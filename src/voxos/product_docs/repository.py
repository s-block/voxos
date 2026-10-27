import math
import re
from dataclasses import dataclass
from typing import cast

from sqlalchemy import delete, text
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from voxos.db.models import utc_now
from voxos.db.session import AsyncSessionFactory
from voxos.product_docs.models import (
    ProductDocChunk,
    ProductDocPage,
    ProductDocSearchMatch,
    ProductDocsIndexStats,
    ProductDocSource,
)
from voxos.product_docs.records import (
    ProductDocChunkRecord,
    ProductDocSourceRecord,
    vector_to_pg_text,
)

QUERY_TERM_PATTERN = re.compile(r"[a-z0-9_./:-]+")


@dataclass(frozen=True, slots=True)
class ProductDocRepository:
    session_factory: AsyncSessionFactory

    async def upsert_index(
        self,
        *,
        sources: tuple[ProductDocSource, ...],
        chunks: tuple[ProductDocChunk, ...],
        prune: bool,
    ) -> ProductDocsIndexStats:
        source_paths = tuple(source.path for source in sources)
        chunk_ids = tuple(chunk.id for chunk in chunks)
        async with self.session_factory() as session:
            for source in sources:
                await upsert_source(session=session, source=source)
            for chunk in chunks:
                await upsert_chunk(session=session, chunk=chunk)
            chunks_pruned = 0
            sources_pruned = 0
            if prune:
                chunks_pruned = await prune_stale_chunks(
                    session=session,
                    source_paths=source_paths,
                    chunk_ids=chunk_ids,
                )
                sources_pruned = await prune_stale_sources(
                    session=session,
                    source_paths=source_paths,
                )
            await session.commit()
        return ProductDocsIndexStats(
            sources_seen=len(sources),
            sources_upserted=len(sources),
            sources_pruned=sources_pruned,
            chunks_seen=len(chunks),
            chunks_upserted=len(chunks),
            chunks_pruned=chunks_pruned,
        )

    async def get_page(self, source_path: str) -> ProductDocPage | None:
        async with self.session_factory() as session:
            record = await session.get(ProductDocSourceRecord, source_path)
            if record is None:
                return None
            return page_from_record(record)

    async def get_chunk(self, chunk_id: str) -> ProductDocChunk | None:
        async with self.session_factory() as session:
            record = await session.get(ProductDocChunkRecord, chunk_id)
            if record is None:
                return None
            return chunk_from_record(record)

    async def search_chunks(
        self,
        *,
        query_text: str,
        query_embedding: tuple[float, ...],
        embedding_model: str,
        embedding_dimension: int,
        limit: int,
        source_path: str | None = None,
    ) -> tuple[ProductDocSearchMatch, ...]:
        async with self.session_factory() as session:
            bind = session.get_bind()
            if bind.dialect.name == "postgresql":
                return await postgres_search_chunks(
                    session=session,
                    query_text=query_text,
                    query_embedding=query_embedding,
                    embedding_model=embedding_model,
                    embedding_dimension=embedding_dimension,
                    limit=limit,
                    source_path=source_path,
                )
            return await python_search_chunks(
                session=session,
                query_text=query_text,
                query_embedding=query_embedding,
                embedding_model=embedding_model,
                embedding_dimension=embedding_dimension,
                limit=limit,
                source_path=source_path,
            )


async def upsert_source(
    *,
    session: AsyncSession,
    source: ProductDocSource,
) -> None:
    now = utc_now()
    record = await session.get(ProductDocSourceRecord, source.path)
    if record is None:
        record = ProductDocSourceRecord(
            path=source.path,
            title=source.title,
            content=source.content,
            source_hash=source.source_hash,
            last_updated=source.last_updated,
            indexed_at=source.indexed_at,
            created_at=now,
            updated_at=now,
        )
    else:
        record.title = source.title
        record.content = source.content
        record.source_hash = source.source_hash
        record.last_updated = source.last_updated
        record.indexed_at = source.indexed_at
        record.updated_at = now
    session.add(record)


async def upsert_chunk(
    *,
    session: AsyncSession,
    chunk: ProductDocChunk,
) -> None:
    now = utc_now()
    record = await find_chunk_record(session=session, chunk=chunk)
    if record is None:
        record = ProductDocChunkRecord(
            id=chunk.id,
            source_path=chunk.source_path,
            chunk_index=chunk.chunk_index,
            title=chunk.title,
            heading_path=list(chunk.heading_path),
            heading_slug=chunk.heading_slug,
            content=chunk.content,
            content_hash=chunk.content_hash,
            token_count=chunk.token_count,
            char_count=chunk.char_count,
            embedding_model=chunk.embedding_model,
            embedding_dimension=chunk.embedding_dimension,
            embedding=list(chunk.embedding),
            created_at=now,
            updated_at=now,
        )
    else:
        record.id = chunk.id
        record.source_path = chunk.source_path
        record.chunk_index = chunk.chunk_index
        record.title = chunk.title
        record.heading_path = list(chunk.heading_path)
        record.heading_slug = chunk.heading_slug
        record.content = chunk.content
        record.content_hash = chunk.content_hash
        record.token_count = chunk.token_count
        record.char_count = chunk.char_count
        record.embedding_model = chunk.embedding_model
        record.embedding_dimension = chunk.embedding_dimension
        record.embedding = list(chunk.embedding)
        record.updated_at = now
    session.add(record)


async def find_chunk_record(
    *,
    session: AsyncSession,
    chunk: ProductDocChunk,
) -> ProductDocChunkRecord | None:
    record = await session.get(ProductDocChunkRecord, chunk.id)
    if record is not None:
        return record
    statement = select(ProductDocChunkRecord).where(
        col(ProductDocChunkRecord.source_path) == chunk.source_path,
        col(ProductDocChunkRecord.chunk_index) == chunk.chunk_index,
    )
    result = await session.exec(statement)
    return result.first()


async def prune_stale_chunks(
    *,
    session: AsyncSession,
    source_paths: tuple[str, ...],
    chunk_ids: tuple[str, ...],
) -> int:
    if not source_paths:
        result = await session.exec(delete(ProductDocChunkRecord))
        return int(result.rowcount or 0)
    removed_source_statement = delete(ProductDocChunkRecord).where(
        col(ProductDocChunkRecord.source_path).not_in(source_paths),
    )
    removed_source_result = await session.exec(removed_source_statement)
    stale_current_statement = delete(ProductDocChunkRecord).where(
        col(ProductDocChunkRecord.source_path).in_(source_paths),
    )
    if chunk_ids:
        stale_current_statement = stale_current_statement.where(
            col(ProductDocChunkRecord.id).not_in(chunk_ids),
        )
    stale_current_result = await session.exec(stale_current_statement)
    return int(removed_source_result.rowcount or 0) + int(stale_current_result.rowcount or 0)


async def prune_stale_sources(
    *,
    session: AsyncSession,
    source_paths: tuple[str, ...],
) -> int:
    if not source_paths:
        result = await session.exec(delete(ProductDocSourceRecord))
        return int(result.rowcount or 0)
    statement = delete(ProductDocSourceRecord).where(
        col(ProductDocSourceRecord.path).not_in(source_paths),
    )
    result = await session.exec(statement)
    return int(result.rowcount or 0)


async def postgres_search_chunks(
    *,
    session: AsyncSession,
    query_text: str,
    query_embedding: tuple[float, ...],
    embedding_model: str,
    embedding_dimension: int,
    limit: int,
    source_path: str | None,
) -> tuple[ProductDocSearchMatch, ...]:
    source_filter = "AND source_path = :source_path" if source_path is not None else ""
    statement = text(
        f"""
        SELECT
            id,
            source_path,
            title,
            heading_path,
            heading_slug,
            content,
            ((1 - (embedding <=> CAST(:query_embedding AS vector))) + 1) / 2 AS vector_score,
            ts_rank_cd(
                to_tsvector('english', title || ' ' || heading_path::text || ' ' || content),
                plainto_tsquery('english', :query_text)
            ) AS text_score
        FROM product_doc_chunks
        WHERE embedding_model = :embedding_model
          AND embedding_dimension = :embedding_dimension
          {source_filter}
        ORDER BY (embedding <=> CAST(:query_embedding AS vector)) ASC
        LIMIT :limit
        """,
    )
    params: dict[str, object] = {
        "query_embedding": vector_to_pg_text(query_embedding),
        "query_text": query_text,
        "embedding_model": embedding_model,
        "embedding_dimension": embedding_dimension,
        "limit": max(limit * 4, limit),
    }
    if source_path is not None:
        params["source_path"] = source_path
    result = await session.execute(statement, params)
    matches: list[ProductDocSearchMatch] = []
    for row in result.mappings():
        vector_score = clamp_score(float(row["vector_score"] or 0.0))
        text_score = clamp_score(float(row["text_score"] or 0.0))
        matches.append(
            ProductDocSearchMatch(
                chunk_id=str(row["id"]),
                title=str(row["title"]),
                source_path=str(row["source_path"]),
                heading_path=tuple(cast(list[str], row["heading_path"])),
                heading_slug=str(row["heading_slug"]),
                excerpt=excerpt_for_text(str(row["content"]), query_text),
                score=combined_score(vector_score=vector_score, text_score=text_score),
                vector_score=vector_score,
                text_score=text_score,
            ),
        )
    return tuple(sorted(matches, key=lambda match: match.score, reverse=True)[:limit])


async def python_search_chunks(
    *,
    session: AsyncSession,
    query_text: str,
    query_embedding: tuple[float, ...],
    embedding_model: str,
    embedding_dimension: int,
    limit: int,
    source_path: str | None,
) -> tuple[ProductDocSearchMatch, ...]:
    statement = select(ProductDocChunkRecord).where(
        col(ProductDocChunkRecord.embedding_model) == embedding_model,
        col(ProductDocChunkRecord.embedding_dimension) == embedding_dimension,
    )
    if source_path is not None:
        statement = statement.where(col(ProductDocChunkRecord.source_path) == source_path)
    result = await session.exec(statement)
    matches = tuple(
        match_from_record(
            record=record,
            query_text=query_text,
            query_embedding=query_embedding,
        )
        for record in result.all()
    )
    return tuple(sorted(matches, key=lambda match: match.score, reverse=True)[:limit])


def match_from_record(
    *,
    record: ProductDocChunkRecord,
    query_text: str,
    query_embedding: tuple[float, ...],
) -> ProductDocSearchMatch:
    vector_score = cosine_similarity(query_embedding, tuple(record.embedding))
    text_score = lexical_score(
        query_text=query_text,
        text=" ".join((record.title, " ".join(record.heading_path), record.content)),
    )
    return ProductDocSearchMatch(
        chunk_id=record.id,
        title=record.title,
        source_path=record.source_path,
        heading_path=tuple(record.heading_path),
        heading_slug=record.heading_slug,
        excerpt=excerpt_for_text(record.content, query_text),
        score=combined_score(vector_score=vector_score, text_score=text_score),
        vector_score=vector_score,
        text_score=text_score,
    )


def combined_score(*, vector_score: float, text_score: float) -> float:
    return clamp_score((0.75 * vector_score) + (0.25 * text_score))


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    dot_product = sum(left_value * right_value for left_value, right_value in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return clamp_score((dot_product / (left_norm * right_norm) + 1.0) / 2.0)


def lexical_score(*, query_text: str, text: str) -> float:
    query_terms = tuple(QUERY_TERM_PATTERN.findall(query_text.lower()))
    if not query_terms:
        return 0.0
    normalized_text = text.lower()
    matches = sum(1 for term in query_terms if term in normalized_text)
    phrase_bonus = 1 if query_text.lower().strip() in normalized_text else 0
    return clamp_score((matches + phrase_bonus) / (len(query_terms) + 1))


def excerpt_for_text(text_value: str, query_text: str, max_length: int = 360) -> str:
    normalized_text = " ".join(text_value.split())
    query_terms = QUERY_TERM_PATTERN.findall(query_text.lower())
    start = 0
    lower_text = normalized_text.lower()
    for term in query_terms:
        term_index = lower_text.find(term)
        if term_index >= 0:
            start = max(0, term_index - 80)
            break
    excerpt = normalized_text[start : start + max_length].strip()
    if start > 0:
        excerpt = f"...{excerpt}"
    if start + max_length < len(normalized_text):
        excerpt = f"{excerpt}..."
    return excerpt or normalized_text[:max_length]


def clamp_score(value: float) -> float:
    if value < 0:
        return 0.0
    if value > 1:
        return 1.0
    return value


def page_from_record(record: ProductDocSourceRecord) -> ProductDocPage:
    return ProductDocPage(
        title=record.title,
        source_path=record.path,
        content=record.content,
        source_hash=record.source_hash,
        last_updated=record.last_updated,
    )


def chunk_from_record(record: ProductDocChunkRecord) -> ProductDocChunk:
    return ProductDocChunk(
        id=record.id,
        source_path=record.source_path,
        chunk_index=record.chunk_index,
        title=record.title,
        heading_path=tuple(record.heading_path),
        heading_slug=record.heading_slug,
        content=record.content,
        content_hash=record.content_hash,
        token_count=record.token_count,
        char_count=record.char_count,
        embedding_model=record.embedding_model,
        embedding_dimension=record.embedding_dimension,
        embedding=tuple(record.embedding),
        updated_at=record.updated_at,
    )
