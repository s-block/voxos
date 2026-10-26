from datetime import UTC, date, datetime

import pytest

from voxos.db.session import AsyncSessionFactory
from voxos.product_docs.models import (
    ProductDocChunk,
    ProductDocSearchRequest,
    ProductDocSource,
)
from voxos.product_docs.repository import ProductDocRepository
from voxos.product_docs.retrieval import EnvironmentProductDocRetrievalService


class CountingEmbeddingClient:
    model = "counting-test"
    dimensions = 3

    def __init__(self) -> None:
        self.requests: list[tuple[str, ...]] = []

    async def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        self.requests.append(texts)
        return tuple((1.0, 0.0, 0.0) for _ in texts)


@pytest.mark.asyncio
async def test_environment_retrieval_uses_injected_embedding_client(
    sqlite_session_factory: AsyncSessionFactory,
) -> None:
    repository = ProductDocRepository(sqlite_session_factory)
    await repository.upsert_index(
        sources=(source(),),
        chunks=(chunk(),),
        prune=True,
    )
    embedding_client = CountingEmbeddingClient()
    service = EnvironmentProductDocRetrievalService(
        session_factory=sqlite_session_factory,
        embedding_client=embedding_client,
    )

    first = await service.search_docs(ProductDocSearchRequest(query="LiveKit"))
    second = await service.search_docs(ProductDocSearchRequest(query="MCP"))

    assert first.matches[0].source_path == "Home.md"
    assert second.matches[0].source_path == "Home.md"
    assert embedding_client.requests == [("LiveKit",), ("MCP",)]


def source() -> ProductDocSource:
    return ProductDocSource(
        path="Home.md",
        title="Home",
        content="# Home\n\nLast updated: 2026-07-05\n\nLiveKit docs.",
        source_hash="a" * 64,
        last_updated=date(2026, 7, 5),
        indexed_at=datetime(2026, 7, 5, tzinfo=UTC),
    )


def chunk() -> ProductDocChunk:
    return ProductDocChunk(
        id="home",
        source_path="Home.md",
        chunk_index=0,
        title="Home",
        heading_path=("Home",),
        heading_slug="home",
        content="LiveKit docs.",
        content_hash="b" * 64,
        token_count=2,
        char_count=13,
        embedding_model="counting-test",
        embedding_dimension=3,
        embedding=(1.0, 0.0, 0.0),
        updated_at=datetime(2026, 7, 5, tzinfo=UTC),
    )
