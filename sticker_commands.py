"""
Matrix sticker command handlers.
"""

from astrbot.api.event import AstrMessageEvent, filter


class StickerCommandMixin:
    @filter.command("sticker")
    async def sticker_command(self, event: AstrMessageEvent):
        """
        Sticker 管理命令

        用法：
        - /sticker help - 显示帮助
        - /sticker list [pack] - 列出所有 sticker 或指定包
        - /sticker packs - 列出所有包
        - /sticker save <name> [pack] - 保存引用的 sticker
        - /sticker send <id|name> - 发送指定的 sticker
        - /sticker delete <id> - 删除 sticker
        - /sticker stats - 显示统计信息
        """
        if not self._ensure_storage():
            yield event.plain_result(
                "Sticker 模块未初始化，请确保已安装 Matrix 适配器插件"
            )
            return

        args = event.message_str.strip().split()
        if len(args) < 2:
            args.append("help")

        subcommand = args[1].lower() if len(args) > 1 else "help"

        if subcommand == "help":
            yield event.plain_result(self._get_help_text())

        elif subcommand == "list":
            pack_name = args[2] if len(args) > 2 else None
            result = await self._list_stickers(pack_name)
            yield event.plain_result(result)

        elif subcommand == "packs":
            result = self._list_packs()
            yield event.plain_result(result)

        elif subcommand == "save":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker save <name> [pack]")
                return
            name = args[2]
            pack_name = args[3] if len(args) > 3 else None
            result = await self._save_sticker(event, name, pack_name)
            yield event.plain_result(result)

        elif subcommand == "send":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker send <id|name>")
                return
            identifier = args[2]
            result = await self._send_sticker(event, identifier)
            if isinstance(result, str):
                yield event.plain_result(result)

        elif subcommand == "delete":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker delete <id>")
                return
            sticker_id = args[2]
            result = self._delete_sticker(sticker_id)
            yield event.plain_result(result)

        elif subcommand == "stats":
            result = self._get_stats()
            yield event.plain_result(result)

        elif subcommand == "sync":
            result = await self._sync_room_stickers(event)
            yield event.plain_result(result)

        else:
            yield event.plain_result(
                f"未知子命令：{subcommand}\n" + self._get_help_text()
            )

    def _get_help_text(self) -> str:
        """获取帮助文本"""
        return """Matrix Sticker 管理

命令列表：
/sticker help - 显示此帮助
/sticker list [pack] - 列出 sticker（可选按包过滤）
/sticker packs - 列出所有 sticker 包
/sticker save <name> [pack] - 保存引用消息中的 sticker
/sticker send <id|name> - 发送指定的 sticker
/sticker delete <id> - 删除 sticker
/sticker stats - 显示统计信息
/sticker sync - 同步当前房间的 sticker 包

别名管理：
/sticker_alias add <id> <alias> - 添加别名短码
/sticker_alias remove <id> <alias> - 移除别名
/sticker_alias list <id> - 列出别名

提示：
- 回复一条包含 sticker 的消息并使用 /sticker save 来保存
- 使用 /sticker send 来发送已保存的 sticker
- 使用 /sticker sync 来同步房间的自定义 sticker
- LLM 会自动获知可用的 sticker 短码
- 在消息中使用 :shortcode: 格式会自动替换为 sticker"""
