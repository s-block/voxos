from datetime import UTC, date, datetime

import pytest

from voxos.db.session import AsyncSessionFactory
from voxos.product_docs.models import ProductDocChunk, ProductDocSource
from voxos.product_docs.repository import ProductDocRepository


@pytest.mark.asyncio
async def test_product_doc_repository_upserts_searches_and_prunes(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    repository = ProductDocRepository(sqlite_session_factory)
    source = product_doc_source(path="Home.md", content="Voice and MCP setup.")
    voice_chunk = chunk(
        chunk_id="voice",
        source_path=source.path,
        chunk_index=0,
        content="Voice setup uses LiveKit and provider keys.",
        embedding=(1.0, 0.0, 0.0),
    )
    mcp_chunk = chunk(
        chunk_id="mcp",
        source_path=source.path,
        chunk_index=1,
        content="MCP setup uses remote HTTP servers and allowed tools.",
        embedding=(0.0, 1.0, 0.0),
    )

    first_stats = await repository.upsert_index(
        sources=(source,),
        chunks=(voice_chunk, mcp_chunk),
        prune=True,
    )
    matches = await repository.search_chunks(
        query_text="LiveKit voice",
        query_embedding=(1.0, 0.0, 0.0),
        embedding_model="test-model",
        embedding_dimension=3,
        limit=2,
    )
    page = await repository.get_page("Home.md")
    pruned_stats = await repository.upsert_index(
        sources=(source,),
        chunks=(voice_chunk,),
        prune=True,
    )
    after_prune = await repository.search_chunks(
        query_text="remote MCP",
        query_embedding=(0.0, 1.0, 0.0),
        embedding_model="test-model",
        embedding_dimension=3,
        limit=2,
    )

    assert first_stats.sources_upserted == 1
    assert first_stats.chunks_upserted == 2
    assert matches[0].chunk_id == "voice"
    assert page is not None
    assert page.title == "Home"
    assert pruned_stats.chunks_pruned == 1
    assert tuple(match.chunk_id for match in after_prune) == ("voice",)


@pytest.mark.asyncio
async def test_product_doc_repository_refreshes_changed_logical_chunk(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    repository = ProductDocRepository(sqlite_session_factory)
    source = product_doc_source(path="Home.md", content="Original docs.")
    legacy_chunk = chunk(
        chunk_id="legacy",
        source_path=source.path,
        chunk_index=0,
        content="Original docs mention MCP.",
        embedding=(1.0, 0.0, 0.0),
    )
    refreshed_chunk = chunk(
        chunk_id="stable",
        source_path=source.path,
        chunk_index=0,
        content="Updated docs mention LiveKit.",
        embedding=(0.0, 1.0, 0.0),
    )

    await repository.upsert_index(
        sources=(source,),
        chunks=(legacy_chunk,),
        prune=True,
    )
    stats = await repository.upsert_index(
        sources=(product_doc_source(path="Home.md", content="Updated docs."),),
        chunks=(refreshed_chunk,),
        prune=True,
    )
    stale_chunk = await repository.get_chunk("legacy")
    updated_chunk = await repository.get_chunk("stable")

    assert stats.chunks_upserted == 1
    assert stale_chunk is None
    assert updated_chunk is not None
    assert updated_chunk.content == "Updated docs mention LiveKit."


@pytest.mark.asyncio
async def test_product_doc_repository_prunes_removed_sources(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    repository = ProductDocRepository(sqlite_session_factory)
    home_source = product_doc_source(path="Home.md", content="Home docs.")
    removed_source = product_doc_source(path="Removed.md", content="Removed docs.")

    await repository.upsert_index(
        sources=(home_source, removed_source),
        chunks=(
            chunk(
                chunk_id="home",
                source_path="Home.md",
                chunk_index=0,
                content="Home docs mention LiveKit.",
                embedding=(1.0, 0.0, 0.0),
            ),
            chunk(
                chunk_id="removed",
                source_path="Removed.md",
                chunk_index=0,
                content="Removed docs mention MCP.",
                embedding=(0.0, 1.0, 0.0),
            ),
        ),
        prune=True,
    )
    stats = await repository.upsert_index(
        sources=(home_source,),
        chunks=(
            chunk(
                chunk_id="home",
                source_path="Home.md",
                chunk_index=0,
                content="Home docs mention LiveKit.",
                embedding=(1.0, 0.0, 0.0),
            ),
        ),
        prune=True,
    )
    removed_page = await repository.get_page("Removed.md")
    removed_matches = await repository.search_chunks(
        query_text="MCP",
        query_embedding=(0.0, 1.0, 0.0),
        embedding_model="test-model",
        embedding_dimension=3,
        limit=5,
        source_path="Removed.md",
    )

    assert stats.sources_pruned == 1
    assert stats.chunks_pruned == 1
    assert removed_page is None
    assert removed_matches == ()


def product_doc_source(*, path: str, content: str) -> ProductDocSource:
    return ProductDocSource(
        path=path,
        title=path.removesuffix(".md"),
        content=f"# {path.removesuffix('.md')}\n\nLast updated: 2026-07-05\n\n{content}",
        source_hash="c" * 64,
        last_updated=date(2026, 7, 5),
        indexed_at=datetime(2026, 7, 5, tzinfo=UTC),
    )


def chunk(
    *,
    chunk_id: str,
    source_path: str,
    chunk_index: int,
    content: str,
    embedding: tuple[float, ...],
) -> ProductDocChunk:
    return ProductDocChunk(
        id=chunk_id,
        source_path=source_path,
        chunk_index=chunk_index,
        title="Home",
        heading_path=("Home",),
        heading_slug="home",
        content=content,
        content_hash=(chunk_id[:1] or "d") * 64,
        token_count=8,
        char_count=len(content),
        embedding_model="test-model",
        embedding_dimension=3,
        embedding=embedding,
        updated_at=datetime(2026, 7, 5, tzinfo=UTC),
    )
