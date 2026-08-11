from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class ObjectReaderClient(Protocol):
    def get_object(self, **kwargs: Any) -> Mapping[str, Any]: ...


class S3SourceObjectReader:
    """Read immutable source objects for worker-side verification and parsing."""

    def __init__(self, *, client: ObjectReaderClient, bucket: str) -> None:
        if not bucket:
            raise ValueError("bucket must not be empty")
        self._client = client
        self._bucket = bucket

    async def read_bytes(self, object_key: str) -> bytes:
        return await asyncio.to_thread(self._read_bytes, object_key)

    async def download(self, object_key: str, destination: Path) -> None:
        payload = await self.read_bytes(object_key)
        destination.write_bytes(payload)

    async def sha256(self, object_key: str) -> str:
        payload = await self.read_bytes(object_key)
        return hashlib.sha256(payload).hexdigest()

    def _read_bytes(self, object_key: str) -> bytes:
        response = self._client.get_object(Bucket=self._bucket, Key=object_key)
        body = response.get("Body")
        if body is None or not hasattr(body, "read"):
            raise ValueError("object response did not include a readable body")
        try:
            chunks: list[bytes] = []
            while chunk := body.read(1024 * 1024):
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()
