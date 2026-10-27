import httpx2
import orjson
import pytest
from pydantic import SecretStr
from pytest import MonkeyPatch

from voxos.product_docs.embeddings import (
    OpenAIProductDocEmbeddingClient,
    ProductDocEmbeddingConfigurationError,
    ProductDocsEmbeddingSettings,
)
from voxos.product_docs.records import DEFAULT_EMBEDDING_DIMENSION


def test_openai_embedding_client_rejects_non_schema_dimension() -> None:
    settings = ProductDocsEmbeddingSettings(
        platform_api_key=SecretStr("test-key"),
        dimensions=DEFAULT_EMBEDDING_DIMENSION - 1,
    )

    with pytest.raises(ProductDocEmbeddingConfigurationError, match="1536 dimensions"):
        OpenAIProductDocEmbeddingClient.from_settings(settings)


def test_embedding_settings_reads_platform_openai_key(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("PLATFORM_OPENAI_API_KEY", "platform-key")

    settings = ProductDocsEmbeddingSettings(_env_file=None)

    assert settings.platform_api_key is not None
    assert settings.platform_api_key.get_secret_value() == "platform-key"


def test_embedding_settings_does_not_fall_back_to_openai_api_key(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("PLATFORM_OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-key")

    settings = ProductDocsEmbeddingSettings(_env_file=None)

    assert settings.platform_api_key is None


@pytest.mark.asyncio
async def test_embedding_sdk_uses_injected_httpx2_transport_and_preserves_order() -> None:
    requests: list[httpx2.Request] = []

    def response(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        return httpx2.Response(
            200,
            json={
                "object": "list",
                "model": "text-embedding-3-small",
                "data": [
                    {"object": "embedding", "index": 1, "embedding": [2.0] * DEFAULT_EMBEDDING_DIMENSION},
                    {"object": "embedding", "index": 0, "embedding": [1.0] * DEFAULT_EMBEDDING_DIMENSION},
                ],
                "usage": {"prompt_tokens": 2, "total_tokens": 2},
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(response)) as http_client:
        client = OpenAIProductDocEmbeddingClient.from_settings(
            ProductDocsEmbeddingSettings(platform_api_key=SecretStr("embedding-test-key")),
            http_client=http_client,
        )
        vectors = await client.embed_texts(("first", "second"))

    assert vectors[0] == (1.0,) * DEFAULT_EMBEDDING_DIMENSION
    assert vectors[1] == (2.0,) * DEFAULT_EMBEDDING_DIMENSION
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer embedding-test-key"
    assert orjson.loads(requests[0].content) == {
        "input": ["first", "second"],
        "model": "text-embedding-3-small",
        "dimensions": DEFAULT_EMBEDDING_DIMENSION,
        "encoding_format": "base64",
    }
