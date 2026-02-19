"""
Matrix sticker storage mixin - 存储和基础命令
"""

import importlib
import sys
import time
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Reply


class StickerStorageMixin:
    """Sticker 基础功能：初始化、存储、查找等"""

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
        platform_manager = getattr(self.context, "platform_manager", None)
        if platform_manager is None:
            return []
        get_insts = getattr(platform_manager, "get_insts", None)
        if callable(get_insts):
            try:
                return list(get_insts())
            except Exception:
                pass
        platforms = getattr(platform_manager, "platform_insts", None)
        if isinstance(platforms, list):
            return platforms
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
            return
        self._get_storage_sticker(str(sticker_id), update_usage=True)

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

    def cmd_delete_sticker(self, sticker_id: str) -> str:
        """删除 sticker"""
        if self._storage.delete_sticker(sticker_id):
            self._invalidate_sticker_lookup_cache()
            return f"已删除 sticker: {sticker_id}"
        return f"未找到 sticker: {sticker_id}"

    def cmd_get_stats(self) -> str:
        """获取统计信息"""
        stats = self._storage.get_stats()

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

        return "\n".join(lines)

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
        try:
            platform_id = str(event.get_platform_id() or "")
            fallback_syncer = None
            for platform in self._iter_platform_instances():
                syncer = getattr(platform, "sticker_syncer", None)
                if syncer is None:
                    continue
                try:
                    meta = platform.meta()
                except Exception:
                    meta = None
                meta_name = str(getattr(meta, "name", "") or "").strip().lower()
                if meta_name != "matrix":
                    continue
                if platform_id and str(getattr(meta, "id", "") or "") == platform_id:
                    return syncer
                if fallback_syncer is None:
                    fallback_syncer = syncer
            return fallback_syncer
        except Exception as e:
            logger.debug(f"获取 Matrix sticker 同步器失败：{e}")
        return None

    def _get_matrix_client(self, event: AstrMessageEvent):
        """获取 Matrix 客户端"""
        try:
            platform_id = str(event.get_platform_id() or "")
            fallback_client = None
            for platform in self._iter_platform_instances():
                if not hasattr(platform, "client"):
                    continue
                try:
                    meta = platform.meta()
                except Exception:
                    meta = None
                meta_name = str(getattr(meta, "name", "") or "").strip().lower()
                if meta_name != "matrix":
                    continue
                if platform_id and str(getattr(meta, "id", "") or "") == platform_id:
                    return platform.client
                if fallback_client is None:
                    fallback_client = platform.client
            return fallback_client
        except Exception as e:
            logger.debug(f"获取 Matrix 客户端失败：{e}")
        return None
