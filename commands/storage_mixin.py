"""
Matrix sticker storage mixin - 存储和基础命令
"""

import importlib
import sys
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
        """确保存储已初始化并刷新索引"""
        if self._storage is None:
            self._init_sticker_module()
        if self._storage is not None:
            if hasattr(self._storage, "reload_index"):
                self._storage.reload_index()
            elif hasattr(self._storage, "_load_index"):
                self._storage._load_index()
        return self._storage is not None

    def _find_sticker_by_shortcode(self, shortcode: str):
        """根据短码查找 sticker（支持 body 和别名）"""
        if self._storage is None:
            return None

        all_stickers = self._storage.list_stickers(limit=1000)

        for meta in all_stickers:
            if meta.body.lower() == shortcode.lower():
                return self._storage.get_sticker(meta.sticker_id)

        for meta in all_stickers:
            if meta.tags and shortcode.lower() in [t.lower() for t in meta.tags]:
                return self._storage.get_sticker(meta.sticker_id)

        results = self._storage.find_stickers(query=shortcode, limit=1)
        if results:
            return results[0]

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
            return f"已保存 sticker: {meta.sticker_id[:8]} ({name})"
        except Exception as e:
            logger.error(f"保存 sticker 失败：{e}")
            return f"保存失败：{e}"

    async def cmd_send_sticker(self, event: AstrMessageEvent, identifier: str):
        """发送 sticker"""
        sticker = self._storage.get_sticker(identifier)

        if sticker is None:
            results = self._storage.find_stickers(query=identifier, limit=1)
            if results:
                sticker = results[0]

        if sticker is None:
            return f"未找到 sticker: {identifier}"

        try:
            chain = MessageChain([sticker])
            await event.send(chain)
            return None
        except Exception as e:
            logger.error(f"发送 sticker 失败：{e}")
            return f"发送失败：{e}"

    def cmd_delete_sticker(self, sticker_id: str) -> str:
        """删除 sticker"""
        if self._storage.delete_sticker(sticker_id):
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
            room_id = event.session_id
            if not room_id:
                return "无法获取当前房间 ID"

            syncer = None
            for platform in self.context.platform_manager.get_insts():
                if hasattr(platform, "sticker_syncer"):
                    syncer = platform.sticker_syncer
                    break

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

    def _get_matrix_client(self, event: AstrMessageEvent):
        """获取 Matrix 客户端"""
        try:
            for platform in self.context.platform_manager.get_insts():
                if hasattr(platform, "client") and hasattr(platform, "_matrix_config"):
                    return platform.client
        except Exception as e:
            logger.debug(f"获取 Matrix 客户端失败：{e}")
        return None
