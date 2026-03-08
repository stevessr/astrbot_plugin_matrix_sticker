from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from astrbot.api import logger
from astrbot.core.db.vec_db.faiss_impl.document_storage import DocumentStorage
from astrbot.core.db.vec_db.faiss_impl.embedding_storage import EmbeddingStorage


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
    SCHEMA_VERSION = 1

    def __init__(self, base_dir: str | Path) -> None:
        self.base_dir = Path(base_dir)
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.documents_path = self.base_dir / "documents.sqlite3"
        self.faiss_path = self.base_dir / "vectors.faiss"
        self.manifest_path = self.base_dir / "manifest.json"
        self.document_storage: DocumentStorage | None = None
        self.embedding_storage: EmbeddingStorage | None = None
        self.dimension: int | None = None

    async def initialize(self, dimension: int) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        if self.document_storage is None:
            self.document_storage = DocumentStorage(str(self.documents_path))
            await self.document_storage.initialize()
        if self.embedding_storage is None or self.dimension != dimension:
            self.embedding_storage = EmbeddingStorage(dimension, str(self.faiss_path))
        self.dimension = dimension

    def load_manifest(self) -> dict[str, Any]:
        if not self.manifest_path.exists():
            return {}
        try:
            return json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"加载 sticker 向量索引 manifest 失败：{e}")
            return {}

    def save_manifest(self, **overrides: Any) -> dict[str, Any]:
        manifest = self.load_manifest()
        manifest.update(overrides)
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
        if manifest.get("schema_version") != expected_schema:
            return False, "schema_version_mismatch", manifest
        if manifest.get("embedding_provider_id") != embedding_provider_id:
            return False, "embedding_provider_id_mismatch", manifest
        if int(manifest.get("embedding_dimension") or 0) != int(embedding_dimension):
            return False, "embedding_dimension_mismatch", manifest
        return True, None, manifest

    async def reset(self, embedding_provider_id: str, embedding_dimension: int) -> None:
        await self.close()
        for path in (self.documents_path, self.faiss_path):
            if path.exists():
                path.unlink()
        await self.initialize(embedding_dimension)
        self.save_manifest(
            schema_version=self.SCHEMA_VERSION,
            embedding_provider_id=embedding_provider_id,
            embedding_dimension=int(embedding_dimension),
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
                schema_version=self.SCHEMA_VERSION,
                embedding_provider_id=embedding_provider_id,
                embedding_dimension=int(embedding_dimension),
                index_built_at=self._now_iso(),
                indexed_count=0,
            )
            return 0

        assert self.document_storage is not None
        assert self.embedding_storage is not None

        doc_ids = [entry.sticker_id for entry in entries]
        texts = [entry.text for entry in entries]
        metadatas = [entry.metadata for entry in entries]
        db_ids = await self.document_storage.insert_documents_batch(doc_ids, texts, metadatas)

        vectors = np.array(
            [self._coerce_entry_vector(entry, embedding_dimension) for entry in entries],
            dtype=np.float32,
        )
        self._normalize_matrix(vectors)
        await self.embedding_storage.insert_batch(vectors, db_ids)

        self.save_manifest(
            schema_version=self.SCHEMA_VERSION,
            embedding_provider_id=embedding_provider_id,
            embedding_dimension=int(embedding_dimension),
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
        assert self.document_storage is not None
        assert self.embedding_storage is not None

        existing = await self.document_storage.get_document_by_doc_id(sticker_id)
        if existing is not None:
            existing_db_id = int(existing["id"])
            await self.embedding_storage.delete([existing_db_id])
            await self.document_storage.delete_document_by_doc_id(sticker_id)

        new_db_id = await self.document_storage.insert_document(sticker_id, text, metadata)
        coerced = np.array(
            self._coerce_vector(vector, self.dimension),
            dtype=np.float32,
        )
        self._normalize_vector(coerced)
        await self.embedding_storage.insert(coerced, new_db_id)
        await self._refresh_manifest_counts()

    async def delete(self, sticker_ids: list[str]) -> int:
        assert self.document_storage is not None
        assert self.embedding_storage is not None
        deleted = 0
        faiss_ids: list[int] = []
        for sticker_id in sticker_ids:
            existing = await self.document_storage.get_document_by_doc_id(sticker_id)
            if existing is None:
                continue
            faiss_ids.append(int(existing["id"]))
            await self.document_storage.delete_document_by_doc_id(sticker_id)
            deleted += 1
        if faiss_ids:
            await self.embedding_storage.delete(faiss_ids)
        if deleted:
            await self._refresh_manifest_counts()
        return deleted

    async def list_documents(self, limit: int = 50000) -> list[StickerVectorDocument]:
        assert self.document_storage is not None
        count = await self.document_storage.count_documents()
        docs = await self.document_storage.get_documents(
            metadata_filters={},
            limit=max(limit, count),
        )
        results: list[StickerVectorDocument] = []
        for doc in docs:
            metadata = self._parse_metadata(doc.get("metadata"))
            results.append(
                StickerVectorDocument(
                    sticker_id=str(doc.get("doc_id") or ""),
                    text=str(doc.get("text") or ""),
                    metadata=metadata,
                    db_id=int(doc["id"]) if doc.get("id") is not None else None,
                )
            )
        return results

    async def search(
        self,
        query_vector: list[float],
        fetch_k: int,
    ) -> list[StickerVectorQueryResult]:
        assert self.dimension is not None, "向量索引尚未初始化"
        assert self.document_storage is not None
        assert self.embedding_storage is not None
        if fetch_k <= 0:
            return []

        vector = np.array(
            self._coerce_vector(query_vector, self.dimension),
            dtype=np.float32,
        ).reshape(1, -1)
        self._normalize_matrix(vector)
        distances, indices = await self.embedding_storage.search(vector, fetch_k)
        raw_ids = [int(idx) for idx in indices[0].tolist() if int(idx) >= 0]
        if not raw_ids:
            return []

        docs = await self.document_storage.get_documents(
            metadata_filters={},
            ids=raw_ids,
            limit=len(raw_ids),
        )
        doc_map: dict[int, dict[str, Any]] = {
            int(doc["id"]): doc for doc in docs if doc.get("id") is not None
        }

        results: list[StickerVectorQueryResult] = []
        for distance, raw_id in zip(distances[0].tolist(), indices[0].tolist(), strict=False):
            db_id = int(raw_id)
            if db_id < 0:
                continue
            doc = doc_map.get(db_id)
            if doc is None:
                continue
            results.append(
                StickerVectorQueryResult(
                    sticker_id=str(doc.get("doc_id") or ""),
                    similarity=self.distance_to_similarity(float(distance)),
                    text=str(doc.get("text") or ""),
                    metadata=self._parse_metadata(doc.get("metadata")),
                    db_id=db_id,
                )
            )
        return results

    def get_status(self) -> dict[str, Any]:
        manifest = self.load_manifest()
        return {
            "schema_version": manifest.get("schema_version"),
            "embedding_provider_id": manifest.get("embedding_provider_id"),
            "embedding_dimension": manifest.get("embedding_dimension"),
            "index_built_at": manifest.get("index_built_at"),
            "indexed_count": int(manifest.get("indexed_count") or 0),
            "documents_path": str(self.documents_path),
            "faiss_path": str(self.faiss_path),
            "documents_exists": self.documents_path.exists(),
            "faiss_exists": self.faiss_path.exists(),
        }

    async def close(self) -> None:
        if self.document_storage is not None:
            await self.document_storage.close()
            self.document_storage = None
        self.embedding_storage = None
        self.dimension = None

    @staticmethod
    def distance_to_similarity(distance: float) -> float:
        similarity = 1.0 - (distance / 2.0)
        return max(-1.0, min(1.0, similarity))

    async def _refresh_manifest_counts(self) -> None:
        assert self.document_storage is not None
        self.save_manifest(
            indexed_count=await self.document_storage.count_documents(),
            index_built_at=self._now_iso(),
        )

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

    @staticmethod
    def _normalize_vector(vector: np.ndarray) -> None:
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm

    @classmethod
    def _normalize_matrix(cls, matrix: np.ndarray) -> None:
        if matrix.ndim == 1:
            cls._normalize_vector(matrix)
            return
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        matrix /= norms

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
            raise ValueError(
                f"向量维度不匹配，期望 {expected_dim}，实际 {len(vector)}"
            )
        return [float(value) for value in vector]

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()
