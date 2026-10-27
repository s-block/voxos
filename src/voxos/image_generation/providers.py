import base64
import binascii
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, cast

import httpx
import orjson

from voxos.text.models import ResolvedTextCredentials, TextProvider

_MAX_PROVIDER_RESPONSE_BYTES = 32 * 1024 * 1024
_MAX_DOWNLOADED_IMAGE_BYTES = 20 * 1024 * 1024
_PROVIDER_TIMEOUT = httpx.Timeout(180.0, connect=15.0)


class ImageGenerationProviderError(RuntimeError):
    """A safe provider-facing image generation failure."""


class _NvidiaImageFinishReason(StrEnum):
    CONTENT_FILTERED = "CONTENT_FILTERED"
    ERROR = "ERROR"
    SUCCESS = "SUCCESS"


@dataclass(frozen=True, slots=True)
class ImageGenerationInput:
    content: bytes
    mime_type: str

    @property
    def data_url(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.mime_type};base64,{encoded}"


@dataclass(frozen=True, slots=True)
class ImageGenerationProviderRequest:
    provider: TextProvider
    model: str
    prompt: str
    inputs: tuple[ImageGenerationInput, ...] = ()


@dataclass(frozen=True, slots=True)
class GeneratedImage:
    content: bytes


class ImageGenerationProviderClient(Protocol):
    async def generate(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        """Generate or edit one image."""


class HttpImageGenerationProviderClient:
    def __init__(self, http_client: httpx.AsyncClient) -> None:
        self._http_client = http_client

    async def generate(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        generator = {
            TextProvider.NVIDIA: self._generate_nvidia,
            TextProvider.OPENAI: self._generate_openai,
            TextProvider.XAI: self._generate_xai,
        }.get(request.provider)
        if generator is None:
            message = f"{request.provider.value} does not support image generation"
            raise ImageGenerationProviderError(message)
        return await generator(request, credentials)

    async def _generate_nvidia(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        if request.inputs:
            message = (
                "NVIDIA hosted image editing does not accept private user images; enable OpenAI or xAI to edit images"
            )
            raise ImageGenerationProviderError(message)
        response = await self._post_json(
            f"https://ai.api.nvidia.com/v1/genai/{request.model}",
            credentials=credentials,
            payload={
                "prompt": request.prompt,
                "width": 1024,
                "height": 1024,
                "seed": 0,
                "steps": 4,
            },
        )
        payload = _response_object(response)
        artifacts = payload.get("artifacts")
        if not isinstance(artifacts, list) or not artifacts:
            message = "NVIDIA returned an image response without an artifact"
            raise ImageGenerationProviderError(message)
        artifact = artifacts[0]
        if not isinstance(artifact, dict):
            message = "NVIDIA returned an invalid image artifact"
            raise ImageGenerationProviderError(message)
        return GeneratedImage(content=_nvidia_image_content(artifact))

    async def _generate_openai(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        if request.inputs:
            files = [
                (
                    "image[]",
                    (f"input-{index}.png", image.content, image.mime_type),
                )
                for index, image in enumerate(request.inputs)
            ]
            response = await self._post_multipart(
                "https://api.openai.com/v1/images/edits",
                credentials=credentials,
                data={
                    "model": request.model,
                    "prompt": request.prompt,
                    "n": "1",
                    "size": "1024x1024",
                },
                files=files,
            )
        else:
            response = await self._post_json(
                "https://api.openai.com/v1/images/generations",
                credentials=credentials,
                payload={
                    "model": request.model,
                    "prompt": request.prompt,
                    "n": 1,
                    "size": "1024x1024",
                },
            )
        return GeneratedImage(content=_openai_compatible_image(response))

    async def _generate_xai(
        self,
        request: ImageGenerationProviderRequest,
        credentials: ResolvedTextCredentials,
    ) -> GeneratedImage:
        payload: dict[str, object] = {
            "model": request.model,
            "prompt": request.prompt,
            "n": 1,
            "response_format": "url",
        }
        endpoint = "https://api.x.ai/v1/images/generations"
        if request.inputs:
            endpoint = "https://api.x.ai/v1/images/edits"
            encoded_inputs = [{"type": "image_url", "url": image.data_url} for image in request.inputs]
            if len(encoded_inputs) == 1:
                payload["image"] = encoded_inputs[0]
            else:
                payload["images"] = encoded_inputs
        response = await self._post_json(
            endpoint,
            credentials=credentials,
            payload=payload,
        )
        response_payload = _response_object(response)
        data = response_payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            message = "xAI returned an image response without image data"
            raise ImageGenerationProviderError(message)
        item = cast(dict[str, object], data[0])
        encoded = item.get("b64_json")
        if isinstance(encoded, str):
            return GeneratedImage(content=_decoded_image(encoded))
        url = item.get("url")
        if not isinstance(url, str):
            message = "xAI returned an image response without a download URL"
            raise ImageGenerationProviderError(message)
        return GeneratedImage(content=await self._download_xai_image(url))

    async def _post_json(
        self,
        url: str,
        *,
        credentials: ResolvedTextCredentials,
        payload: dict[str, object],
    ) -> httpx.Response:
        try:
            response = await self._http_client.post(
                url,
                content=orjson.dumps(payload),
                headers={
                    "Accept": "application/json",
                    "Authorization": (f"Bearer {credentials.token.get_secret_value()}"),
                    "Content-Type": "application/json",
                },
                timeout=_PROVIDER_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = f"{credentials.provider.value} image generation failed with status {exc.response.status_code}"
            raise ImageGenerationProviderError(message) from exc
        except httpx.HTTPError as exc:
            message = f"{credentials.provider.value} image generation is unavailable"
            raise ImageGenerationProviderError(message) from exc
        _validate_response_size(response)
        return response

    async def _post_multipart(
        self,
        url: str,
        *,
        credentials: ResolvedTextCredentials,
        data: dict[str, str],
        files: list[tuple[str, tuple[str, bytes, str]]],
    ) -> httpx.Response:
        try:
            response = await self._http_client.post(
                url,
                data=data,
                files=files,
                headers={
                    "Accept": "application/json",
                    "Authorization": (f"Bearer {credentials.token.get_secret_value()}"),
                },
                timeout=_PROVIDER_TIMEOUT,
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            message = f"{credentials.provider.value} image editing failed with status {exc.response.status_code}"
            raise ImageGenerationProviderError(message) from exc
        except httpx.HTTPError as exc:
            message = f"{credentials.provider.value} image editing is unavailable"
            raise ImageGenerationProviderError(message) from exc
        _validate_response_size(response)
        return response

    async def _download_xai_image(self, url: str) -> bytes:
        parsed = httpx.URL(url)
        host = parsed.host
        if (
            parsed.scheme != "https"
            or host is None
            or not (host == "x.ai" or host.endswith(".x.ai"))
            or parsed.userinfo
            or parsed.port not in {None, 443}
        ):
            message = "xAI returned an unsafe image download URL"
            raise ImageGenerationProviderError(message)
        try:
            async with self._http_client.stream(
                "GET",
                parsed,
                headers={"Accept": "image/*"},
                follow_redirects=False,
                timeout=_PROVIDER_TIMEOUT,
            ) as response:
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(content) + len(chunk) > _MAX_DOWNLOADED_IMAGE_BYTES:
                        message = "xAI returned an image larger than the supported limit"
                        raise ImageGenerationProviderError(message)
                    content.extend(chunk)
        except ImageGenerationProviderError:
            raise
        except httpx.HTTPError as exc:
            message = "The generated xAI image could not be downloaded"
            raise ImageGenerationProviderError(message) from exc
        if not content:
            message = "xAI returned an empty image"
            raise ImageGenerationProviderError(message)
        return bytes(content)


def _response_object(response: httpx.Response) -> dict[str, object]:
    try:
        payload = orjson.loads(response.content)
    except orjson.JSONDecodeError as exc:
        message = "The image provider returned invalid JSON"
        raise ImageGenerationProviderError(message) from exc
    if not isinstance(payload, dict):
        message = "The image provider returned an invalid response"
        raise ImageGenerationProviderError(message)
    return cast(dict[str, object], payload)


def _openai_compatible_image(response: httpx.Response) -> bytes:
    payload = _response_object(response)
    data = payload.get("data")
    if not isinstance(data, list) or not data or not isinstance(data[0], dict):
        message = "OpenAI returned an image response without image data"
        raise ImageGenerationProviderError(message)
    return _decoded_image(data[0].get("b64_json"))


def _nvidia_image_content(artifact: dict[str, object]) -> bytes:
    finish_reason_value = artifact.get("finishReason")
    if not isinstance(finish_reason_value, str):
        message = "NVIDIA returned an image artifact without a valid finish reason"
        raise ImageGenerationProviderError(message)
    try:
        finish_reason = _NvidiaImageFinishReason(finish_reason_value)
    except ValueError as exc:
        message = "NVIDIA returned an image artifact without a valid finish reason"
        raise ImageGenerationProviderError(message) from exc

    if finish_reason == _NvidiaImageFinishReason.CONTENT_FILTERED:
        message = (
            "NVIDIA's content filter rejected the image prompt. Do not retry it "
            "unchanged; revise the prompt to remove or generalize potentially "
            "restricted content."
        )
        raise ImageGenerationProviderError(message)
    if finish_reason == _NvidiaImageFinishReason.ERROR:
        message = (
            "NVIDIA failed to generate the image. Retry once with a newly composed "
            "equivalent prompt; if it fails again, tell the user that image generation "
            "is temporarily unavailable."
        )
        raise ImageGenerationProviderError(message)
    return _decoded_image(artifact.get("base64"))


def _decoded_image(value: object) -> bytes:
    if not isinstance(value, str) or not value:
        message = "The image provider returned an image without encoded content"
        raise ImageGenerationProviderError(message)
    if len(value) > _MAX_PROVIDER_RESPONSE_BYTES * 2:
        message = "The image provider returned an image larger than the supported limit"
        raise ImageGenerationProviderError(message)
    try:
        content = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        message = "The image provider returned invalid encoded image content"
        raise ImageGenerationProviderError(message) from exc
    if not content or len(content) > _MAX_DOWNLOADED_IMAGE_BYTES:
        message = "The image provider returned an image outside the supported size limit"
        raise ImageGenerationProviderError(message)
    return content


def _validate_response_size(response: httpx.Response) -> None:
    if len(response.content) > _MAX_PROVIDER_RESPONSE_BYTES:
        message = "The image provider response exceeded the supported size limit"
        raise ImageGenerationProviderError(message)
