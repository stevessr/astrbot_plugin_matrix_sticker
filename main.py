"""
Matrix Sticker 管理插件

提供 sticker 保存、列表、发送等管理命令
依赖 astrbot_plugin_matrix_adapter 的 sticker 模块
"""

import asyncio

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, register
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from .commands import (
    StickerAliasMixin,
    StickerLLMMixin,
    StickerManageMixin,
)
from .emoji_shortcodes import configure_emoji_shortcodes, warmup_emoji_shortcodes


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

    _AUTO_SYNC_INTERVAL_SECONDS = 180

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self.config = config or {}
        self._storage = None
        self._Sticker = None
        self._StickerInfo = None
        self._auto_sync_task: asyncio.Task | None = None
        self._auto_sync_lock: asyncio.Lock | None = None
        self._startup_sync_task: asyncio.Task | None = None
        self._user_synced_platform_ids: set[str] = set()
        self._availability_reset_platform_ids: set[str] = set()

        configure_emoji_shortcodes(
            enabled=self._is_emoji_shortcodes_enabled(),
            strict_mode=self._is_shortcode_strict_mode(),
        )
        warmup_emoji_shortcodes(fetch_remote=False)

        self._init_sticker_module()

    def _is_emoji_shortcodes_enabled(self) -> bool:
        if "emoji_shortcodes" in self.config:
            return bool(self.config.get("emoji_shortcodes"))
        if "matrix_sticker_emoji_shortcodes" in self.config:
            return bool(self.config.get("matrix_sticker_emoji_shortcodes"))
        return bool(self.config.get("matrix_emoji_shortcodes", False))

    def _is_shortcode_strict_mode(self) -> bool:
        if "emoji_shortcodes_strict_mode" in self.config:
            return bool(self.config.get("emoji_shortcodes_strict_mode"))
        if "matrix_sticker_shortcode_strict_mode" in self.config:
            return bool(self.config.get("matrix_sticker_shortcode_strict_mode"))
        return bool(self.config.get("matrix_emoji_shortcodes_strict_mode", False))

    def _is_sticker_auto_sync_enabled(self) -> bool:
        return bool(self.config.get("matrix_sticker_auto_sync", False))

    def _is_sticker_sync_user_emotes_enabled(self) -> bool:
        return bool(self.config.get("matrix_sticker_sync_user_emotes", False))

    def _iter_matrix_platforms(self):
        for platform in self.context.platform_manager.get_insts():
            if not hasattr(platform, "sticker_syncer") or not hasattr(
                platform, "client"
            ):
                continue
            try:
                meta = platform.meta()
                if getattr(meta, "name", "") != "matrix":
                    continue
            except Exception:
                if not hasattr(platform, "_matrix_config"):
                    continue
            yield platform

    def _platform_sync_key(self, platform) -> str:
        return str(getattr(platform, "client_self_id", "") or id(platform))

    def _is_client_ready(self, client) -> bool:
        return bool(
            client
            and getattr(client, "user_id", None)
            and getattr(client, "access_token", None)
        )

    async def _sync_platform_stickers(self, platform) -> None:
        syncer = getattr(platform, "sticker_syncer", None)
        client = getattr(platform, "client", None)
        if not syncer or not client:
            return

        if not self._is_client_ready(client):
            return

        platform_key = self._platform_sync_key(platform)

        if platform_key not in self._availability_reset_platform_ids:
            try:
                if hasattr(syncer, "reset_available"):
                    syncer.reset_available()
                self._availability_reset_platform_ids.add(platform_key)
            except Exception as e:
                logger.debug(
                    f"Reset sticker availability failed for {platform_key}: {e}"
                )

        if (
            self._is_sticker_sync_user_emotes_enabled()
            and platform_key not in self._user_synced_platform_ids
        ):
            try:
                user_count = await syncer.sync_user_stickers()
                if user_count > 0:
                    logger.info(f"Synced {user_count} user stickers on {platform_key}")
                self._user_synced_platform_ids.add(platform_key)
            except Exception as e:
                logger.debug(f"Sync user stickers failed for {platform_key}: {e}")

        try:
            joined_rooms = await client.get_joined_rooms()
        except Exception as e:
            logger.debug(f"Load joined rooms failed for {platform_key}: {e}")
            return

        total_synced = 0
        for room_id in joined_rooms:
            try:
                total_synced += await syncer.sync_room_stickers(room_id)
            except Exception as room_e:
                logger.debug(f"Sync room stickers failed for {room_id}: {room_e}")

        if total_synced > 0:
            logger.info(f"Synced {total_synced} room stickers on {platform_key}")

    async def _sync_all_platform_stickers_once(self) -> None:
        if not self._is_sticker_auto_sync_enabled():
            return
        if self._auto_sync_lock is None:
            self._auto_sync_lock = asyncio.Lock()
        async with self._auto_sync_lock:
            for platform in self._iter_matrix_platforms():
                await self._sync_platform_stickers(platform)

    async def _startup_sync_when_ready(self) -> None:
        for _ in range(30):
            for platform in self._iter_matrix_platforms():
                client = getattr(platform, "client", None)
                if self._is_client_ready(client):
                    await self._sync_all_platform_stickers_once()
                    return
            await asyncio.sleep(1)

        await self._sync_all_platform_stickers_once()

    def _ensure_startup_sync_task(self) -> None:
        if not self._is_sticker_auto_sync_enabled():
            return
        if self._startup_sync_task and not self._startup_sync_task.done():
            return
        self._startup_sync_task = asyncio.create_task(
            self._startup_sync_when_ready(),
            name="matrix-sticker-startup-sync",
        )

    async def _auto_sync_loop(self) -> None:
        while True:
            try:
                await self._sync_all_platform_stickers_once()
            except Exception as e:
                logger.debug(f"Auto sync loop failed: {e}")
            await asyncio.sleep(self._AUTO_SYNC_INTERVAL_SECONDS)

    def _ensure_auto_sync_task(self) -> None:
        if not self._is_sticker_auto_sync_enabled():
            return
        if self._auto_sync_task and not self._auto_sync_task.done():
            return
        self._auto_sync_task = asyncio.create_task(
            self._auto_sync_loop(),
            name="matrix-sticker-auto-sync",
        )

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
                    "用法：/sticker addroom <shortcode> [pack]\n"
                    "请先引用一条包含图片的消息，然后发送此命令\n"
                    "pack 为可选的表情包名称"
                )
                return
            shortcode = args[2]
            state_key = args[3] if len(args) > 3 else ""
            result = await self.cmd_add_room_emote(event, shortcode, state_key)
            yield event.plain_result(result)

        elif subcommand == "removeroom":
            if len(args) < 3:
                yield event.plain_result("用法：/sticker removeroom <shortcode> [pack]")
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

    @filter.on_astrbot_loaded()
    async def on_astrbot_loaded(self):
        """Start auto-sync task when enabled."""
        self._ensure_auto_sync_task()

    @filter.on_platform_loaded()
    async def on_platform_loaded(self):
        """Run one startup sync pass after Matrix login is ready."""
        self._ensure_startup_sync_task()

    async def terminate(self):
        if self._startup_sync_task and not self._startup_sync_task.done():
            self._startup_sync_task.cancel()
            try:
                await self._startup_sync_task
            except asyncio.CancelledError:
                pass
        self._startup_sync_task = None

        if self._auto_sync_task and not self._auto_sync_task.done():
            self._auto_sync_task.cancel()
            try:
                await self._auto_sync_task
            except asyncio.CancelledError:
                pass
        self._auto_sync_task = None
