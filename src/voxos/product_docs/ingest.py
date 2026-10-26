import argparse
import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from sqlalchemy.ext.asyncio import AsyncEngine

from voxos.db.engine import create_database_engine, create_session_factory
from voxos.db.models import utc_now
from voxos.db.settings import DatabaseSettings
from voxos.product_docs.chunking import ChunkDraft, MarkdownChunker
from voxos.product_docs.embeddings import (
    OpenAIProductDocEmbeddingClient,
    ProductDocEmbeddingClient,
    ProductDocsEmbeddingSettings,
    embed_in_batches,
)
from voxos.product_docs.loader import MarkdownDocument, load_markdown_documents
from voxos.product_docs.models import ProductDocChunk, ProductDocsIndexStats
from voxos.product_docs.repository import ProductDocRepository
from voxos.product_docs.validation import validate_wiki_docs


@dataclass(frozen=True, slots=True)
class ProductDocsIndexer:
    repository: ProductDocRepository
    embedding_client: ProductDocEmbeddingClient
    batch_size: int
    chunker: MarkdownChunker = field(default_factory=MarkdownChunker)

    async def index_docs(
        self,
        *,
        docs_dir: Path,
        prune: bool,
        dry_run: bool = False,
    ) -> ProductDocsIndexStats:
        documents = load_markdown_documents(docs_dir)
        chunk_drafts = chunk_documents(chunker=self.chunker, documents=documents)
        if dry_run:
            return ProductDocsIndexStats(
                sources_seen=len(documents),
                sources_upserted=0,
                chunks_seen=len(chunk_drafts),
                chunks_upserted=0,
                chunks_pruned=0,
                dry_run=True,
            )
        embeddings = await embed_in_batches(
            client=self.embedding_client,
            texts=tuple(chunk.content for chunk in chunk_drafts),
            batch_size=self.batch_size,
        )
        chunks = tuple(
            product_doc_chunk_from_draft(
                draft=draft,
                embedding=embedding,
                embedding_model=self.embedding_client.model,
                embedding_dimension=self.embedding_client.dimensions,
            )
            for draft, embedding in zip(chunk_drafts, embeddings, strict=True)
        )
        return await self.repository.upsert_index(
            sources=tuple(document.source for document in documents),
            chunks=chunks,
            prune=prune,
        )


def chunk_documents(
    *,
    chunker: MarkdownChunker,
    documents: tuple[MarkdownDocument, ...],
) -> tuple[ChunkDraft, ...]:
    chunks: list[ChunkDraft] = []
    for document in documents:
        chunks.extend(chunker.chunk_document(document))
    return tuple(chunks)


def product_doc_chunk_from_draft(
    *,
    draft: ChunkDraft,
    embedding: tuple[float, ...],
    embedding_model: str,
    embedding_dimension: int,
) -> ProductDocChunk:
    return ProductDocChunk(
        id=draft.id,
        source_path=draft.source_path,
        chunk_index=draft.chunk_index,
        title=draft.title,
        heading_path=draft.heading_path,
        heading_slug=draft.heading_slug,
        content=draft.content,
        content_hash=draft.content_hash,
        token_count=draft.token_count,
        char_count=draft.char_count,
        embedding_model=embedding_model,
        embedding_dimension=embedding_dimension,
        embedding=embedding,
        updated_at=utc_now(),
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Index Voxos wiki docs.")
    parser.add_argument("--docs-dir", default="docs/wiki", help="Wiki docs directory.")
    parser.add_argument("--prune", action="store_true", help="Prune stale chunks.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate, load, and chunk docs without embedding or database writes.",
    )
    return parser


async def async_main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    docs_dir = Path(args.docs_dir)
    validation_errors = validate_wiki_docs(docs_dir, check_manifest=True)
    if validation_errors:
        for error in validation_errors:
            sys.stderr.write(f"{error}\n")
        return 1

    settings = ProductDocsEmbeddingSettings()
    engine: AsyncEngine | None = None
    try:
        if args.dry_run:
            documents = load_markdown_documents(docs_dir)
            chunks = chunk_documents(
                chunker=MarkdownChunker(),
                documents=documents,
            )
            stats = ProductDocsIndexStats(
                sources_seen=len(documents),
                sources_upserted=0,
                chunks_seen=len(chunks),
                chunks_upserted=0,
                chunks_pruned=0,
                dry_run=True,
            )
        else:
            engine = create_database_engine(DatabaseSettings())
            indexer = ProductDocsIndexer(
                repository=ProductDocRepository(create_session_factory(engine)),
                embedding_client=OpenAIProductDocEmbeddingClient.from_settings(settings),
                batch_size=settings.batch_size,
            )
            stats = await indexer.index_docs(
                docs_dir=docs_dir,
                prune=args.prune,
                dry_run=False,
            )
    finally:
        if engine is not None:
            await engine.dispose()

    sys.stdout.write(f"{stats.model_dump_json()}\n")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main())
