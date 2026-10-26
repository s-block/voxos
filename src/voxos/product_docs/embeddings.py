from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx
from openai import AsyncOpenAI, OpenAIError
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from voxos.product_docs.records import DEFAULT_EMBEDDING_DIMENSION

DEFAULT_EMBEDDING_MODEL = "text-embedding-3-small"


class ProductDocEmbeddingClient(Protocol):
    @property
    def model(self) -> str:
        """Embedding model name used for indexed chunks."""

    @property
    def dimensions(self) -> int:
        """Embedding vector dimension used for indexed chunks."""

    async def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        """Embed non-empty texts in input order."""


class ProductDocsEmbeddingSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        populate_by_name=True,
    )

    platform_api_key: SecretStr | None = Field(
        default=None,
        validation_alias="PLATFORM_OPENAI_API_KEY",
    )
    model: str = Field(
        default=DEFAULT_EMBEDDING_MODEL,
        validation_alias="PRODUCT_DOCS_EMBEDDING_MODEL",
    )
    dimensions: int = Field(
        default=DEFAULT_EMBEDDING_DIMENSION,
        validation_alias="PRODUCT_DOCS_EMBEDDING_DIMENSION",
        ge=1,
    )
    batch_size: int = Field(
        default=32,
        validation_alias="PRODUCT_DOCS_EMBEDDING_BATCH_SIZE",
        ge=1,
        le=128,
    )


@dataclass(frozen=True, slots=True)
class OpenAIProductDocEmbeddingClient:
    client: AsyncOpenAI
    model: str = DEFAULT_EMBEDDING_MODEL
    dimensions: int = DEFAULT_EMBEDDING_DIMENSION

    @classmethod
    def from_settings(
        cls,
        settings: ProductDocsEmbeddingSettings | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> "OpenAIProductDocEmbeddingClient":
        resolved_settings = settings or ProductDocsEmbeddingSettings()
        platform_api_key = resolved_settings.platform_api_key
        if platform_api_key is None:
            message = "Product docs embeddings require PLATFORM_OPENAI_API_KEY."
            raise ProductDocEmbeddingConfigurationError(message)
        if resolved_settings.dimensions != DEFAULT_EMBEDDING_DIMENSION:
            message = (
                "Product docs embeddings must use "
                f"{DEFAULT_EMBEDDING_DIMENSION} dimensions because "
                f"product_doc_chunks.embedding is vector({DEFAULT_EMBEDDING_DIMENSION})."
            )
            raise ProductDocEmbeddingConfigurationError(message)
        openai_client = (
            AsyncOpenAI(api_key=platform_api_key.get_secret_value())
            if http_client is None
            else AsyncOpenAI(
                api_key=platform_api_key.get_secret_value(),
                http_client=http_client,
            )
        )
        return cls(
            client=openai_client,
            model=resolved_settings.model,
            dimensions=resolved_settings.dimensions,
        )

    async def embed_texts(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        clean_texts = tuple(text.strip() for text in texts if text.strip())
        if len(clean_texts) != len(texts):
            message = "Product docs embedding input cannot contain empty strings."
            raise ValueError(message)
        if not clean_texts:
            return ()
        try:
            response = await self.client.embeddings.create(
                input=list(clean_texts),
                model=self.model,
                dimensions=self.dimensions,
            )
        except OpenAIError as exc:
            message = "Product docs embedding request failed."
            raise ProductDocEmbeddingError(message) from exc
        embeddings_by_index = sorted(response.data, key=lambda item: item.index)
        return tuple(tuple(float(value) for value in item.embedding) for item in embeddings_by_index)


class ProductDocEmbeddingConfigurationError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


class ProductDocEmbeddingError(RuntimeError):
    def __init__(self, message: str) -> None:
        super().__init__(message)


def batched_texts(
    texts: tuple[str, ...],
    *,
    batch_size: int,
) -> tuple[tuple[str, ...], ...]:
    batches: list[tuple[str, ...]] = []
    for index in range(0, len(texts), batch_size):
        batches.append(texts[index : index + batch_size])
    return tuple(batches)


async def embed_in_batches(
    *,
    client: ProductDocEmbeddingClient,
    texts: Sequence[str],
    batch_size: int,
) -> tuple[tuple[float, ...], ...]:
    embeddings: list[tuple[float, ...]] = []
    for batch in batched_texts(tuple(texts), batch_size=batch_size):
        embeddings.extend(await client.embed_texts(batch))
    return tuple(embeddings)
