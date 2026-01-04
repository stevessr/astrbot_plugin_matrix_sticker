"""
Matrix sticker room emote mixin
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent


class StickerRoomEmoteMixin:
    """房间自定义表情相关命令"""

    async def _get_image_mxc_from_reply(
        self, event: AstrMessageEvent
    ) -> tuple[str | None, str | None]:
        """从引用消息中获取图片 mxc URL 和 mimetype

        Returns:
            (mxc_url, mimetype) 或 (None, None)
        """
        try:
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

            client = self._get_matrix_client(event)
            if not client:
                return None, None

            reply_event = await client.get_event(room_id, reply_event_id)
            if not reply_event:
                return None, None

            reply_content = reply_event.get("content", {})
            msgtype = reply_content.get("msgtype", "")
            event_type = reply_event.get("type", "")

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
        """添加表情到当前房间"""
        if event.platform_meta.name != "matrix":
            return "此命令仅在 Matrix 平台可用"

        client = self._get_matrix_client(event)
        if not client:
            return "无法获取 Matrix 客户端"

        room_id = event.get_session_id()
        if not room_id:
            return "无法获取当前房间 ID"

        mxc_url, mimetype = await self._get_image_mxc_from_reply(event)
        if not mxc_url:
            return (
                "请引用一条包含图片或 sticker 的消息\n\n"
                "用法：\n"
                "1. 找到或发送一张图片\n"
                "2. 引用该图片消息\n"
                "3. 发送 /sticker addroom <短码>"
            )

        if not mxc_url.startswith("mxc://"):
            return f"无效的图片 URL：{mxc_url}"

        shortcode = shortcode.strip().strip(":")
        if not shortcode:
            return "请提供有效的短码名称"

        import re

        if not re.match(r"^[a-zA-Z0-9_-]+$", shortcode):
            return "短码只能包含字母、数字、下划线和连字符"

        try:
            room_emotes_type = "im.ponies.room_emotes"

            try:
                current_state = await client.get_room_state_event(
                    room_id, room_emotes_type, state_key
                )
            except Exception:
                current_state = {}

            images = current_state.get("images", {})
            if shortcode in images:
                return f"短码 :{shortcode}: 已存在于该房间"

            images[shortcode] = {
                "url": mxc_url,
                "info": {
                    "mimetype": mimetype,
                },
            }

            pack_info = current_state.get("pack", {})
            new_content = {
                "images": images,
            }
            if pack_info:
                new_content["pack"] = pack_info

            await client.set_room_state_event(
                room_id, room_emotes_type, new_content, state_key
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
        """从当前房间移除表情"""
        if event.platform_meta.name != "matrix":
            return "此命令仅在 Matrix 平台可用"

        client = self._get_matrix_client(event)
        if not client:
            return "无法获取 Matrix 客户端"

        room_id = event.get_session_id()
        if not room_id:
            return "无法获取当前房间 ID"

        shortcode = shortcode.strip().strip(":")
        if not shortcode:
            return "请提供要移除的短码名称"

        try:
            room_emotes_type = "im.ponies.room_emotes"

            try:
                current_state = await client.get_room_state_event(
                    room_id, room_emotes_type, state_key
                )
            except Exception:
                return "该房间没有自定义表情包"

            images = current_state.get("images", {})

            if shortcode not in images:
                return f"未找到表情 :{shortcode}:"

            del images[shortcode]

            new_content = {"images": images}
            pack_info = current_state.get("pack", {})
            if pack_info:
                new_content["pack"] = pack_info

            await client.set_room_state_event(
                room_id, room_emotes_type, new_content, state_key
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
        """列出当前房间的表情"""
        if event.platform_meta.name != "matrix":
            return "此命令仅在 Matrix 平台可用"

        client = self._get_matrix_client(event)
        if not client:
            return "无法获取 Matrix 客户端"

        room_id = event.get_session_id()
        if not room_id:
            return "无法获取当前房间 ID"

        try:
            room_emotes_type = "im.ponies.room_emotes"

            state = await client.get_room_state(room_id)

            emote_packs = []
            for ev in state:
                if ev.get("type") == room_emotes_type:
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
