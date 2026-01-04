"""
Matrix sticker base mixin - 存储和辅助方法
"""

import importlib
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Reply


class StickerBaseMixin:
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
            client = None
            try:
                for platform in self.context.platform_manager.get_insts():
                    if hasattr(platform, "client") and hasattr(
                        platform, "_matrix_config"
                    ):
                        client = platform.client
                        break
            except Exception:
                pass

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

    async def _get_image_mxc_from_reply(self, event: AstrMessageEvent) -> tuple[str | None, str | None]:
        """从引用消息中获取图片 mxc URL 和 mimetype

        Returns:
            (mxc_url, mimetype) 或 (None, None)
        """
        try:
            # 获取原始消息中的引用信息
            room_id = event.get_session_id()
            reply_event_id = None

            raw_message = getattr(event, "message_obj", None)
            if raw_message:
                raw_event = getattr(raw_message, "raw_message", None)
                if raw_event:
                    content = getattr(raw_event, "content", None)
                    if content and isinstance(content, dict):
                        relates_to = content.get("m.relates_to", {})
                        in_reply_to = relates_to.get("m.in_reply_to", {})
                        reply_event_id = in_reply_to.get("event_id")

            if not reply_event_id:
                return None, None

            # 获取被引用的消息
            client = self._get_matrix_client(event)
            if not client:
                return None, None

            reply_event = await client.get_event(room_id, reply_event_id)
            if not reply_event:
                return None, None

            reply_content = reply_event.get("content", {})
            msgtype = reply_content.get("msgtype", "")
            event_type = reply_event.get("type", "")

            # 检查是否是图片或 sticker
            mxc_url = None
            mimetype = "image/png"

            if msgtype == "m.image":
                mxc_url = reply_content.get("url")
                info = reply_content.get("info", {})
                mimetype = info.get("mimetype", "image/png")
            elif msgtype == "m.sticker" or event_type == "m.sticker":
                mxc_url = reply_content.get("url")
                info = reply_content.get("info", {})
                mimetype = info.get("mimetype", "image/png")

            return mxc_url, mimetype

        except Exception as e:
            logger.debug(f"获取引用图片失败：{e}")
            return None, None

    async def cmd_add_room_emote(
        self, event: AstrMessageEvent, shortcode: str, state_key: str = ""
    ) -> str:
        """添加表情到当前房间

        Args:
            event: 消息事件（需要引用一条包含图片的消息）
            shortcode: 表情短码（不需要冒号）
            state_key: 可选，sticker 包的 state_key（默认为空字符串表示默认包）

        Returns:
            结果消息
        """
        # 检查平台
        if event.platform_meta.name != "matrix":
            return "此命令仅在 Matrix 平台可用"

        # 获取客户端
        client = self._get_matrix_client(event)
        if not client:
            return "无法获取 Matrix 客户端"

        room_id = event.get_session_id()
        if not room_id:
            return "无法获取当前房间 ID"

        # 从引用消息获取图片
        mxc_url, mimetype = await self._get_image_mxc_from_reply(event)
        if not mxc_url:
            return (
                "请引用一条包含图片或 sticker 的消息\n\n"
                "用法：\n"
                "1. 找到或发送一张图片\n"
                "2. 引用该图片消息\n"
                "3. 发送 /sticker addroom <短码>"
            )

        # 验证 mxc URL 格式
        if not mxc_url.startswith("mxc://"):
            return f"无效的图片 URL：{mxc_url}"

        # 清理短码（移除可能的冒号）
        shortcode = shortcode.strip().strip(":")
        if not shortcode:
            return "请提供有效的短码名称"

        # 验证短码格式（只允许字母数字下划线）
        import re
        if not re.match(r"^[a-zA-Z0-9_-]+$", shortcode):
            return "短码只能包含字母、数字、下划线和连字符"

        try:
            # 获取当前房间的 emotes 状态
            ROOM_EMOTES_TYPE = "im.ponies.room_emotes"

            try:
                current_state = await client.get_room_state_event(
                    room_id, ROOM_EMOTES_TYPE, state_key
                )
            except Exception:
                # 如果不存在，创建新的
                current_state = {}

            # 准备新的 images 字典
            images = current_state.get("images", {})

            # 检查短码是否已存在
            if shortcode in images:
                return f"短码 :{shortcode}: 已存在于该房间"

            # 添加新的 emote
            images[shortcode] = {
                "url": mxc_url,
                "info": {
                    "mimetype": mimetype,
                },
            }

            # 保留现有的 pack 信息，或创建新的
            pack_info = current_state.get("pack", {})

            # 构建新的状态内容
            new_content = {
                "images": images,
            }
            if pack_info:
                new_content["pack"] = pack_info

            # 更新房间状态
            await client.set_room_state_event(
                room_id, ROOM_EMOTES_TYPE, new_content, state_key
            )

            return (
                f"✅ 已添加表情 :{shortcode}: 到房间\n"
                f"URL: {mxc_url}\n"
                f"房间现有 {len(images)} 个自定义表情"
            )

        except Exception as e:
            logger.error(f"添加房间表情失败：{e}")
            if "forbidden" in str(e).lower() or "403" in str(e):
                return "❌ 权限不足：需要房间管理员权限才能添加自定义表情"
            return f"❌ 添加失败：{e}"

    async def cmd_remove_room_emote(
        self, event: AstrMessageEvent, shortcode: str, state_key: str = ""
    ) -> str:
        """从当前房间移除表情

        Args:
            event: 消息事件
            shortcode: 表情短码
            state_key: 可选，sticker 包的 state_key

        Returns:
            结果消息
        """
        # 检查平台
        if event.platform_meta.name != "matrix":
            return "此命令仅在 Matrix 平台可用"

        # 获取客户端
        client = self._get_matrix_client(event)
        if not client:
            return "无法获取 Matrix 客户端"

        room_id = event.get_session_id()
        if not room_id:
            return "无法获取当前房间 ID"

        # 清理短码
        shortcode = shortcode.strip().strip(":")
        if not shortcode:
            return "请提供要移除的短码名称"

        try:
            ROOM_EMOTES_TYPE = "im.ponies.room_emotes"

            # 获取当前状态
            try:
                current_state = await client.get_room_state_event(
                    room_id, ROOM_EMOTES_TYPE, state_key
                )
            except Exception:
                return "该房间没有自定义表情包"

            images = current_state.get("images", {})

            if shortcode not in images:
                return f"未找到表情 :{shortcode}:"

            # 移除表情
            del images[shortcode]

            # 构建新的状态内容
            new_content = {"images": images}
            pack_info = current_state.get("pack", {})
            if pack_info:
                new_content["pack"] = pack_info

            # 更新房间状态
            await client.set_room_state_event(
                room_id, ROOM_EMOTES_TYPE, new_content, state_key
            )

            return f"✅ 已从房间移除表情 :{shortcode}:"

        except Exception as e:
            logger.error(f"移除房间表情失败：{e}")
            if "forbidden" in str(e).lower() or "403" in str(e):
                return "❌ 权限不足：需要房间管理员权限才能移除自定义表情"
            return f"❌ 移除失败：{e}"

    async def cmd_list_room_emotes(
        self, event: AstrMessageEvent, state_key: str = ""
    ) -> str:
        """列出当前房间的表情

        Args:
            event: 消息事件
            state_key: 可选，sticker 包的 state_key

        Returns:
            表情列表
        """
        # 检查平台
        if event.platform_meta.name != "matrix":
            return "此命令仅在 Matrix 平台可用"

        # 获取客户端
        client = self._get_matrix_client(event)
        if not client:
            return "无法获取 Matrix 客户端"

        room_id = event.get_session_id()
        if not room_id:
            return "无法获取当前房间 ID"

        try:
            ROOM_EMOTES_TYPE = "im.ponies.room_emotes"

            # 获取所有 emote 包
            state = await client.get_room_state(room_id)

            emote_packs = []
            for ev in state:
                if ev.get("type") == ROOM_EMOTES_TYPE:
                    sk = ev.get("state_key", "")
                    content = ev.get("content", {})
                    images = content.get("images", {})
                    pack_info = content.get("pack", {})
                    display_name = pack_info.get("display_name", sk or "默认")
                    emote_packs.append((sk, display_name, images))

            if not emote_packs:
                return "该房间没有自定义表情"

            lines = ["**房间自定义表情**\n"]

            for sk, display_name, images in emote_packs:
                if len(emote_packs) > 1:
                    lines.append(f"**{display_name}** (state_key: {sk or '默认'}):")

                if not images:
                    lines.append("  (空)")
                else:
                    for shortcode in sorted(images.keys()):
                        lines.append(f"  :{shortcode}:")

                lines.append(f"  共 {len(images)} 个表情\n")

            return "\n".join(lines)

        except Exception as e:
            logger.error(f"列出房间表情失败：{e}")
            return f"❌ 获取失败：{e}"
