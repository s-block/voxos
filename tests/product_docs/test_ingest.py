from pathlib import Path

import pytest

from voxos.db.session import AsyncSessionFactory
from voxos.product_docs.embeddings import ProductDocEmbeddingClient
from voxos.product_docs.ingest import ProductDocsIndexer
from voxos.product_docs.repository import ProductDocRepository
from voxos.product_docs.validation import write_manifest


class StaticEmbeddingClient(ProductDocEmbeddingClient):
    model = "static-test"
    dimensions = 3

    async def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(index + 1), 0.0, 0.0) for index, _ in enumerate(texts))


@pytest.mark.asyncio
async def test_indexer_dry_run_and_database_write(
    tmp_path: Path,
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    docs_dir = tmp_path / "wiki"
    docs_dir.mkdir()
    (docs_dir / "Home.md").write_text(
        "# Home\n\nLast updated: 2026-07-05\n\n## Voice\n\nUse LiveKit.",
        encoding="utf-8",
    )
    (docs_dir / "_Sidebar.md").write_text(
        "# Sidebar\n\nLast updated: 2026-07-05\n\n- [Home](Home.md)\n",
        encoding="utf-8",
    )
    write_manifest(docs_dir)
    indexer = ProductDocsIndexer(
        repository=ProductDocRepository(sqlite_session_factory),
        embedding_client=StaticEmbeddingClient(),
        batch_size=2,
    )

    dry_run_stats = await indexer.index_docs(
        docs_dir=docs_dir,
        prune=True,
        dry_run=True,
    )
    write_stats = await indexer.index_docs(
        docs_dir=docs_dir,
        prune=True,
        dry_run=False,
    )
    matches = await ProductDocRepository(sqlite_session_factory).search_chunks(
        query_text="LiveKit",
        query_embedding=(1.0, 0.0, 0.0),
        embedding_model="static-test",
        embedding_dimension=3,
        limit=1,
    )

    assert dry_run_stats.dry_run is True
    assert dry_run_stats.sources_seen == 1
    assert write_stats.sources_upserted == 1
    assert write_stats.chunks_upserted >= 1
    assert matches[0].source_path == "Home.md"
