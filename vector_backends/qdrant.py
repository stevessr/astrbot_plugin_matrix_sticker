from __future__ import annotations

import inspect
import json
import zlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import numpy as np

from astrbot.api import logger

from .base import StickerVectorBackend

if TYPE_CHECKING:
    from ..vector_index import StickerVectorDocument, StickerVectorQueryResult


class QdrantStickerVectorBackend(StickerVectorBackend):
    backend_type = "qdrant"
    SCHEMA_VERSION = 1
    DEFAULT_COLLECTION = "matrix_sticker_vectors"
    DEFAULT_TIMEOUT = 10
    _UPSERT_BATCH_SIZE = 256
    _SCROLL_BATCH_SIZE = 256

    def __init__(
        self,
        base_dir: str | Path,
        backend_config: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(base_dir, backend_config=backend_config)
        self.backend_config = self._normalize_backend_config(self.backend_config)
        self.collection_name = self.backend_config["collection"]
        self.manifest_path = self.base_dir / "manifest.json"
        self.client: Any | None = None
        self.models: Any | None = None

    @classmethod
    def _normalize_backend_config(
        cls,
        backend_config: dict[str, Any] | None,
    ) -> dict[str, Any]:
        raw_config = dict(backend_config) if isinstance(backend_config, dict) else {}
        collection = str(
            raw_config.get("collection", cls.DEFAULT_COLLECTION)
            or cls.DEFAULT_COLLECTION
        ).strip()
        if not collection:
            collection = cls.DEFAULT_COLLECTION
        try:
            timeout = int(raw_config.get("timeout", cls.DEFAULT_TIMEOUT))
        except (TypeError, ValueError):
            timeout = cls.DEFAULT_TIMEOUT
        return {
            "url": str(raw_config.get("url", "") or "").strip(),
            "api_key": str(raw_config.get("api_key", "") or "").strip(),
            "collection": collection,
            "prefer_grpc": cls._parse_bool(raw_config.get("prefer_grpc", False)),
            "timeout": max(1, timeout),
        }

    @staticmethod
    def _parse_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        return str(value or "").strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
            "enable",
            "enabled",
        }

    def _load_qdrant_sdk(self) -> tuple[Any, Any]:
        try:
            from qdrant_client import AsyncQdrantClient, models
        except ImportError as e:
            raise ModuleNotFoundError(
                "缺少 qdrant-client 依赖，请安装插件 requirements.txt 中声明的 qdrant-client"
            ) from e
        return AsyncQdrantClient, models

    async def initialize(self, dimension: int) -> None:
        self.dimension = int(dimension)
        self.collection_name = self.backend_config["collection"]
        if not self.backend_config["url"]:
            raise ValueError("Qdrant 向量后端缺少 qdrant.url 配置")
        if self.client is None:
            client_cls, models = self._load_qdrant_sdk()
            self.models = models
            if self.backend_config["url"] == ":memory:":
                self.client = client_cls(":memory:")
            else:
                client_kwargs: dict[str, Any] = {
                    "url": self.backend_config["url"],
                    "timeout": self.backend_config["timeout"],
                    "prefer_grpc": self.backend_config["prefer_grpc"],
                }
                if self.backend_config["api_key"]:
                    client_kwargs["api_key"] = self.backend_config["api_key"]
                self.client = client_cls(**client_kwargs)
        await self._ensure_collection(self.dimension)

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"加载 Qdrant sticker manifest 失败：{e}")
            return {}

    def save_manifest(self, **overrides: Any) -> dict[str, Any]:
        manifest = self.load_manifest()
        manifest.update(overrides)
        manifest.setdefault("backend_type", self.backend_type)
        manifest.setdefault("schema_version", self.SCHEMA_VERSION)
        manifest.setdefault("collection_name", self.collection_name)
        manifest.setdefault("qdrant_url", self.backend_config["url"])
        manifest.setdefault("prefer_grpc", self.backend_config["prefer_grpc"])
        self.manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return manifest

    def check_compatibility(
        self,
        embedding_provider_id: str,
        embedding_dimension: int,
        schema_version: int | None = None,
    ) -> tuple[bool, str | None, dict[str, Any]]:
        manifest = self.load_manifest()
        if not manifest:
            return False, "manifest_missing", manifest
        expected_schema = schema_version or self.SCHEMA_VERSION
        if manifest.get("backend_type", self.backend_type) != self.backend_type:
            return False, "backend_type_mismatch", manifest
        if int(manifest.get("schema_version") or 0) != int(expected_schema):
            return False, "schema_version_mismatch", manifest
        if manifest.get("embedding_provider_id") != embedding_provider_id:
            return False, "embedding_provider_id_mismatch", manifest
        if int(manifest.get("embedding_dimension") or 0) != int(embedding_dimension):
            return False, "embedding_dimension_mismatch", manifest
        if manifest.get("collection_name") != self.collection_name:
            return False, "collection_name_mismatch", manifest
        if manifest.get("qdrant_url") != self.backend_config["url"]:
            return False, "qdrant_url_mismatch", manifest
        if bool(manifest.get("prefer_grpc", False)) != bool(
            self.backend_config["prefer_grpc"]
        ):
            return False, "prefer_grpc_mismatch", manifest
        return True, None, manifest

    async def reset(self, embedding_provider_id: str, embedding_dimension: int) -> None:
        await self.initialize(embedding_dimension)
        if await self._collection_exists():
            await self.client.delete_collection(collection_name=self.collection_name)
        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self.models.VectorParams(
                size=int(embedding_dimension),
                distance=self.models.Distance.COSINE,
            ),
        )
        self.save_manifest(
            backend_type=self.backend_type,
            schema_version=self.SCHEMA_VERSION,
            embedding_provider_id=embedding_provider_id,
            embedding_dimension=int(embedding_dimension),
            collection_name=self.collection_name,
            qdrant_url=self.backend_config["url"],
            prefer_grpc=self.backend_config["prefer_grpc"],
            index_built_at=None,
            indexed_count=0,
        )

    async def rebuild_full(
        self,
        embedding_provider_id: str,
        embedding_dimension: int,
        entries: list[StickerVectorDocument],
    ) -> int:
        await self.reset(embedding_provider_id, embedding_dimension)
        if not entries:
            self.save_manifest(
                backend_type=self.backend_type,
                schema_version=self.SCHEMA_VERSION,
                embedding_provider_id=embedding_provider_id,
                embedding_dimension=int(embedding_dimension),
                collection_name=self.collection_name,
                qdrant_url=self.backend_config["url"],
                prefer_grpc=self.backend_config["prefer_grpc"],
                index_built_at=self._now_iso(),
                indexed_count=0,
            )
            return 0

        points = [
            self._build_point_from_entry(entry, embedding_dimension)
            for entry in entries
        ]
        for batch in self._iter_chunks(points, self._UPSERT_BATCH_SIZE):
            await self.client.upsert(
                collection_name=self.collection_name,
                points=batch,
                wait=True,
            )

        self.save_manifest(
            backend_type=self.backend_type,
            schema_version=self.SCHEMA_VERSION,
            embedding_provider_id=embedding_provider_id,
            embedding_dimension=int(embedding_dimension),
            collection_name=self.collection_name,
            qdrant_url=self.backend_config["url"],
            prefer_grpc=self.backend_config["prefer_grpc"],
            index_built_at=self._now_iso(),
            indexed_count=len(entries),
        )
        return len(entries)

    async def upsert(
        self,
        sticker_id: str,
        vector: list[float],
        text: str,
        metadata: dict[str, Any],
    ) -> None:
        assert self.dimension is not None, "向量索引尚未初始化"
        assert self.client is not None
        assert self.models is not None
        point = self.models.PointStruct(
            id=str(sticker_id),
            vector=self._normalize_vector(
                self._coerce_vector(vector, self.dimension),
            ),
            payload=self._build_payload(sticker_id, text, metadata),
        )
        await self.client.upsert(
            collection_name=self.collection_name,
            points=[point],
            wait=True,
        )
        await self._refresh_manifest_counts()

    async def delete(self, sticker_ids: list[str]) -> int:
        assert self.client is not None
        sticker_ids = [str(sticker_id) for sticker_id in sticker_ids if str(sticker_id)]
        if not sticker_ids:
            return 0
        existing = await self.client.retrieve(
            collection_name=self.collection_name,
            ids=sticker_ids,
            with_payload=False,
            with_vectors=False,
        )
        deleted = len(existing)
        if deleted <= 0:
            return 0
        try:
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=sticker_ids,
                wait=True,
            )
        except TypeError:
            selector_cls = getattr(self.models, "PointIdsList", None)
            selector = (
                selector_cls(points=sticker_ids)
                if selector_cls is not None
                else sticker_ids
            )
            await self.client.delete(
                collection_name=self.collection_name,
                points_selector=selector,
                wait=True,
            )
        await self._refresh_manifest_counts()
        return deleted

    async def list_documents(self, limit: int = 50000) -> list[StickerVectorDocument]:
        from ..vector_index import StickerVectorDocument

        assert self.client is not None
        if limit <= 0:
            return []

        results: list[StickerVectorDocument] = []
        offset: Any = None
        while len(results) < limit:
            batch_limit = min(self._SCROLL_BATCH_SIZE, limit - len(results))
            points, next_offset = await self.client.scroll(
                collection_name=self.collection_name,
                limit=batch_limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            if not points:
                break
            for point in points:
                payload = self._coerce_payload(getattr(point, "payload", None))
                sticker_id = str(
                    payload.get("sticker_id") or getattr(point, "id", "") or ""
                )
                results.append(
                    StickerVectorDocument(
                        sticker_id=sticker_id,
                        text=str(payload.get("text", "") or ""),
                        metadata=self._parse_metadata(payload.get("metadata")),
                        db_id=self._coerce_db_id(getattr(point, "id", None)),
                    )
                )
                if len(results) >= limit:
                    break
            if next_offset is None:
                break
            offset = next_offset
        return results

    async def search(
        self,
        query_vector: list[float],
        fetch_k: int,
    ) -> list[StickerVectorQueryResult]:
        from ..vector_index import StickerVectorQueryResult

        assert self.dimension is not None, "向量索引尚未初始化"
        assert self.client is not None
        if fetch_k <= 0:
            return []

        vector = self._normalize_vector(
            self._coerce_vector(query_vector, self.dimension),
        )
        points = await self._query_points(vector, fetch_k)
        results: list[StickerVectorQueryResult] = []
        for point in points:
            payload = self._coerce_payload(getattr(point, "payload", None))
            sticker_id = str(
                payload.get("sticker_id") or getattr(point, "id", "") or ""
            )
            results.append(
                StickerVectorQueryResult(
                    sticker_id=sticker_id,
                    similarity=float(getattr(point, "score", 0.0) or 0.0),
                    text=str(payload.get("text", "") or ""),
                    metadata=self._parse_metadata(payload.get("metadata")),
                    db_id=self._coerce_db_id(getattr(point, "id", None)),
                )
            )
        return results

    def get_status(self) -> dict[str, Any]:
        manifest = self.load_manifest()
        return {
            "backend_type": manifest.get("backend_type", self.backend_type),
            "implemented": True,
            "schema_version": manifest.get("schema_version"),
            "embedding_provider_id": manifest.get("embedding_provider_id"),
            "embedding_dimension": manifest.get("embedding_dimension"),
            "index_built_at": manifest.get("index_built_at"),
            "indexed_count": int(manifest.get("indexed_count") or 0),
            "collection_name": manifest.get("collection_name", self.collection_name),
            "qdrant_url": manifest.get("qdrant_url", self.backend_config["url"]),
            "prefer_grpc": manifest.get(
                "prefer_grpc",
                self.backend_config["prefer_grpc"],
            ),
            "client_initialized": self.client is not None,
            "manifest_path": str(self.manifest_path),
            "backend_config": dict(self.backend_config),
        }

    async def close(self) -> None:
        if self.client is not None:
            close = getattr(self.client, "close", None)
            if callable(close):
                result = close()
                if inspect.isawaitable(result):
                    await result
        self.client = None
        self.models = None
        self.dimension = None

    async def _ensure_collection(self, dimension: int) -> None:
        assert self.client is not None
        assert self.models is not None
        if await self._collection_exists():
            return
        await self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=self.models.VectorParams(
                size=int(dimension),
                distance=self.models.Distance.COSINE,
            ),
        )

    async def _collection_exists(self) -> bool:
        assert self.client is not None
        collection_exists = getattr(self.client, "collection_exists", None)
        if callable(collection_exists):
            return bool(await collection_exists(self.collection_name))
        try:
            await self.client.get_collection(self.collection_name)
        except Exception:
            return False
        return True

    async def _query_points(self, query_vector: list[float], fetch_k: int) -> list[Any]:
        assert self.client is not None
        search = getattr(self.client, "search", None)
        if callable(search):
            return list(
                await search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=fetch_k,
                    with_payload=True,
                    with_vectors=False,
                )
            )
        query_points = getattr(self.client, "query_points", None)
        if callable(query_points):
            result = await query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=fetch_k,
                with_payload=True,
                with_vectors=False,
            )
            return list(getattr(result, "points", None) or [])
        raise RuntimeError("当前 qdrant-client 不支持 search/query_points")

    async def _refresh_manifest_counts(self) -> None:
        self.save_manifest(
            backend_type=self.backend_type,
            indexed_count=await self._get_points_count(),
            index_built_at=self._now_iso(),
            collection_name=self.collection_name,
            qdrant_url=self.backend_config["url"],
            prefer_grpc=self.backend_config["prefer_grpc"],
        )

    async def _get_points_count(self) -> int:
        assert self.client is not None
        try:
            collection_info = await self.client.get_collection(self.collection_name)
        except Exception as e:
            logger.debug(f"读取 Qdrant collection 信息失败，将回退 scroll 计数：{e}")
            return await self._count_points_by_scroll()
        for attr in ("points_count", "vectors_count"):
            value = getattr(collection_info, attr, None)
            if value is not None:
                return int(value)
        return await self._count_points_by_scroll()

    async def _count_points_by_scroll(self) -> int:
        assert self.client is not None
        total = 0
        offset: Any = None
        while True:
            points, next_offset = await self.client.scroll(
                collection_name=self.collection_name,
                limit=self._SCROLL_BATCH_SIZE,
                offset=offset,
                with_payload=False,
                with_vectors=False,
            )
            total += len(points)
            if next_offset is None:
                return total
            offset = next_offset

    def _build_point_from_entry(
        self,
        entry: StickerVectorDocument,
        expected_dim: int,
    ) -> Any:
        assert self.models is not None
        return self.models.PointStruct(
            id=str(entry.sticker_id),
            vector=self._normalize_vector(
                self._coerce_entry_vector(entry, expected_dim),
            ),
            payload=self._build_payload(entry.sticker_id, entry.text, entry.metadata),
        )

    @staticmethod
    def _build_payload(
        sticker_id: str,
        text: str,
        metadata: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "sticker_id": str(sticker_id),
            "text": str(text or ""),
            "metadata": dict(metadata or {}),
        }

    @staticmethod
    def _coerce_payload(payload: Any) -> dict[str, Any]:
        if isinstance(payload, dict):
            return payload
        return {}

    @staticmethod
    def _parse_metadata(raw_metadata: Any) -> dict[str, Any]:
        if isinstance(raw_metadata, dict):
            return raw_metadata
        if isinstance(raw_metadata, str) and raw_metadata:
            try:
                return json.loads(raw_metadata)
            except Exception:
                return {}
        return {}

    @classmethod
    def _coerce_entry_vector(
        cls,
        entry: StickerVectorDocument,
        expected_dim: int,
    ) -> list[float]:
        vector = entry.vector
        if vector is None:
            vector = entry.metadata.get("vector")
        if not isinstance(vector, list):
            raise ValueError(f"sticker {entry.sticker_id} 缺少可用向量")
        return cls._coerce_vector(vector, expected_dim)

    @staticmethod
    def _coerce_vector(vector: list[float], expected_dim: int) -> list[float]:
        if len(vector) != expected_dim:
            raise ValueError(f"向量维度不匹配，期望 {expected_dim}，实际 {len(vector)}")
        return [float(value) for value in vector]

    @staticmethod
    def _normalize_vector(vector: list[float]) -> list[float]:
        arr = np.array(vector, dtype=np.float32)
        norm = float(np.linalg.norm(arr))
        if norm > 0:
            arr /= norm
        return arr.astype(np.float32).tolist()

    @staticmethod
    def _coerce_db_id(raw_id: Any) -> int:
        if isinstance(raw_id, int):
            return raw_id
        return int(zlib.crc32(str(raw_id or "").encode("utf-8")) & 0x7FFFFFFF)

    @staticmethod
    def _iter_chunks(items: list[Any], size: int) -> list[list[Any]]:
        return [items[idx : idx + size] for idx in range(0, len(items), size)]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()
