"""
Matrix sticker manage mixin - sticker 管理命令逻辑
"""

from .base import StickerBaseMixin


class StickerManageMixin(StickerBaseMixin):
    """Sticker 管理命令逻辑"""

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
/sticker mode <on|off> - 开关 LLM 提示词注入（工具启停请在 WebUI 管理）

房间表情管理（Matrix 专用）：
/sticker addroom <shortcode> [pack] - 添加引用图片为房间表情（pack 为表情包名称）
/sticker removeroom <shortcode> [pack] - 移除房间表情
/sticker roomlist [pack] - 列出当前房间的自定义表情

别名管理：
/sticker_alias add <id> <alias> - 添加别名短码
/sticker_alias remove <id> <alias> - 移除别名
/sticker_alias list <id> - 列出别名

提示：
- 回复一条包含 sticker 的消息并使用 /sticker save 来保存
- 使用 /sticker send 来发送已保存的 sticker
- 使用 /sticker sync 来同步房间的自定义 sticker
- /sticker save|delete|sync|addroom|removeroom|mode 需要管理员权限
- 添加房间表情需要房间管理员权限
- LLM 会自动获知可用的 sticker 短码
- 在消息中使用 :shortcode: 格式会自动替换为 sticker"""
