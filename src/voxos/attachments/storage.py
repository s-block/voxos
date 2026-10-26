import asyncio
from pathlib import Path
from typing import Protocol

from aiobotocore.session import get_session

from voxos.attachments.settings import (
    AttachmentSettings,
    AttachmentStorageBackend,
)

AIOBOTOCORE_SESSION = get_session()


class AttachmentObjectStore(Protocol):
    async def delete(self, key: str) -> None:
        """Delete an object if it exists."""

    async def get(self, key: str) -> bytes:
        """Return a complete, bounded attachment object."""

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        """Persist an attachment object privately."""


class LocalAttachmentObjectStore:
    def __init__(self, root: Path, *, max_read_bytes: int) -> None:
        self._root = root.resolve()
        self._max_read_bytes = max_read_bytes

    async def delete(self, key: str) -> None:
        path = self._path(key)

        def unlink() -> None:
            path.unlink(missing_ok=True)
            parent = path.parent
            while parent != self._root:
                try:
                    parent.rmdir()
                except OSError:
                    break
                parent = parent.parent

        await asyncio.to_thread(unlink)

    async def get(self, key: str) -> bytes:
        path = self._path(key)

        def read() -> bytes:
            if path.stat().st_size > self._max_read_bytes:
                message = "Stored attachment exceeds the configured read limit"
                raise ValueError(message)
            return path.read_bytes()

        return await asyncio.to_thread(read)

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        del content_type
        path = self._path(key)

        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(f"{path.suffix}.part")
            temporary.write_bytes(content)
            temporary.replace(path)

        await asyncio.to_thread(write)

    def _path(self, key: str) -> Path:
        path = (self._root / key).resolve()
        if not path.is_relative_to(self._root):
            message = "Attachment object key escaped the configured root"
            raise ValueError(message)
        return path


class S3AttachmentObjectStore:
    def __init__(
        self,
        *,
        bucket: str,
        max_read_bytes: int,
        region: str | None,
    ) -> None:
        self._bucket = bucket
        self._max_read_bytes = max_read_bytes
        self._region = region

    async def delete(self, key: str) -> None:
        async with AIOBOTOCORE_SESSION.create_client(
            "s3",
            region_name=self._region,
        ) as client:
            await client.delete_object(Bucket=self._bucket, Key=key)

    async def get(self, key: str) -> bytes:
        async with AIOBOTOCORE_SESSION.create_client(
            "s3",
            region_name=self._region,
        ) as client:
            response = await client.get_object(Bucket=self._bucket, Key=key)
            async with response["Body"] as body:
                content = bytes(await body.read(self._max_read_bytes + 1))
        if len(content) > self._max_read_bytes:
            message = "Stored attachment exceeds the configured read limit"
            raise ValueError(message)
        return content

    async def put(self, key: str, content: bytes, content_type: str) -> None:
        async with AIOBOTOCORE_SESSION.create_client(
            "s3",
            region_name=self._region,
        ) as client:
            await client.put_object(
                Bucket=self._bucket,
                Key=key,
                Body=content,
                ContentType=content_type,
                ServerSideEncryption="AES256",
            )


def attachment_object_store(settings: AttachmentSettings) -> AttachmentObjectStore:
    if settings.backend == AttachmentStorageBackend.S3:
        if settings.s3_bucket is None:
            message = "Attachment S3 bucket is not configured"
            raise ValueError(message)
        return S3AttachmentObjectStore(
            bucket=settings.s3_bucket,
            max_read_bytes=settings.max_image_bytes,
            region=settings.s3_region,
        )
    return LocalAttachmentObjectStore(
        settings.local_path,
        max_read_bytes=settings.max_image_bytes,
    )
