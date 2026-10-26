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
