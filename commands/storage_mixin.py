"""
Matrix sticker storage mixin - 存储和基础命令
"""

import importlib
import math
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Reply
from astrbot.api.star import StarTools
from astrbot.core.provider.provider import EmbeddingProvider
from astrbot.core.utils.astrbot_path import get_astrbot_data_path

from ..vector_index import StickerVectorDocument, StickerVectorIndex
from ..vertex_multimodal_embedding import VertexMultimodalEmbeddingProvider


class StickerStorageMixin:
    """Sticker 基础功能：初始化、存储、查找、向量检索等"""

    _matrix_utils_cls = None
    _DEFAULT_VECTOR_TOP_K = 10
    _DEFAULT_VECTOR_FETCH_K = 50
    _DEFAULT_VECTOR_SIMILARITY_THRESHOLD = 0.35

    def _get_matrix_utils_cls(self):
        if self._matrix_utils_cls is not None:
            return self._matrix_utils_cls

        plugins_dir = Path(__file__).parent.parent.parent
        if str(plugins_dir) not in sys.path:
            sys.path.insert(0, str(plugins_dir))

        try:
            utils_module = importlib.import_module(
                "astrbot_plugin_matrix_adapter.utils"
            )
        except ImportError as e:
            logger.debug(f"无法导入 MatrixUtils：{e}")
            return None

        matrix_utils_cls = getattr(utils_module, "MatrixUtils", None)
        if matrix_utils_cls is None:
            logger.warning("astrbot_plugin_matrix_adapter.utils 中未找到 MatrixUtils")
            return None

        self._matrix_utils_cls = matrix_utils_cls
        return matrix_utils_cls

    def _init_sticker_module(self):
        """初始化 sticker 模块（从 matrix adapter 导入）"""
        try:
            plugins_dir = Path(__file__).parent.parent.parent
            if str(plugins_dir) not in sys.path:
                sys.path.insert(0, str(plugins_dir))

            sticker_module = importlib.import_module(
                "astrbot_plugin_matrix_adapter.sticker"
            )
            self._storage = sticker_module.StickerStorage()
            self._Sticker = sticker_module.Sticker
            self._StickerInfo = sticker_module.StickerInfo
            self._mark_vector_index_dirty()
            logger.info("Matrix Sticker 插件初始化成功")
        except ImportError as e:
            logger.warning(f"无法导入 matrix sticker 模块：{e}")
            logger.warning("请确保已安装 astrbot_plugin_matrix_adapter 插件")
            self._storage = None
            self._Sticker = None
            self._StickerInfo = None

    def _ensure_storage(self):
        """确保存储已初始化，并按节流策略刷新索引。"""
        if self._storage is None:
            self._init_sticker_module()
        if self._storage is not None:
            self._maybe_refresh_storage_index(force=False)
        return self._storage is not None

    def _iter_platform_instances(self):
        matrix_utils_cls = self._get_matrix_utils_cls()
        if matrix_utils_cls is None:
            return []
        try:
            return matrix_utils_cls.iter_platform_instances(self.context)
        except Exception as e:
            logger.debug(f"获取平台实例失败：{e}")
            return []

    def _get_storage_reload_interval_seconds(self) -> float:
        interval = getattr(self, "_storage_reload_interval_seconds", 3.0)
        try:
            return max(0.0, float(interval))
        except (TypeError, ValueError):
            return 3.0

    def _invalidate_sticker_lookup_cache(self) -> None:
        self._shortcode_lookup_cache = None
        self._last_storage_reload_monotonic = 0.0
        self._mark_vector_index_dirty()

    def _mark_vector_index_dirty(self) -> None:
        setattr(self, "_vector_index_dirty", True)

    def _clear_vector_index_dirty(self) -> None:
        setattr(self, "_vector_index_dirty", False)

    def _is_vector_index_dirty(self) -> bool:
        return bool(getattr(self, "_vector_index_dirty", True))

    def _maybe_refresh_storage_index(self, force: bool = False) -> bool:
        if self._storage is None:
            return False
        now = time.monotonic()
        last_reload = float(getattr(self, "_last_storage_reload_monotonic", 0.0))
        interval = self._get_storage_reload_interval_seconds()
        should_reload = force or interval <= 0.0 or (now - last_reload) >= interval
        if not should_reload:
            return False
        try:
            if hasattr(self._storage, "reload_index"):
                self._storage.reload_index()
            elif hasattr(self._storage, "_load_index"):
                self._storage._load_index()
        except Exception as e:
            logger.debug(f"刷新 sticker 索引失败：{e}")
            return False
        self._last_storage_reload_monotonic = now
        self._shortcode_lookup_cache = None
        self._mark_vector_index_dirty()
        return True

    def _build_shortcode_lookup_cache(self) -> dict[str, str]:
        if self._storage is None:
            return {}
        lookup: dict[str, str] = {}
        for meta in self._list_all_sticker_metas(max_limit=20000):
            sticker_id = getattr(meta, "sticker_id", "")
            body = str(getattr(meta, "body", "") or "").strip().lower()
            if body and sticker_id and body not in lookup:
                lookup[body] = sticker_id
            raw_tags = getattr(meta, "tags", None) or []
            for tag in raw_tags:
                tag_norm = str(tag or "").strip().lower()
                if tag_norm and sticker_id and tag_norm not in lookup:
                    lookup[tag_norm] = sticker_id
        return lookup

    def _list_all_sticker_metas(self, max_limit: int = 20000) -> list:
        if self._storage is None:
            return []
        iter_metas = getattr(self._storage, "iter_sticker_metas", None)
        if callable(iter_metas):
            try:
                metas = list(iter_metas())
                return metas[:max_limit]
            except Exception as e:
                logger.debug(f"遍历 sticker 元数据失败：{e}")
        limit = 5000
        try:
            stats = self._storage.get_stats()
            total_count = int(stats.get("total_count", 0))
            if total_count > 0:
                limit = max(limit, total_count)
        except Exception:
            pass
        limit = max(1, min(limit, max_limit))
        try:
            return self._storage.list_stickers(limit=limit)
        except Exception as e:
            logger.debug(f"读取 sticker 列表失败：{e}")
            return []

    def _get_sticker_meta(self, sticker_id: str):
        if self._storage is None:
            return None
        getter = getattr(self._storage, "get_sticker_meta", None)
        if callable(getter):
            try:
                return getter(sticker_id)
            except Exception as e:
                logger.debug(f"读取 sticker 元数据失败：{e}")
        for meta in self._list_all_sticker_metas(max_limit=20000):
            if str(getattr(meta, "sticker_id", "") or "") == str(sticker_id or ""):
                return meta
        return None

    def _get_storage_sticker(self, sticker_id: str, update_usage: bool = True):
        if self._storage is None:
            return None
        getter = getattr(self._storage, "get_sticker", None)
        if not callable(getter):
            return None
        try:
            return getter(sticker_id, update_usage=update_usage)
        except TypeError:
            return getter(sticker_id)

    def _mark_sticker_used(self, sticker) -> None:
        if self._storage is None:
            return
        sticker_id = getattr(sticker, "sticker_id", None)
        if not sticker_id:
            return
        touch_usage = getattr(self._storage, "touch_sticker_usage", None)
        if callable(touch_usage):
            touch_usage(str(sticker_id))
            self._mark_vector_index_dirty()
            return
        self._get_storage_sticker(str(sticker_id), update_usage=True)
        self._mark_vector_index_dirty()

    def _find_sticker_by_shortcode(self, shortcode: str):
        """根据短码查找 sticker（支持 body 和别名）"""
        if self._storage is None:
            return None

        shortcode_norm = str(shortcode or "").strip().lower()
        if not shortcode_norm:
            return None

        lookup = getattr(self, "_shortcode_lookup_cache", None)
        if lookup is None:
            lookup = self._build_shortcode_lookup_cache()
            self._shortcode_lookup_cache = lookup

        sticker_id = lookup.get(shortcode_norm)
        if sticker_id:
            sticker = self._get_storage_sticker(sticker_id, update_usage=False)
            if sticker is not None:
                return sticker
            lookup.pop(shortcode_norm, None)

        results = self._storage.find_stickers(query=shortcode, limit=10)
        for sticker in results:
            sticker_body = str(getattr(sticker, "body", "") or "").strip().lower()
            if sticker_body == shortcode_norm:
                matched_id = getattr(sticker, "sticker_id", None)
                if matched_id:
                    lookup[shortcode_norm] = matched_id
                return sticker
            sticker_tags = getattr(sticker, "tags", None) or []
            tags_norm = {str(tag or "").strip().lower() for tag in sticker_tags}
            if shortcode_norm in tags_norm:
                matched_id = getattr(sticker, "sticker_id", None)
                if matched_id:
                    lookup[shortcode_norm] = matched_id
                return sticker

        return None

    def _get_sticker_shortcodes(self) -> list[str]:
        """获取所有可用的 sticker 短码"""
        if self._storage is None:
            return []

        stickers = self._storage.list_stickers(limit=100)
        return [meta.body for meta in stickers]

    def _parse_bool_config(self, value: Any, default: bool = False) -> bool:
        parser = getattr(self, "_parse_bool_like", None)
        if callable(parser):
            return bool(parser(value, default))
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if raw in {"0", "false", "no", "off", "disable", "disabled"}:
            return False
        return default

    def _get_vector_config(self) -> dict[str, Any]:
        config = getattr(self, "config", None) or {}
        vector_config = config.get("matrix_sticker_vector", {})
        if isinstance(vector_config, dict):
            return vector_config
        return {}

    def _is_vector_search_enabled(self) -> bool:
        vector_config = self._get_vector_config()
        return self._parse_bool_config(vector_config.get("enabled", False), False)

    def _is_vector_auto_reconcile_enabled(self) -> bool:
        vector_config = self._get_vector_config()
        return self._parse_bool_config(
            vector_config.get("auto_reconcile", True),
            True,
        )

    def _is_vector_rebuild_on_startup_enabled(self) -> bool:
        vector_config = self._get_vector_config()
        return self._parse_bool_config(
            vector_config.get("rebuild_on_startup", False),
            False,
        )

    def _is_vector_query_image_enabled(self) -> bool:
        vector_config = self._get_vector_config()
        return self._parse_bool_config(
            vector_config.get("query_image_enabled", True),
            True,
        )

    def _get_vector_provider_id(self) -> str:
        vector_config = self._get_vector_config()
        model = str(
            vector_config.get("model", "multimodalembedding@001")
            or "multimodalembedding@001"
        ).strip()
        project = str(vector_config.get("vertex_project", "") or "").strip()
        location = str(
            vector_config.get("vertex_location", "us-central1") or "us-central1"
        ).strip()
        project_part = project or "adc"
        return f"plugin_vertex:{project_part}:{location}:{model}"

    def _build_vector_provider_config(self) -> dict[str, Any]:
        vector_config = self._get_vector_config()
        return {
            "id": self._get_vector_provider_id(),
            "type": "plugin_vertex_multimodal_embedding",
            "vertex_project": str(
                vector_config.get("vertex_project", "") or ""
            ).strip(),
            "vertex_location": str(
                vector_config.get("vertex_location", "us-central1") or "us-central1"
            ).strip(),
            "embedding_api_base": str(vector_config.get("api_base", "") or "").strip(),
            "embedding_model": str(
                vector_config.get("model", "multimodalembedding@001")
                or "multimodalembedding@001"
            ).strip(),
            "embedding_dimensions": int(vector_config.get("dimensions", 1408) or 1408),
            "timeout": int(vector_config.get("timeout", 20) or 20),
            "proxy": str(vector_config.get("proxy", "") or "").strip(),
        }

    def _get_vector_top_k(self) -> int:
        vector_config = self._get_vector_config()
        try:
            value = int(vector_config.get("top_k", self._DEFAULT_VECTOR_TOP_K))
        except (TypeError, ValueError):
            value = self._DEFAULT_VECTOR_TOP_K
        return max(1, min(value, 50))

    def _get_vector_fetch_k(self) -> int:
        vector_config = self._get_vector_config()
        try:
            value = int(vector_config.get("fetch_k", self._DEFAULT_VECTOR_FETCH_K))
        except (TypeError, ValueError):
            value = self._DEFAULT_VECTOR_FETCH_K
        return max(1, min(value, 500))

    def _get_vector_similarity_threshold(self) -> float:
        vector_config = self._get_vector_config()
        try:
            value = float(
                vector_config.get(
                    "similarity_threshold",
                    self._DEFAULT_VECTOR_SIMILARITY_THRESHOLD,
                )
            )
        except (TypeError, ValueError):
            value = self._DEFAULT_VECTOR_SIMILARITY_THRESHOLD
        return max(-1.0, min(value, 1.0))

    def _get_vector_index_base_dir(self) -> Path:
        try:
            data_dir = StarTools.get_data_dir("astrbot_plugin_matrix_sticker")
            return data_dir / "vector_index"
        except Exception:
            fallback_base = Path(get_astrbot_data_path()) / "plugin_data"
            return fallback_base / "astrbot_plugin_matrix_sticker" / "vector_index"

    def _get_vector_index(self) -> StickerVectorIndex:
        base_dir = self._get_vector_index_base_dir()
        current = getattr(self, "_vector_index", None)
        current_dir = getattr(self, "_vector_index_dir", None)
        if current is None or Path(str(current_dir or "")) != base_dir:
            current = StickerVectorIndex(base_dir)
            setattr(self, "_vector_index", current)
            setattr(self, "_vector_index_dir", base_dir)
        return current

    def _resolve_vector_provider(self) -> tuple[EmbeddingProvider | None, str | None]:
        if not self._is_vector_search_enabled():
            return None, "vector_disabled"
        provider_config = self._build_vector_provider_config()
        provider_cache_key = tuple(sorted(provider_config.items()))
        cached_provider = getattr(self, "_vector_provider", None)
        cached_key = getattr(self, "_vector_provider_cache_key", None)
        if cached_provider is None or cached_key != provider_cache_key:
            provider = VertexMultimodalEmbeddingProvider(provider_config, {})
            setattr(self, "_vector_provider", provider)
            setattr(self, "_vector_provider_cache_key", provider_cache_key)
        else:
            provider = cached_provider
        try:
            if not provider.supports_image_embedding():
                return None, "plugin_vertex_image_embedding_unsupported"
        except Exception as e:
            return None, f"plugin_vertex_provider_capability_error:{e}"
        try:
            dimension = int(provider.get_dim())
        except Exception as e:
            return None, f"plugin_vertex_provider_dimension_error:{e}"
        if dimension <= 0:
            return None, "plugin_vertex_provider_dimension_invalid"
        return provider, None

    async def _ensure_vector_index_open(
        self,
        provider: EmbeddingProvider,
    ) -> StickerVectorIndex:
        index = self._get_vector_index()
        await index.initialize(int(provider.get_dim()))
        return index

    def _should_force_vector_rebuild(self) -> bool:
        pending = getattr(self, "_vector_force_rebuild_pending", None)
        if pending is None:
            pending = self._is_vector_rebuild_on_startup_enabled()
            setattr(self, "_vector_force_rebuild_pending", pending)
        return bool(pending)

    def _clear_force_vector_rebuild(self) -> None:
        setattr(self, "_vector_force_rebuild_pending", False)

    def _build_sticker_vector_text(self, meta) -> str:
        body = str(getattr(meta, "body", "") or "").strip()
        pack_name = str(getattr(meta, "pack_name", "") or "").strip()
        tags = [
            str(tag).strip()
            for tag in (getattr(meta, "tags", None) or [])
            if str(tag).strip()
        ]
        parts = []
        if body:
            parts.append(f"shortcode: {body}")
        if pack_name:
            parts.append(f"pack: {pack_name}")
        if tags:
            parts.append(f"tags: {', '.join(tags)}")
        sticker_id = str(getattr(meta, "sticker_id", "") or "").strip()
        if sticker_id:
            parts.append(f"sticker_id: {sticker_id}")
        return "\n".join(parts) if parts else sticker_id

    def _get_meta_local_path(self, meta) -> str | None:
        local_path = str(getattr(meta, "local_path", "") or "").strip()
        if not local_path:
            return None
        path_obj = Path(local_path)
        if not path_obj.exists():
            return None
        return str(path_obj)

    def _build_meta_fingerprint(self, meta) -> str:
        if self._storage is not None:
            builder = getattr(self._storage, "build_meta_fingerprint", None)
            if callable(builder):
                try:
                    return str(builder(meta))
                except Exception as e:
                    logger.debug(f"构建 sticker 指纹失败，将使用回退方案：{e}")
        sticker_id = str(getattr(meta, "sticker_id", "") or "").strip()
        body = str(getattr(meta, "body", "") or "").strip()
        pack_name = str(getattr(meta, "pack_name", "") or "").strip()
        room_id = str(getattr(meta, "room_id", "") or "").strip()
        tags = [str(tag).strip() for tag in (getattr(meta, "tags", None) or [])]
        local_path = self._get_meta_local_path(meta)
        stat_sig = "missing"
        if local_path:
            try:
                stat = Path(local_path).stat()
                stat_sig = f"{int(stat.st_mtime_ns)}:{int(stat.st_size)}"
            except OSError:
                stat_sig = "stat_error"
        return "|".join(
            [
                sticker_id,
                body,
                pack_name,
                room_id,
                ",".join(sorted(tag for tag in tags if tag)),
                str(getattr(meta, "mxc_url", "") or ""),
                str(getattr(meta, "mimetype", "") or ""),
                stat_sig,
            ]
        )

    def _build_vector_metadata(self, meta, fingerprint: str) -> dict[str, Any]:
        return {
            "sticker_id": str(getattr(meta, "sticker_id", "") or ""),
            "body": str(getattr(meta, "body", "") or ""),
            "pack_name": getattr(meta, "pack_name", None),
            "room_id": getattr(meta, "room_id", None),
            "tags": list(getattr(meta, "tags", None) or []),
            "local_path": getattr(meta, "local_path", None),
            "mxc_url": getattr(meta, "mxc_url", None),
            "mimetype": getattr(meta, "mimetype", None),
            "width": getattr(meta, "width", None),
            "height": getattr(meta, "height", None),
            "created_at": getattr(meta, "created_at", None),
            "last_used": getattr(meta, "last_used", None),
            "use_count": getattr(meta, "use_count", None),
            "fingerprint": fingerprint,
        }

    @staticmethod
    def _normalize_vector(vector: list[float]) -> list[float]:
        if not vector:
            return []
        norm = math.sqrt(sum(float(value) * float(value) for value in vector))
        if norm <= 0:
            return [float(value) for value in vector]
        return [float(value) / norm for value in vector]

    def _fuse_vectors(self, *vectors: list[float] | None) -> list[float]:
        normalized = [
            self._normalize_vector(list(vector))
            for vector in vectors
            if vector is not None and len(vector) > 0
        ]
        if not normalized:
            raise ValueError("没有可融合的向量")
        if len(normalized) == 1:
            return normalized[0]
        dimension = len(normalized[0])
        fused = [0.0] * dimension
        for vector in normalized:
            if len(vector) != dimension:
                raise ValueError("待融合向量维度不一致")
            for idx, value in enumerate(vector):
                fused[idx] += float(value)
        fused = [value / len(normalized) for value in fused]
        return self._normalize_vector(fused)

    async def _build_vector_documents(
        self,
        provider: EmbeddingProvider,
        target_ids: set[str] | None = None,
    ) -> list[StickerVectorDocument]:
        metas = self._list_all_sticker_metas(max_limit=20000)
        if target_ids is not None:
            metas = [
                meta
                for meta in metas
                if str(getattr(meta, "sticker_id", "") or "") in target_ids
            ]
        prepared: list[tuple[Any, str, str, str, str]] = []
        for meta in metas:
            sticker_id = str(getattr(meta, "sticker_id", "") or "").strip()
            if not sticker_id:
                continue
            local_path = self._get_meta_local_path(meta)
            if not local_path:
                logger.debug(f"跳过缺少本地图片缓存的 sticker：{sticker_id}")
                continue
            text = self._build_sticker_vector_text(meta)
            fingerprint = self._build_meta_fingerprint(meta)
            prepared.append((meta, sticker_id, local_path, text, fingerprint))

        if not prepared:
            return []

        text_embeddings = await provider.get_embeddings([item[3] for item in prepared])
        image_embeddings = await provider.get_image_embeddings(
            [item[2] for item in prepared]
        )
        documents: list[StickerVectorDocument] = []
        for (
            meta,
            sticker_id,
            _local_path,
            text,
            fingerprint,
        ), text_vector, image_vector in zip(
            prepared,
            text_embeddings,
            image_embeddings,
            strict=False,
        ):
            documents.append(
                StickerVectorDocument(
                    sticker_id=sticker_id,
                    text=text,
                    metadata=self._build_vector_metadata(meta, fingerprint),
                    vector=self._fuse_vectors(text_vector, image_vector),
                )
            )
        return documents

    async def _reconcile_vector_index(
        self,
        *,
        force_full: bool = False,
    ) -> tuple[bool, str, dict[str, Any]]:
        provider, provider_reason = self._resolve_vector_provider()
        if provider is None:
            return False, provider_reason or "provider_unavailable", {}
        index = await self._ensure_vector_index_open(provider)
        provider_id = self._get_vector_provider_id()
        dimension = int(provider.get_dim())
        compatible, incompatibility_reason, manifest = index.check_compatibility(
            provider_id,
            dimension,
        )
        if force_full or self._should_force_vector_rebuild() or not compatible:
            entries = await self._build_vector_documents(provider)
            count = await index.rebuild_full(provider_id, dimension, entries)
            self._clear_force_vector_rebuild()
            self._clear_vector_index_dirty()
            return (
                True,
                f"rebuilt:{count}",
                {"indexed_count": count, **index.get_status()},
            )

        if not self._is_vector_index_dirty():
            return True, "up_to_date", {**manifest, **index.get_status()}

        docs = await index.list_documents()
        doc_map = {doc.sticker_id: doc for doc in docs}
        metas = self._list_all_sticker_metas(max_limit=20000)
        meta_map = {
            str(getattr(meta, "sticker_id", "") or ""): meta
            for meta in metas
            if str(getattr(meta, "sticker_id", "") or "")
        }

        existing_ids = set(doc_map)
        current_ids = set(meta_map)
        delete_ids = sorted(existing_ids - current_ids)
        changed_ids: set[str] = set()
        for sticker_id, meta in meta_map.items():
            fingerprint = self._build_meta_fingerprint(meta)
            current_doc = doc_map.get(sticker_id)
            current_fingerprint = None
            if current_doc is not None:
                current_fingerprint = current_doc.metadata.get("fingerprint")
            if current_doc is None or current_fingerprint != fingerprint:
                changed_ids.add(sticker_id)

        deleted = 0
        if delete_ids:
            deleted = await index.delete(delete_ids)

        updated = 0
        if changed_ids:
            entries = await self._build_vector_documents(
                provider, target_ids=changed_ids
            )
            built_ids = {entry.sticker_id for entry in entries}
            missing_ids = sorted(changed_ids - built_ids)
            if missing_ids:
                await index.delete(missing_ids)
                deleted += len(missing_ids)
            for entry in entries:
                await index.upsert(
                    entry.sticker_id,
                    entry.vector or [],
                    entry.text,
                    entry.metadata,
                )
                updated += 1

        self._clear_vector_index_dirty()
        status = index.get_status()
        status.update({"updated_count": updated, "deleted_count": deleted})
        return True, "reconciled", status

    async def _ensure_vector_search_state(
        self,
        *,
        force_reconcile: bool = False,
    ) -> tuple[EmbeddingProvider | None, StickerVectorIndex | None, str | None]:
        provider, provider_reason = self._resolve_vector_provider()
        if provider is None:
            return None, None, provider_reason
        index = await self._ensure_vector_index_open(provider)
        provider_id = self._get_vector_provider_id()
        dimension = int(provider.get_dim())
        compatible, incompatibility_reason, _manifest = index.check_compatibility(
            provider_id,
            dimension,
        )
        should_reconcile = force_reconcile or self._should_force_vector_rebuild()
        if not compatible:
            should_reconcile = True
        elif self._is_vector_auto_reconcile_enabled() and self._is_vector_index_dirty():
            should_reconcile = True
        if should_reconcile:
            ok, reconcile_reason, _status = await self._reconcile_vector_index(
                force_full=force_reconcile
                or not compatible
                or self._should_force_vector_rebuild()
            )
            if not ok:
                return None, None, reconcile_reason
            compatible, incompatibility_reason, _manifest = index.check_compatibility(
                provider_id,
                dimension,
            )
        if not compatible:
            return None, None, incompatibility_reason
        return provider, index, None

    async def _resolve_event_query_image_path(
        self,
        event: AstrMessageEvent,
    ) -> str | None:
        if not self._is_vector_query_image_enabled():
            return None
        message_obj = getattr(event, "message_obj", None)
        components = getattr(message_obj, "message", None) or []
        for component in components:
            if isinstance(component, Image):
                try:
                    return await component.convert_to_file_path()
                except Exception as e:
                    logger.debug(f"解析当前消息图片失败：{e}")
            if getattr(component, "type", None) == "Sticker":
                sticker_path = self._resolve_sticker_local_path(component)
                if sticker_path:
                    return sticker_path
                sticker_url = str(getattr(component, "url", "") or "")
                if sticker_url.startswith("mxc://"):
                    download_from_reply = getattr(
                        self, "_download_mxc_to_temp_file", None
                    )
                    if callable(download_from_reply):
                        try:
                            return await download_from_reply(
                                event,
                                sticker_url,
                                getattr(
                                    getattr(component, "info", None), "mimetype", None
                                )
                                or "image/png",
                            )
                        except Exception as e:
                            logger.debug(f"下载当前 sticker 图片失败：{e}")
        get_reply_image_file_path = getattr(self, "_get_reply_image_file_path", None)
        if callable(get_reply_image_file_path):
            try:
                return await get_reply_image_file_path(event)
            except Exception as e:
                logger.debug(f"解析引用图片失败：{e}")
        return None

    def _meta_matches_filters(
        self,
        meta,
        *,
        pack_name_norm: str,
        tag_filters: list[str],
        room_scope_norm: str,
        current_room_id: str,
    ) -> bool:
        pack = str(getattr(meta, "pack_name", "") or "")
        room_id = str(getattr(meta, "room_id", "") or "")
        meta_tags = [
            str(tag).strip().lower()
            for tag in (getattr(meta, "tags", None) or [])
            if str(tag).strip()
        ]
        if pack_name_norm and pack_name_norm not in pack.lower():
            return False
        if room_scope_norm == "room":
            if not room_id:
                return False
            if current_room_id and room_id != current_room_id:
                return False
        if room_scope_norm == "user" and room_id:
            return False
        if tag_filters and any(tag not in meta_tags for tag in tag_filters):
            return False
        return True

    def _build_meta_from_vector_metadata(self, metadata: dict[str, Any]):
        return SimpleNamespace(
            sticker_id=str(metadata.get("sticker_id") or ""),
            body=str(metadata.get("body") or ""),
            pack_name=metadata.get("pack_name"),
            room_id=metadata.get("room_id"),
            tags=list(metadata.get("tags") or []),
            local_path=metadata.get("local_path"),
            created_at=metadata.get("created_at"),
            last_used=metadata.get("last_used"),
            use_count=metadata.get("use_count") or 0,
            mxc_url=metadata.get("mxc_url"),
            mimetype=metadata.get("mimetype"),
            width=metadata.get("width"),
            height=metadata.get("height"),
        )

    def _format_tool_search_output(
        self,
        page: list[tuple[Any, float, list[str]]],
        *,
        total: int,
        offset: int,
        semantic: bool,
        image_query_used: bool,
    ) -> str:
        if not page:
            return f"No results at offset {offset}. Total matched: {total}."
        lines = [
            (
                f"Sticker search matched {total} item(s), "
                f"returning {len(page)} from offset {offset}."
            ),
            (
                f"search_mode={'semantic' if semantic else 'string'} "
                f"query_image={'yes' if image_query_used else 'no'}"
            ),
            "Use tool sticker_send with sticker_id to send one.",
        ]
        for idx, (meta, score, meta_tags) in enumerate(page, start=offset + 1):
            normalized_tags = [
                str(tag).strip() for tag in meta_tags if str(tag).strip()
            ]
            tags_text = ", ".join(normalized_tags[:8]) if normalized_tags else "-"
            file_path_text, file_exists = self._format_local_file_path(
                getattr(meta, "local_path", None)
            )
            sticker_id = str(getattr(meta, "sticker_id", "") or "-")
            body = str(getattr(meta, "body", "") or "")
            pack_name_text = str(getattr(meta, "pack_name", "") or "-")
            use_count = self._to_int(getattr(meta, "use_count", 0))
            lines.append(
                f"{idx}. id={sticker_id} shortcode=:{body}: "
                f"pack={pack_name_text} tags={tags_text} "
                f"used={use_count} "
                f"last={self._format_timestamp(getattr(meta, 'last_used', None))} "
                f"score={score:.4f} "
                f"file_path={file_path_text} "
                f"file_exists={'yes' if file_exists else 'no'}"
            )
        return "\n".join(lines)

    async def _search_stickers_semantic(
        self,
        event: AstrMessageEvent,
        *,
        keyword: str,
        pack_name_norm: str,
        tag_filters: list[str],
        room_scope_norm: str,
        current_room_id: str,
        limit: int,
        offset: int,
    ) -> tuple[str | None, list[tuple[Any, float, list[str]]], int, bool]:
        provider, index, reason = await self._ensure_vector_search_state(
            force_reconcile=False
        )
        if provider is None or index is None:
            return reason, [], 0, False
        keyword_norm = str(keyword or "").strip()
        image_query_path = await self._resolve_event_query_image_path(event)
        if not keyword_norm and not image_query_path:
            return "query_missing", [], 0, False

        text_vector = None
        image_vector = None
        if keyword_norm:
            text_vector = await provider.get_embedding(keyword_norm)
        if image_query_path:
            image_vector = await provider.get_image_embedding(image_query_path)
        query_vector = self._fuse_vectors(text_vector, image_vector)

        fetch_k = max(self._get_vector_fetch_k(), limit + offset)
        results = await index.search(query_vector, fetch_k)
        threshold = self._get_vector_similarity_threshold()
        filtered: list[tuple[Any, float, list[str]]] = []
        seen_ids: set[str] = set()
        for result in results:
            if result.similarity < threshold:
                continue
            sticker_id = str(result.sticker_id or "").strip()
            if not sticker_id or sticker_id in seen_ids:
                continue
            meta = self._get_sticker_meta(sticker_id)
            if meta is None:
                meta = self._build_meta_from_vector_metadata(result.metadata)
            if not self._meta_matches_filters(
                meta,
                pack_name_norm=pack_name_norm,
                tag_filters=tag_filters,
                room_scope_norm=room_scope_norm,
                current_room_id=current_room_id,
            ):
                continue
            meta_tags = [
                str(tag).strip()
                for tag in (getattr(meta, "tags", None) or [])
                if str(tag).strip()
            ]
            filtered.append((meta, float(result.similarity), meta_tags))
            seen_ids.add(sticker_id)
        return None, filtered, len(filtered), image_query_path is not None

    async def search_stickers_for_tool(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        pack_name: str = "",
        tags: str = "",
        limit: int = 10,
        offset: int = 0,
        sort_by: str = "relevance",
        match_mode: str = "fuzzy",
        include_alias: bool = True,
        room_scope: str = "all",
    ) -> str:
        if not self._ensure_storage():
            return "Sticker storage is not ready."

        try:
            limit = max(1, min(int(limit or 10), 50))
        except (TypeError, ValueError):
            limit = 10
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0
        sort_by_norm = str(sort_by or "relevance").strip().lower()
        match_mode_norm = str(match_mode or "fuzzy").strip().lower()
        room_scope_norm = str(room_scope or "all").strip().lower()
        keyword_norm = str(keyword or "").strip()
        pack_name_norm = str(pack_name or "").strip().lower()
        include_alias_flag = self._parse_bool_config(include_alias, True)
        current_room_id = str(event.get_session_id() or "").strip()
        tag_filters = [tag.lower() for tag in self._split_csv_items(tags)]

        valid_sort = {"relevance", "recent", "popular", "created", "name"}
        valid_match = {"fuzzy", "exact", "regex"}
        valid_scope = {"all", "room", "user"}
        if sort_by_norm not in valid_sort:
            return f"Invalid sort_by: {sort_by_norm}. Use one of: {', '.join(sorted(valid_sort))}."
        if match_mode_norm not in valid_match:
            return f"Invalid match_mode: {match_mode_norm}. Use one of: {', '.join(sorted(valid_match))}."
        if room_scope_norm not in valid_scope:
            return f"Invalid room_scope: {room_scope_norm}. Use one of: {', '.join(sorted(valid_scope))}."
        if room_scope_norm == "room" and not current_room_id:
            return "Current room context is unavailable; cannot apply room_scope=room."

        if sort_by_norm == "relevance":
            (
                semantic_error,
                semantic_scored,
                semantic_total,
                image_query_used,
            ) = await self._search_stickers_semantic(
                event,
                keyword=keyword_norm,
                pack_name_norm=pack_name_norm,
                tag_filters=tag_filters,
                room_scope_norm=room_scope_norm,
                current_room_id=current_room_id,
                limit=limit,
                offset=offset,
            )
            if semantic_scored:
                page = semantic_scored[offset : offset + limit]
                return self._format_tool_search_output(
                    page,
                    total=semantic_total,
                    offset=offset,
                    semantic=True,
                    image_query_used=image_query_used,
                )
            if semantic_total > 0:
                return self._format_tool_search_output(
                    [],
                    total=semantic_total,
                    offset=offset,
                    semantic=True,
                    image_query_used=image_query_used,
                )
            if semantic_error not in {None, "query_missing"}:
                logger.debug(f"Semantic sticker search unavailable: {semantic_error}")

        regex = None
        if keyword_norm and match_mode_norm == "regex":
            try:
                regex = re.compile(keyword_norm, re.IGNORECASE)
            except re.error as e:
                return f"Invalid regex pattern: {e}"

        metas = self._list_all_sticker_metas(max_limit=20000)
        if not metas:
            return "No stickers found in storage."

        scored: list[tuple[Any, float, list[str]]] = []
        keyword_lower = keyword_norm.lower()

        for meta in metas:
            body = str(getattr(meta, "body", "") or "")
            pack = str(getattr(meta, "pack_name", "") or "")
            room_id = getattr(meta, "room_id", None)
            raw_tags = getattr(meta, "tags", None) or []
            meta_tags = [str(tag) for tag in raw_tags if isinstance(tag, str) and tag]
            meta_tags_lower = [tag.lower() for tag in meta_tags]

            if pack_name_norm and pack_name_norm not in pack.lower():
                continue

            if room_scope_norm == "room":
                if not room_id:
                    continue
                if current_room_id and str(room_id) != current_room_id:
                    continue
            if room_scope_norm == "user" and room_id:
                continue

            if tag_filters and any(tag not in meta_tags_lower for tag in tag_filters):
                continue

            score = 0.0
            if keyword_norm:
                matched = False

                if match_mode_norm == "exact":
                    if body.lower() == keyword_lower:
                        score += 8.0
                        matched = True
                    if pack.lower() == keyword_lower:
                        score += 4.0
                        matched = True
                    if include_alias_flag and keyword_lower in meta_tags_lower:
                        score += 6.0
                        matched = True

                elif match_mode_norm == "regex":
                    fields = [body, pack]
                    if include_alias_flag:
                        fields.extend(meta_tags)
                    hits = sum(1 for field in fields if regex and regex.search(field))
                    if hits > 0:
                        score += float(hits * 2)
                        matched = True

                else:
                    if keyword_lower in body.lower():
                        score += 4.0
                        matched = True
                    if keyword_lower in pack.lower():
                        score += 2.0
                        matched = True
                    if include_alias_flag and any(
                        keyword_lower in tag for tag in meta_tags_lower
                    ):
                        score += 1.5
                        matched = True

                if not matched:
                    continue

            scored.append((meta, score, meta_tags))

        total = len(scored)
        if total == 0:
            return "No stickers matched the filters."

        if sort_by_norm == "recent":
            scored.sort(
                key=lambda item: (
                    self._to_float(getattr(item[0], "last_used", 0.0)),
                    self._to_float(getattr(item[0], "created_at", 0.0)),
                ),
                reverse=True,
            )
        elif sort_by_norm == "popular":
            scored.sort(
                key=lambda item: (
                    self._to_int(getattr(item[0], "use_count", 0)),
                    self._to_float(getattr(item[0], "last_used", 0.0)),
                ),
                reverse=True,
            )
        elif sort_by_norm == "created":
            scored.sort(
                key=lambda item: self._to_float(getattr(item[0], "created_at", 0.0)),
                reverse=True,
            )
        elif sort_by_norm == "name":
            scored.sort(
                key=lambda item: str(getattr(item[0], "body", "") or "").lower()
            )
        else:
            scored.sort(
                key=lambda item: (
                    item[1],
                    self._to_int(getattr(item[0], "use_count", 0)),
                    self._to_float(getattr(item[0], "last_used", 0.0)),
                ),
                reverse=True,
            )

        page = scored[offset : offset + limit]
        return self._format_tool_search_output(
            page,
            total=total,
            offset=offset,
            semantic=False,
            image_query_used=False,
        )

    async def _find_semantic_sticker_for_send(
        self,
        event: AstrMessageEvent,
        identifier: str,
    ):
        provider, index, reason = await self._ensure_vector_search_state(
            force_reconcile=False
        )
        if provider is None or index is None:
            logger.debug(f"Semantic sticker send fallback unavailable: {reason}")
            return None

        text_query = str(identifier or "").strip()
        image_query_path = await self._resolve_event_query_image_path(event)
        if not text_query and not image_query_path:
            return None

        text_vector = None
        image_vector = None
        if text_query:
            text_vector = await provider.get_embedding(text_query)
        if image_query_path:
            image_vector = await provider.get_image_embedding(image_query_path)
        query_vector = self._fuse_vectors(text_vector, image_vector)
        threshold = self._get_vector_similarity_threshold()
        results = await index.search(query_vector, max(1, self._get_vector_fetch_k()))
        for result in results:
            if result.similarity < threshold:
                continue
            sticker = self._get_storage_sticker(result.sticker_id, update_usage=False)
            if sticker is not None:
                return sticker
        return None

    async def _maybe_auto_reconcile_vector_index(self) -> None:
        if not self._is_vector_search_enabled():
            return
        if (
            not self._is_vector_auto_reconcile_enabled()
            and not self._should_force_vector_rebuild()
        ):
            return
        try:
            await self._reconcile_vector_index(
                force_full=self._should_force_vector_rebuild()
            )
        except Exception as e:
            logger.debug(f"自动同步 sticker 向量索引失败：{e}")

    async def cmd_list_stickers(self, pack_name: str | None = None) -> str:
        """列出 sticker"""
        stickers = self._storage.list_stickers(pack_name=pack_name, limit=20)

        if not stickers:
            if pack_name:
                return f"包 '{pack_name}' 中没有 sticker"
            return "没有保存的 sticker"

        lines = ["已保存的 sticker："]
        for meta in stickers:
            pack_info = f" [{meta.pack_name}]" if meta.pack_name else ""
            lines.append(f"  {meta.sticker_id[:8]}: {meta.body}{pack_info}")

        if len(stickers) == 20:
            lines.append("  ... (显示前 20 个)")

        return "\n".join(lines)

    def cmd_list_packs(self) -> str:
        """列出所有包"""
        packs = self._storage.list_packs()

        if not packs:
            return "没有 sticker 包"

        lines = ["Sticker 包列表："]
        for pack in packs:
            count = len(self._storage.list_stickers(pack_name=pack, limit=1000))
            lines.append(f"  {pack}: {count} 个 sticker")

        return "\n".join(lines)

    async def cmd_save_sticker(
        self, event: AstrMessageEvent, name: str, pack_name: str | None
    ) -> str:
        """保存 sticker"""
        sticker_to_save = None

        for component in event.message_obj.message:
            if hasattr(component, "type") and component.type == "Sticker":
                sticker_to_save = component
                break
            if isinstance(component, Reply):
                pass

        if sticker_to_save is None:
            for component in event.message_obj.message:
                if isinstance(component, Image):
                    try:
                        if self._Sticker is None or self._StickerInfo is None:
                            raise ImportError("Sticker 模块未加载")

                        sticker_to_save = self._Sticker(
                            body=name,
                            url=component.file or component.url,
                            info=self._StickerInfo(mimetype="image/png"),
                        )
                        break
                    except Exception as e:
                        logger.warning(f"转换图片为 sticker 失败：{e}")

        if sticker_to_save is None:
            return "未找到可保存的 sticker 或图片。请回复包含 sticker/图片 的消息，或发送包含图片的消息。"

        sticker_to_save.body = name
        if pack_name:
            sticker_to_save.pack_name = pack_name

        try:
            client = self._get_matrix_client(event)
            meta = await self._storage.save_sticker(
                sticker_to_save,
                client=client,
                pack_name=pack_name,
            )
            self._invalidate_sticker_lookup_cache()
            await self._maybe_auto_reconcile_vector_index()
            return f"已保存 sticker: {meta.sticker_id[:8]} ({name})"
        except Exception as e:
            logger.error(f"保存 sticker 失败：{e}")
            return f"保存失败：{e}"

    async def cmd_send_sticker(self, event: AstrMessageEvent, identifier: str):
        """发送 sticker"""
        sticker = self._get_storage_sticker(identifier, update_usage=True)
        usage_recorded = sticker is not None

        if sticker is None:
            sticker = self._find_sticker_by_shortcode(identifier)

        if sticker is None:
            try:
                results = self._storage.find_stickers(query=identifier, limit=1)
            except Exception as e:
                logger.debug(f"按关键词查找 sticker 失败：{e}")
                results = []
            if results:
                sticker = results[0]

        if sticker is None and self._is_vector_search_enabled():
            try:
                sticker = await self._find_semantic_sticker_for_send(event, identifier)
            except Exception as e:
                logger.debug(f"语义检索 sticker 失败：{e}")

        if sticker is None:
            return f"未找到 sticker: {identifier}"

        try:
            chain = MessageChain([sticker])
            await event.send(chain)
            if not usage_recorded:
                self._mark_sticker_used(sticker)
            return None
        except Exception as e:
            logger.error(f"发送 sticker 失败：{e}")
            return f"发送失败：{e}"

    async def cmd_delete_sticker(self, sticker_id: str) -> str:
        """删除 sticker"""
        if self._storage.delete_sticker(sticker_id):
            self._invalidate_sticker_lookup_cache()
            await self._maybe_auto_reconcile_vector_index()
            return f"已删除 sticker: {sticker_id}"
        return f"未找到 sticker: {sticker_id}"

    def _get_vector_status_snapshot(self) -> tuple[dict[str, Any], str | None]:
        provider, provider_reason = self._resolve_vector_provider()
        index = self._get_vector_index()
        status = index.get_status()
        status["enabled"] = self._is_vector_search_enabled()
        status["provider_id"] = self._get_vector_provider_id()
        status["dirty"] = self._is_vector_index_dirty()
        if provider is not None:
            try:
                compatible, incompatibility_reason, _manifest = (
                    index.check_compatibility(
                        self._get_vector_provider_id(),
                        int(provider.get_dim()),
                    )
                )
                status["compatible"] = compatible
                status["compatibility_reason"] = incompatibility_reason
                status["provider_dimension"] = int(provider.get_dim())
                status["provider_supports_image_embedding"] = bool(
                    provider.supports_image_embedding()
                )
            except Exception as e:
                status["compatible"] = False
                status["compatibility_reason"] = f"provider_reason:{e}"
        else:
            status["compatible"] = False
            status["compatibility_reason"] = provider_reason
        return status, provider_reason

    def cmd_get_stats(self) -> str:
        """获取统计信息"""
        stats = self._storage.get_stats()
        vector_status, _vector_error = self._get_vector_status_snapshot()

        lines = [
            "Sticker 统计信息：",
            f"  总数量：{stats['total_count']}",
            f"  占用空间：{stats['total_size_mb']} MB",
            f"  包数量：{stats['pack_count']}",
        ]

        if stats["packs"]:
            lines.append(f"  包列表：{', '.join(stats['packs'][:5])}")
            if len(stats["packs"]) > 5:
                lines.append(f"    ... 共 {len(stats['packs'])} 个包")

        lines.append("")
        lines.append("向量索引：")
        lines.append(f"  已启用：{'是' if vector_status.get('enabled') else '否'}")
        lines.append(f"  Provider：{vector_status.get('provider_id') or '-'}")
        lines.append(f"  兼容：{'是' if vector_status.get('compatible') else '否'}")
        lines.append(f"  已索引数量：{int(vector_status.get('indexed_count') or 0)}")
        lines.append(f"  向量维度：{vector_status.get('embedding_dimension') or '-'}")
        lines.append(f"  最近构建：{vector_status.get('index_built_at') or '-'}")
        lines.append(f"  脏状态：{'是' if vector_status.get('dirty') else '否'}")
        compatibility_reason = vector_status.get("compatibility_reason")
        if compatibility_reason:
            lines.append(f"  状态说明：{compatibility_reason}")
        return "\n".join(lines)

    async def cmd_search_stickers(
        self,
        event: AstrMessageEvent,
        keyword: str,
    ) -> str:
        """命令方式检索 sticker。"""
        return await self.search_stickers_for_tool(
            event,
            keyword=keyword,
            limit=self._get_vector_top_k(),
            offset=0,
            sort_by="relevance",
            match_mode="fuzzy",
            include_alias=True,
            room_scope="all",
        )

    async def cmd_reindex_stickers(self) -> str:
        """重建向量索引。"""
        if not self._ensure_storage():
            return "Sticker 模块未初始化"
        if not self._is_vector_search_enabled():
            return "向量搜索未启用，请先开启 matrix_sticker_vector.enabled。"
        try:
            ok, reason, status = await self._reconcile_vector_index(force_full=True)
        except Exception as e:
            logger.error(f"重建 sticker 向量索引失败：{e}")
            return f"重建失败：{e}"
        if not ok:
            return f"重建失败：{reason}"
        indexed_count = int(status.get("indexed_count") or 0)
        provider_id = (
            status.get("embedding_provider_id") or self._get_vector_provider_id()
        )
        return (
            f"已重建 sticker 向量索引\n"
            f"Provider: {provider_id}\n"
            f"Indexed: {indexed_count}\n"
            f"Built at: {status.get('index_built_at') or '-'}"
        )

    async def cmd_sync_room_stickers(self, event: AstrMessageEvent) -> str:
        """同步当前房间的 sticker 包"""
        try:
            room_id = str(event.get_session_id() or "").strip()
            if not room_id:
                return "无法获取当前房间 ID"

            syncer = self._get_matrix_syncer(event)

            if syncer is None:
                return "未找到 Matrix 适配器的 sticker 同步器"

            count = await syncer.sync_room_stickers(room_id, force=True)
            self._invalidate_sticker_lookup_cache()
            await self._maybe_auto_reconcile_vector_index()

            if count > 0:
                return f"成功同步 {count} 个 sticker（房间：{room_id[:20]}...）"
            else:
                packs = await syncer.get_room_sticker_packs(room_id)
                if packs:
                    pack_info = ", ".join(
                        f"{p.display_name} ({p.sticker_count})" for p in packs
                    )
                    return f"房间有 sticker 包但同步数为 0：{pack_info}"
                else:
                    return "该房间没有自定义 sticker 包（im.ponies.room_emotes）"

        except Exception as e:
            logger.error(f"同步房间 sticker 失败：{e}")
            return f"同步失败：{e}"

    def _get_matrix_syncer(self, event: AstrMessageEvent):
        matrix_utils_cls = self._get_matrix_utils_cls()
        if matrix_utils_cls is None:
            return None
        try:
            platform_id = str(event.get_platform_id() or "")
            platform = matrix_utils_cls.get_matrix_platform(self.context, platform_id)
            if platform is None:
                return None
            return getattr(platform, "sticker_syncer", None)
        except Exception as e:
            logger.debug(f"获取 Matrix sticker 同步器失败：{e}")
        return None

    def _get_matrix_client(self, event: AstrMessageEvent):
        """获取 Matrix 客户端"""
        matrix_utils_cls = self._get_matrix_utils_cls()
        if matrix_utils_cls is None:
            return None
        try:
            platform_id = str(event.get_platform_id() or "")
            return matrix_utils_cls.get_matrix_client(self.context, platform_id)
        except Exception as e:
            logger.debug(f"获取 Matrix 客户端失败：{e}")
        return None
