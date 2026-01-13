"""
Matrix Sticker 管理插件

提供 sticker 保存、列表、发送等管理命令
依赖 astrbot_plugin_matrix_adapter 的 sticker 模块
"""

from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from .commands import (
    StickerAliasMixin,
    StickerLLMMixin,
    StickerManageMixin,
)


@register(
    name="astrbot_plugin_matrix_sticker",
    desc="Matrix Sticker 管理插件，提供 sticker 保存、列表和发送命令",
    version="1.0.0",
    author="AstrBot",
)
class MatrixStickerPlugin(
    Star,
    StickerManageMixin,
    StickerAliasMixin,
    StickerLLMMixin,
):
    """Matrix Sticker 管理插件"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = config or {}
        self._storage = None
        self._Sticker = None
        self._StickerInfo = None
        self._init_sticker_module()

    # ========== Command Bindings ==========
    # 装饰器必须定义在 main.py 中，逻辑委托给 mixin

    @filter.command("sticker")
    async def sticker_command(self, event: AstrMessageEvent):
        """Sticker 管理命令"""
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
            result = await self.cmd_list_stickers(pack_name)
            yield event.plain_result(result)

        elif subcommand == "packs":
            result = self.cmd_list_packs()
            yield event.plain_result(result)

        elif subcommand == "save":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker save <name> [pack]")
                return
            name = args[2]
            pack_name = args[3] if len(args) > 3 else None
            result = await self.cmd_save_sticker(event, name, pack_name)
            yield event.plain_result(result)

        elif subcommand == "send":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker send <id|name>")
                return
            identifier = args[2]
            result = await self.cmd_send_sticker(event, identifier)
            if isinstance(result, str):
                yield event.plain_result(result)

        elif subcommand == "delete":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker delete <id>")
                return
            sticker_id = args[2]
            result = self.cmd_delete_sticker(sticker_id)
            yield event.plain_result(result)

        elif subcommand == "stats":
            result = self.cmd_get_stats()
            yield event.plain_result(result)

        elif subcommand == "sync":
            result = await self.cmd_sync_room_stickers(event)
            yield event.plain_result(result)

        elif subcommand == "addroom":
            if len(args) < 3:
                yield event.plain_result(
                    "用法：/sticker addroom <shortcode>\n"
                    "请先引用一条包含图片的消息，然后发送此命令"
                )
                return
            shortcode = args[2]
            state_key = args[3] if len(args) > 3 else ""
            result = await self.cmd_add_room_emote(event, shortcode, state_key)
            yield event.plain_result(result)

        elif subcommand == "removeroom":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker removeroom <shortcode>")
                return
            shortcode = args[2]
            state_key = args[3] if len(args) > 3 else ""
            result = await self.cmd_remove_room_emote(event, shortcode, state_key)
            yield event.plain_result(result)

        elif subcommand == "roomlist":
            state_key = args[2] if len(args) > 2 else ""
            result = await self.cmd_list_room_emotes(event, state_key)
            yield event.plain_result(result)

        else:
            yield event.plain_result(
                f"未知子命令：{subcommand}\n" + self._get_help_text()
            )

    @filter.command("sticker_alias")
    async def sticker_alias_command(self, event: AstrMessageEvent):
        """Sticker 短码别名管理"""
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
            result = self.cmd_add_alias(sticker_id, alias)
            yield event.plain_result(result)

        elif subcommand == "remove":
            if len(args) < 4:
                yield event.plain_result(
                    "用法：/sticker_alias remove <sticker_id> <alias>"
                )
                return
            sticker_id = args[2]
            alias = args[3]
            result = self.cmd_remove_alias(sticker_id, alias)
            yield event.plain_result(result)

        elif subcommand == "list":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker_alias list <sticker_id>")
                return
            sticker_id = args[2]
            result = self.cmd_list_aliases(sticker_id)
            yield event.plain_result(result)

        else:
            yield event.plain_result(self._get_alias_help_text())

    # ========== LLM Hooks ==========

    @filter.on_llm_response()
    async def on_llm_response(
        self, event: AstrMessageEvent, response: LLMResponse | None
    ):
        """缓存 LLM 响应"""
        self.hook_cache_llm_response(event, response)

    @filter.on_decorating_result()
    async def on_decorating_result(self, event: AstrMessageEvent):
        """替换短码为 sticker"""
        await self.hook_replace_shortcodes(event)

    @filter.on_llm_request()
    async def on_llm_request(self, event: AstrMessageEvent, req: ProviderRequest):
        """注入 sticker 短码到 LLM 提示词"""
        self.hook_inject_sticker_prompt(event, req)
