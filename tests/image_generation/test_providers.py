import base64

import httpx
import orjson
import pytest
from pydantic import SecretStr

from voxos.image_generation.providers import (
    HttpImageGenerationProviderClient,
    ImageGenerationInput,
    ImageGenerationProviderError,
    ImageGenerationProviderRequest,
)
from voxos.text.models import ResolvedTextCredentials, TextProvider


@pytest.mark.asyncio
async def test_nvidia_generation_uses_hosted_flux_endpoint() -> None:
    image = b"generated-image"

    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == ("https://ai.api.nvidia.com/v1/genai/black-forest-labs/flux.2-klein-4b")
        assert request.headers["authorization"] == "Bearer provider-secret"
        payload = orjson.loads(request.content)
        assert payload == {
            "height": 1024,
            "prompt": "A moonlit glass forest",
            "seed": 0,
            "steps": 4,
            "width": 1024,
        }
        return httpx.Response(
            200,
            json={
                "artifacts": [
                    {
                        "base64": base64.b64encode(image).decode("ascii"),
                        "finishReason": "SUCCESS",
                        "seed": 123,
                    },
                ],
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpImageGenerationProviderClient(http_client)
        result = await client.generate(
            ImageGenerationProviderRequest(
                provider=TextProvider.NVIDIA,
                model="black-forest-labs/flux.2-klein-4b",
                prompt="A moonlit glass forest",
            ),
            credentials(TextProvider.NVIDIA),
        )

    assert result.content == image


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("finish_reason", "expected_message"),
    [
        (
            "CONTENT_FILTERED",
            "NVIDIA's content filter rejected the image prompt",
        ),
        (
            "ERROR",
            "NVIDIA failed to generate the image",
        ),
    ],
)
async def test_nvidia_generation_surfaces_non_success_finish_reason(
    finish_reason: str,
    expected_message: str,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "artifacts": [
                    {
                        "base64": "",
                        "finishReason": finish_reason,
                        "seed": 123,
                    },
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpImageGenerationProviderClient(http_client)
        with pytest.raises(
            ImageGenerationProviderError,
            match=expected_message,
        ):
            await client.generate(
                ImageGenerationProviderRequest(
                    provider=TextProvider.NVIDIA,
                    model="black-forest-labs/flux.2-klein-4b",
                    prompt="A moonlit glass forest",
                ),
                credentials(TextProvider.NVIDIA),
            )


@pytest.mark.asyncio
@pytest.mark.parametrize("finish_reason", [None, "UNKNOWN"])
async def test_nvidia_generation_rejects_invalid_finish_reason(
    finish_reason: str | None,
) -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        del request
        return httpx.Response(
            200,
            json={
                "artifacts": [
                    {
                        "base64": "not-used",
                        "finishReason": finish_reason,
                        "seed": 123,
                    },
                ],
            },
        )

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpImageGenerationProviderClient(http_client)
        with pytest.raises(
            ImageGenerationProviderError,
            match="NVIDIA returned an image artifact without a valid finish reason",
        ):
            await client.generate(
                ImageGenerationProviderRequest(
                    provider=TextProvider.NVIDIA,
                    model="black-forest-labs/flux.2-klein-4b",
                    prompt="A moonlit glass forest",
                ),
                credentials(TextProvider.NVIDIA),
            )


@pytest.mark.asyncio
async def test_xai_edit_sends_private_data_url_and_downloads_bounded_result() -> None:
    generated = b"edited-image"
    calls: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "POST":
            payload = orjson.loads(request.content)
            images = payload["images"]
            assert images[0]["url"] == "data:image/png;base64,c291cmNl"
            assert images[1]["url"] == "data:image/jpeg;base64,c2Vjb25k"
            return httpx.Response(
                200,
                json={"data": [{"url": "https://imgen.x.ai/result.jpeg"}]},
            )
        return httpx.Response(200, content=generated)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = HttpImageGenerationProviderClient(http_client)
        result = await client.generate(
            ImageGenerationProviderRequest(
                provider=TextProvider.XAI,
                model="grok-imagine-image-quality",
                prompt="Make it neon",
                inputs=(
                    ImageGenerationInput(
                        content=b"source",
                        mime_type="image/png",
                    ),
                    ImageGenerationInput(
                        content=b"second",
                        mime_type="image/jpeg",
                    ),
                ),
            ),
            credentials(TextProvider.XAI),
        )

    assert result.content == generated
    assert [request.method for request in calls] == ["POST", "GET"]


@pytest.mark.asyncio
async def test_xai_single_edit_uses_the_singular_image_contract() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST":
            payload = orjson.loads(request.content)
            assert payload["image"]["url"] == "data:image/png;base64,c291cmNl"
            assert "images" not in payload
            return httpx.Response(
                200,
                json={"data": [{"url": "https://imgen.x.ai/result.jpeg"}]},
            )
        return httpx.Response(200, content=b"edited-image")

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
    ) as http_client:
        client = HttpImageGenerationProviderClient(http_client)
        result = await client.generate(
            ImageGenerationProviderRequest(
                provider=TextProvider.XAI,
                model="grok-imagine-image-quality",
                prompt="Make it neon",
                inputs=(
                    ImageGenerationInput(
                        content=b"source",
                        mime_type="image/png",
                    ),
                ),
            ),
            credentials(TextProvider.XAI),
        )

    assert result.content == b"edited-image"


def credentials(provider: TextProvider) -> ResolvedTextCredentials:
    return ResolvedTextCredentials(
        provider=provider,
        reference="credential-1",
        token=SecretStr("provider-secret"),
    )
