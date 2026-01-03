"""
Matrix sticker alias helpers.
"""

from astrbot.api.event import AstrMessageEvent, filter


class StickerAliasMixin:
    @filter.command("sticker_alias")
    async def sticker_alias_command(self, event: AstrMessageEvent):
        """
        Sticker 短码别名管理

        用法：
        - /sticker_alias add <sticker_id> <alias> - 添加别名
        - /sticker_alias remove <sticker_id> <alias> - 移除别名
        - /sticker_alias list <sticker_id> - 列出别名
        """
        if not self._ensure_storage():
            yield event.plain_result("Sticker 模块未初始化")
            return

        args = event.message_str.strip().split()
        if len(args) < 2:
            yield event.plain_result(self._get_alias_help_text())
            return

        subcommand = args[1].lower()

        if subcommand == "add":
            if len(args) < 4:
                yield event.plain_result(
                    "用法：/sticker_alias add <sticker_id> <alias>"
                )
                return
            sticker_id = args[2]
            alias = args[3]
            result = self._add_sticker_alias(sticker_id, alias)
            yield event.plain_result(result)

        elif subcommand == "remove":
            if len(args) < 4:
                yield event.plain_result(
                    "用法：/sticker_alias remove <sticker_id> <alias>"
                )
                return
            sticker_id = args[2]
            alias = args[3]
            result = self._remove_sticker_alias(sticker_id, alias)
            yield event.plain_result(result)

        elif subcommand == "list":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker_alias list <sticker_id>")
                return
            sticker_id = args[2]
            result = self._list_sticker_aliases(sticker_id)
            yield event.plain_result(result)

        else:
            yield event.plain_result(self._get_alias_help_text())

    def _get_alias_help_text(self) -> str:
        """获取别名管理帮助文本"""
        return """Sticker 短码别名管理

命令列表：
/sticker_alias add <sticker_id> <alias> - 为 sticker 添加别名短码
/sticker_alias remove <sticker_id> <alias> - 移除别名
/sticker_alias list <sticker_id> - 列出 sticker 的所有别名

说明：
- sticker_id 可以是完整 ID 或前 8 位
- 别名可以用作短码，如 :alias:
- 别名存储在 sticker 的 tags 字段中"""

    def _add_sticker_alias(self, sticker_id: str, alias: str) -> str:
        """为 sticker 添加别名"""
        sticker_meta = None
        for meta in self._storage.list_stickers(limit=1000):
            if meta.sticker_id.startswith(sticker_id):
                sticker_meta = meta
                break

        if sticker_meta is None:
            return f"未找到 sticker: {sticker_id}"

        if sticker_meta.tags is None:
            sticker_meta.tags = []

        if alias in sticker_meta.tags:
            return f"别名 '{alias}' 已存在"

        sticker_meta.tags.append(alias)
        self._storage._save_index()

        return f"已为 sticker {sticker_meta.sticker_id[:8]} 添加别名：{alias}"

    def _remove_sticker_alias(self, sticker_id: str, alias: str) -> str:
        """移除 sticker 别名"""
        sticker_meta = None
        for meta in self._storage.list_stickers(limit=1000):
            if meta.sticker_id.startswith(sticker_id):
                sticker_meta = meta
                break

        if sticker_meta is None:
            return f"未找到 sticker: {sticker_id}"

        if sticker_meta.tags is None or alias not in sticker_meta.tags:
            return f"别名 '{alias}' 不存在"

        sticker_meta.tags.remove(alias)
        self._storage._save_index()

        return f"已移除别名：{alias}"

    def _list_sticker_aliases(self, sticker_id: str) -> str:
        """列出 sticker 的所有别名"""
        sticker_meta = None
        for meta in self._storage.list_stickers(limit=1000):
            if meta.sticker_id.startswith(sticker_id):
                sticker_meta = meta
                break

        if sticker_meta is None:
            return f"未找到 sticker: {sticker_id}"

        if not sticker_meta.tags:
            return (
                f"sticker {sticker_meta.sticker_id[:8]} ({sticker_meta.body}) 没有别名"
            )

        aliases = ", ".join(sticker_meta.tags)
        return f"sticker {sticker_meta.sticker_id[:8]} ({sticker_meta.body}) 的别名：\n{aliases}"
