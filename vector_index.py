from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .vector_backends import (
    FaissStickerVectorBackend,
    QdrantStickerVectorBackend,
    StickerVectorBackend,
)


@dataclass(slots=True)
class StickerVectorDocument:
    sticker_id: str
    text: str
    metadata: dict[str, Any]
    vector: list[float] | None = None
    db_id: int | None = None


@dataclass(slots=True)
class StickerVectorQueryResult:
    sticker_id: str
    similarity: float
    text: str
    metadata: dict[str, Any]
    db_id: int


class StickerVectorIndex:
    SUPPORTED_BACKENDS = {
        "faiss": FaissStickerVectorBackend,
        "qdrant": QdrantStickerVectorBackend,
    }

    def __init__(
        self,
        base_dir: str | Path,
        backend_type: str = "faiss",
        backend_config: dict[str, Any] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.backend_type = str(backend_type or "faiss").strip().lower() or "faiss"
        self.backend_config = (
            dict(backend_config) if isinstance(backend_config, dict) else {}
        )
        backend_cls = self.SUPPORTED_BACKENDS.get(self.backend_type)
        if backend_cls is None:
            supported = ", ".join(sorted(self.SUPPORTED_BACKENDS))
            raise ValueError(
                f"不支持的向量后端：{self.backend_type}，可选值：{supported}"
            )
        self.backend: StickerVectorBackend = backend_cls(
            self.base_dir,
            backend_config=self.backend_config,
        )
        self.backend_config = dict(self.backend.backend_config)

    async def initialize(self, dimension: int) -> None:
        await self.backend.initialize(dimension)

    def check_compatibility(
        self,
        embedding_provider_id: str,
        embedding_dimension: int,
        schema_version: int | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        return self.backend.check_compatibility(
            embedding_provider_id,
            embedding_dimension,
            schema_version=schema_version,
        )

    async def rebuild_full(
        self,
        embedding_provider_id: str,
        embedding_dimension: int,
        entries: list[StickerVectorDocument],
    ) -> int:
        return await self.backend.rebuild_full(
            embedding_provider_id,
            embedding_dimension,
            entries,
        )

    async def upsert(
        self,
        sticker_id: str,
        vector: list[float],
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        await self.backend.upsert(sticker_id, vector, text, metadata)

    async def delete(self, sticker_ids: list[str]) -> int:
        return await self.backend.delete(sticker_ids)

    async def list_documents(self, limit: int = 50000) -> list[StickerVectorDocument]:
        return await self.backend.list_documents(limit=limit)

    async def search(
        self,
        query_vector: list[float],
        fetch_k: int,
    ) -> list[StickerVectorQueryResult]:
        return await self.backend.search(query_vector, fetch_k)

    def get_status(self) -> dict[str, Any]:
        status = self.backend.get_status()
        status.setdefault("backend_type", self.backend_type)
        status.setdefault("backend_config", dict(self.backend_config))
        return status

    async def close(self) -> None:
        await self.backend.close()
