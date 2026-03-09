from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..vector_index import StickerVectorDocument, StickerVectorQueryResult


class StickerVectorBackend(ABC):
    backend_type = "unknown"

    def __init__(
        self,
        base_dir: str | Path,
        backend_config: dict[str, Any] | None = None,
    ) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.dimension: int | None = None
        self.backend_config = backend_config or {}

    @abstractmethod
    async def initialize(self, dimension: int) -> None:
        raise NotImplementedError

    @abstractmethod
    def check_compatibility(
        self,
        embedding_provider_id: str,
        embedding_dimension: int,
        schema_version: int | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    async def rebuild_full(
        self,
        embedding_provider_id: str,
        embedding_dimension: int,
        entries: list[StickerVectorDocument],
    ) -> int:
        raise NotImplementedError

    @abstractmethod
    async def upsert(
        self,
        sticker_id: str,
        vector: list[float],
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    async def delete(self, sticker_ids: list[str]) -> int:
        raise NotImplementedError

    @abstractmethod
    async def list_documents(self, limit: int = 50000) -> list[StickerVectorDocument]:
        raise NotImplementedError

    @abstractmethod
    async def search(
        self,
        query_vector: list[float],
        fetch_k: int,
    ) -> list[StickerVectorQueryResult]:
        raise NotImplementedError

    @abstractmethod
    def get_status(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    async def close(self) -> None:
        raise NotImplementedError
