from dataclasses import dataclass
from typing import Protocol

import httpx2 as httpx

from voxos.db.session import AsyncSessionFactory
from voxos.product_docs.embeddings import (
    OpenAIProductDocEmbeddingClient,
    ProductDocEmbeddingClient,
    ProductDocEmbeddingConfigurationError,
    ProductDocsEmbeddingSettings,
)
from voxos.product_docs.models import (
    ProductDocPageResult,
    ProductDocSearchRequest,
    ProductDocSearchResult,
)
from voxos.product_docs.repository import ProductDocRepository


class ProductDocSearchService(Protocol):
    async def search_docs(self, request: ProductDocSearchRequest) -> ProductDocSearchResult:
        """Search indexed Voxos product docs."""

    async def read_page(self, source_path: str) -> ProductDocPageResult:
        """Read an indexed Voxos product doc page by path."""


@dataclass(frozen=True, slots=True)
class ProductDocRetrievalService:
    repository: ProductDocRepository
    embedding_client: ProductDocEmbeddingClient

    async def search_docs(self, request: ProductDocSearchRequest) -> ProductDocSearchResult:
        embedding = (await self.embedding_client.embed_texts((request.query,)))[0]
        matches = await self.repository.search_chunks(
            query_text=request.query,
            query_embedding=embedding,
            embedding_model=self.embedding_client.model,
            embedding_dimension=self.embedding_client.dimensions,
            limit=request.limit,
            source_path=request.source_path,
        )
        message = None if matches else "No matching Voxos docs were found."
        return ProductDocSearchResult(matches=matches, message=message)

    async def read_page(self, source_path: str) -> ProductDocPageResult:
        page = await self.repository.get_page(source_path)
        if page is None:
            return ProductDocPageResult(page=None, message="Voxos docs page was not found.")
        return ProductDocPageResult(page=page)


@dataclass(slots=True)
class EnvironmentProductDocRetrievalService:
    session_factory: AsyncSessionFactory
    settings: ProductDocsEmbeddingSettings | None = None
    embedding_client: ProductDocEmbeddingClient | None = None
    http_client: httpx.AsyncClient | None = None

    async def search_docs(self, request: ProductDocSearchRequest) -> ProductDocSearchResult:
        try:
            embedding_client = self._embedding_client()
        except ProductDocEmbeddingConfigurationError as exc:
            return ProductDocSearchResult(matches=(), message=str(exc))
        return await ProductDocRetrievalService(
            repository=ProductDocRepository(self.session_factory),
            embedding_client=embedding_client,
        ).search_docs(request)

    async def read_page(self, source_path: str) -> ProductDocPageResult:
        return await ProductDocRetrievalService(
            repository=ProductDocRepository(self.session_factory),
            embedding_client=UnavailableEmbeddingClient(),
        ).read_page(source_path)

    def _embedding_client(self) -> ProductDocEmbeddingClient:
        if self.embedding_client is None:
            self.embedding_client = OpenAIProductDocEmbeddingClient.from_settings(
                self.settings,
                http_client=self.http_client,
            )
        return self.embedding_client


@dataclass(frozen=True, slots=True)
class UnavailableProductDocRetrievalService:
    async def search_docs(self, request: ProductDocSearchRequest) -> ProductDocSearchResult:
        _ = request
        return ProductDocSearchResult(
            matches=(),
            message=("Product docs retrieval is not configured for this tool provider."),
        )

    async def read_page(self, source_path: str) -> ProductDocPageResult:
        _ = source_path
        return ProductDocPageResult(
            page=None,
            message="Product docs retrieval is not configured for this tool provider.",
        )


@dataclass(frozen=True, slots=True)
class UnavailableEmbeddingClient:
    model: str = "unavailable"
    dimensions: int = 1

    async def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        message = "Product docs embeddings are not configured."
        raise ProductDocEmbeddingConfigurationError(message)
