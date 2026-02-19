"""
Matrix sticker alias mixin - 别名管理命令逻辑
"""

from .base import StickerBaseMixin


class StickerAliasMixin(StickerBaseMixin):
    """Sticker 别名管理命令逻辑"""

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
- add/remove 需要管理员权限
- 别名存储在 sticker 的 tags 字段中"""

    def cmd_add_alias(self, sticker_id: str, alias: str) -> str:
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
        self._invalidate_sticker_lookup_cache()

        return f"已为 sticker {sticker_meta.sticker_id[:8]} 添加别名：{alias}"

    def cmd_remove_alias(self, sticker_id: str, alias: str) -> str:
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
        self._invalidate_sticker_lookup_cache()

        return f"已移除别名：{alias}"

    def cmd_list_aliases(self, sticker_id: str) -> str:
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
