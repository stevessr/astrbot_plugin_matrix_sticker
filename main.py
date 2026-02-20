"""
Matrix Sticker 管理插件

提供 sticker 保存、列表、发送等管理命令
依赖 astrbot_plugin_matrix_adapter 的 sticker 模块
"""

import asyncio
import math
import re
import shlex
from datetime import datetime
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Reply
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
    version="1.0.1",
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
    _DEFAULT_STORAGE_RELOAD_INTERVAL_SECONDS = 3.0
    _MUTATING_STICKER_SUBCOMMANDS = {
        "save",
        "delete",
        "sync",
        "addroom",
        "removeroom",
        "mode",
    }
    _MUTATING_ALIAS_SUBCOMMANDS = {"add", "remove"}

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
        self._shortcode_lookup_cache: dict[str, str] | None = None
        self._last_storage_reload_monotonic = 0.0
        self._storage_reload_interval_seconds = (
            self._resolve_storage_reload_interval_seconds()
        )

        configure_emoji_shortcodes(
            enabled=self._is_emoji_shortcodes_enabled(),
            strict_mode=self._is_shortcode_strict_mode(),
        )
        warmup_emoji_shortcodes(fetch_remote=False)

        self._init_sticker_module()

    def _resolve_storage_reload_interval_seconds(self) -> float:
        raw_value = self.config.get(
            "matrix_sticker_index_reload_interval_seconds",
            self._DEFAULT_STORAGE_RELOAD_INTERVAL_SECONDS,
        )
        try:
            return max(0.0, float(raw_value))
        except (TypeError, ValueError):
            return self._DEFAULT_STORAGE_RELOAD_INTERVAL_SECONDS

    @staticmethod
    def _is_admin_event(event: AstrMessageEvent) -> bool:
        try:
            return bool(event.is_admin())
        except Exception:
            return False

    def _is_emoji_shortcodes_enabled(self) -> bool:
        if "emoji_shortcodes" in self.config:
            return self._parse_bool_like(self.config.get("emoji_shortcodes"), False)
        if "matrix_sticker_emoji_shortcodes" in self.config:
            return self._parse_bool_like(
                self.config.get("matrix_sticker_emoji_shortcodes"),
                False,
            )
        return self._parse_bool_like(
            self.config.get("matrix_emoji_shortcodes", False), False
        )

    def _is_shortcode_strict_mode(self) -> bool:
        if "emoji_shortcodes_strict_mode" in self.config:
            return self._parse_bool_like(
                self.config.get("emoji_shortcodes_strict_mode"),
                False,
            )
        if "matrix_sticker_shortcode_strict_mode" in self.config:
            return self._parse_bool_like(
                self.config.get("matrix_sticker_shortcode_strict_mode"),
                False,
            )
        return self._parse_bool_like(
            self.config.get("matrix_emoji_shortcodes_strict_mode", False),
            False,
        )

    def _is_sticker_auto_sync_enabled(self) -> bool:
        return self._parse_bool_like(
            self.config.get("matrix_sticker_auto_sync", False),
            False,
        )

    def _is_sticker_sync_user_emotes_enabled(self) -> bool:
        return self._parse_bool_like(
            self.config.get("matrix_sticker_sync_user_emotes", False),
            False,
        )

    def _iter_matrix_platforms(self):
        for platform in self._iter_platform_instances():
            if not hasattr(platform, "sticker_syncer") or not hasattr(
                platform, "client"
            ):
                continue
            client = getattr(platform, "client", None)
            if client is None:
                continue
            client_user_id = str(getattr(client, "user_id", "") or "")
            if not client_user_id.startswith("@") or ":" not in client_user_id:
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
        if isinstance(joined_rooms, dict):
            joined_rooms = joined_rooms.get("joined_rooms", [])
        if not isinstance(joined_rooms, (list, tuple, set)):
            logger.debug(
                f"Invalid joined rooms response for {platform_key}: {joined_rooms}"
            )
            return

        total_synced = 0
        for room_id in joined_rooms:
            try:
                normalized_room_id = str(room_id or "").strip()
                if not normalized_room_id:
                    continue
                total_synced += await syncer.sync_room_stickers(normalized_room_id)
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

    def _handle_startup_sync_task_done(self, task: asyncio.Task) -> None:
        if self._startup_sync_task is task:
            self._startup_sync_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Sticker startup sync task failed: {e}")

    def _ensure_startup_sync_task(self) -> None:
        if not self._is_sticker_auto_sync_enabled():
            if self._startup_sync_task and not self._startup_sync_task.done():
                self._startup_sync_task.cancel()
            self._startup_sync_task = None
            return
        if self._startup_sync_task and not self._startup_sync_task.done():
            return
        self._startup_sync_task = asyncio.create_task(
            self._startup_sync_when_ready(),
            name="matrix-sticker-startup-sync",
        )
        self._startup_sync_task.add_done_callback(self._handle_startup_sync_task_done)

    async def _auto_sync_loop(self) -> None:
        while True:
            try:
                await self._sync_all_platform_stickers_once()
            except Exception as e:
                logger.debug(f"Auto sync loop failed: {e}")
            await asyncio.sleep(self._AUTO_SYNC_INTERVAL_SECONDS)

    def _handle_auto_sync_task_done(self, task: asyncio.Task) -> None:
        if self._auto_sync_task is task:
            self._auto_sync_task = None
        try:
            task.result()
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Sticker auto-sync task failed: {e}")

    def _ensure_auto_sync_task(self) -> None:
        if not self._is_sticker_auto_sync_enabled():
            if self._auto_sync_task and not self._auto_sync_task.done():
                self._auto_sync_task.cancel()
            self._auto_sync_task = None
            return
        if self._auto_sync_task and not self._auto_sync_task.done():
            return
        self._auto_sync_task = asyncio.create_task(
            self._auto_sync_loop(),
            name="matrix-sticker-auto-sync",
        )
        self._auto_sync_task.add_done_callback(self._handle_auto_sync_task_done)

    def _save_runtime_config(self) -> None:
        save_config = getattr(self.config, "save_config", None)
        if callable(save_config):
            try:
                save_config()
            except Exception as e:
                logger.debug(f"Save sticker config failed: {e}")

    def _set_prompt_injection_runtime(self, mode: str, persist: bool = True) -> str:
        normalized = self._normalize_prompt_injection_mode(mode)
        enabled = normalized == "on"
        self.config["matrix_sticker_prompt_injection"] = enabled
        self.config.pop("matrix_sticker_llm_mode", None)
        self.config.pop("matrix_sticker_fc_mode", None)
        if persist:
            self._save_runtime_config()
        return normalized

    @staticmethod
    def _split_csv_items(value: str) -> list[str]:
        if not value:
            return []
        return [item.strip() for item in value.split(",") if item.strip()]

    @staticmethod
    def _split_command_args(message_text: str) -> list[str]:
        text = str(message_text or "").strip()
        if not text:
            return []
        try:
            return shlex.split(text)
        except ValueError:
            return text.split()

    @staticmethod
    def _parse_bool_like(value: Any, default: bool) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        raw = str(value).strip().lower()
        if raw in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if raw in {"0", "false", "no", "off", "disable", "disabled"}:
            return False
        return default

    @staticmethod
    def _format_timestamp(ts: float | None) -> str:
        timestamp = MatrixStickerPlugin._to_float(ts, default=0.0)
        if timestamp <= 0:
            return "-"
        try:
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
        except (OSError, OverflowError, ValueError):
            return "-"

    @staticmethod
    def _to_float(value: Any, default: float = 0.0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        if not math.isfinite(parsed):
            return default
        return parsed

    @classmethod
    def _to_int(cls, value: Any, default: int = 0) -> int:
        parsed = cls._to_float(value, default=float(default))
        return int(parsed)

    @staticmethod
    def _format_local_file_path(path_value: str | None) -> tuple[str, bool]:
        raw_path = str(path_value or "").strip()
        if not raw_path:
            return "-", False
        try:
            path_obj = Path(raw_path).expanduser()
            exists = path_obj.exists()
            if exists:
                return str(path_obj.resolve()), True
            return str(path_obj), False
        except (OSError, RuntimeError, ValueError):
            return raw_path, False

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

        args = self._split_command_args(event.message_str)
        if len(args) < 2:
            args.append("help")

        subcommand = args[1].lower() if len(args) > 1 else "help"

        if (
            subcommand in self._MUTATING_STICKER_SUBCOMMANDS
            and not self._is_admin_event(event)
        ):
            yield event.plain_result("权限不足：该子命令仅管理员可用。")
            return

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

        elif subcommand == "mode":
            if len(args) < 3:
                current = self._get_prompt_injection_mode()
                yield event.plain_result(
                    "当前 Sticker 提示词注入："
                    f"{current}\n"
                    "可选值：on | off\n"
                    "用法：/sticker mode <on|off>\n"
                    "说明：仅控制提示词注入；"
                    "sticker_search/sticker_send 工具默认启用，启停请在 WebUI 手动操作。"
                )
                return

            raw_mode = args[2].strip().lower()
            valid_inputs = {
                "on",
                "off",
                "enable",
                "enabled",
                "disable",
                "disabled",
                "true",
                "false",
                "1",
                "0",
                "yes",
                "no",
                "inject",
                "injection",
                "runtime",
                "prompt",
                "fc",
                "tool",
                "tools",
                "hybrid",
                "both",
            }
            if raw_mode not in valid_inputs:
                yield event.plain_result(
                    "无效参数。可选值：on | off\n用法：/sticker mode <on|off>"
                )
                return

            new_mode = self._set_prompt_injection_runtime(raw_mode, persist=True)
            yield event.plain_result(
                "已更新 Sticker 提示词注入："
                f"{new_mode}\n"
                "sticker_search/sticker_send 工具启停请在 WebUI 手动管理。"
            )

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

        args = self._split_command_args(event.message_str)
        if len(args) < 2:
            yield event.plain_result(self._get_alias_help_text())
            return

        subcommand = args[1].lower()

        if subcommand in self._MUTATING_ALIAS_SUBCOMMANDS and not self._is_admin_event(
            event
        ):
            yield event.plain_result("权限不足：该子命令仅管理员可用。")
            return

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

    @filter.llm_tool(name="sticker_search")
    async def tool_sticker_search(
        self,
        event: AstrMessageEvent,
        keyword: str = "",
        pack_name: str = "",
        tags: str = "",
        limit: int = 10,
        offset: int = 0,
        sort_by: str = "relevance",
        match_mode: str = "fuzzy",
        include_alias: bool = True,
        room_scope: str = "all",
    ) -> str:
        """Search stickers with advanced filters.

        Args:
            keyword(string): Search keyword for shortcode/body, pack, or aliases.
            pack_name(string): Optional pack filter. Supports fuzzy matching.
            tags(string): Optional comma-separated tag filters. All tags must match.
            limit(number): Maximum number of results to return. Range: 1-50.
            offset(number): Result offset for pagination. Must be >= 0.
            sort_by(string): Sort mode: relevance, recent, popular, created, name.
            match_mode(string): Match mode for keyword: fuzzy, exact, regex.
            include_alias(boolean): Whether aliases/tags are used for keyword matching.
            room_scope(string): Scope filter: all, room(current room only), user.
        """
        if not self._ensure_storage():
            return "Sticker storage is not ready."

        try:
            limit = max(1, min(int(limit or 10), 50))
        except (TypeError, ValueError):
            limit = 10
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0
        sort_by_norm = str(sort_by or "relevance").strip().lower()
        match_mode_norm = str(match_mode or "fuzzy").strip().lower()
        room_scope_norm = str(room_scope or "all").strip().lower()
        keyword_norm = str(keyword or "").strip()
        pack_name_norm = str(pack_name or "").strip().lower()
        include_alias_flag = self._parse_bool_like(include_alias, True)
        current_room_id = str(event.get_session_id() or "").strip()
        tag_filters = [tag.lower() for tag in self._split_csv_items(tags)]

        valid_sort = {"relevance", "recent", "popular", "created", "name"}
        valid_match = {"fuzzy", "exact", "regex"}
        valid_scope = {"all", "room", "user"}
        if sort_by_norm not in valid_sort:
            return f"Invalid sort_by: {sort_by_norm}. Use one of: {', '.join(sorted(valid_sort))}."
        if match_mode_norm not in valid_match:
            return f"Invalid match_mode: {match_mode_norm}. Use one of: {', '.join(sorted(valid_match))}."
        if room_scope_norm not in valid_scope:
            return f"Invalid room_scope: {room_scope_norm}. Use one of: {', '.join(sorted(valid_scope))}."
        if room_scope_norm == "room" and not current_room_id:
            return "Current room context is unavailable; cannot apply room_scope=room."

        regex = None
        if keyword_norm and match_mode_norm == "regex":
            try:
                regex = re.compile(keyword_norm, re.IGNORECASE)
            except re.error as e:
                return f"Invalid regex pattern: {e}"

        metas = self._list_all_sticker_metas(max_limit=20000)
        if not metas:
            return "No stickers found in storage."

        scored: list[tuple[Any, float, list[str]]] = []
        keyword_lower = keyword_norm.lower()

        for meta in metas:
            body = str(getattr(meta, "body", "") or "")
            pack = str(getattr(meta, "pack_name", "") or "")
            room_id = getattr(meta, "room_id", None)
            raw_tags = getattr(meta, "tags", None) or []
            meta_tags = [str(tag) for tag in raw_tags if isinstance(tag, str) and tag]
            meta_tags_lower = [tag.lower() for tag in meta_tags]

            if pack_name_norm and pack_name_norm not in pack.lower():
                continue

            if room_scope_norm == "room":
                if not room_id:
                    continue
                if current_room_id and str(room_id) != current_room_id:
                    continue
            if room_scope_norm == "user" and room_id:
                continue

            if tag_filters and any(tag not in meta_tags_lower for tag in tag_filters):
                continue

            score = 0.0
            if keyword_norm:
                matched = False

                if match_mode_norm == "exact":
                    if body.lower() == keyword_lower:
                        score += 8.0
                        matched = True
                    if pack.lower() == keyword_lower:
                        score += 4.0
                        matched = True
                    if include_alias_flag and keyword_lower in meta_tags_lower:
                        score += 6.0
                        matched = True

                elif match_mode_norm == "regex":
                    fields = [body, pack]
                    if include_alias_flag:
                        fields.extend(meta_tags)
                    hits = sum(1 for field in fields if regex and regex.search(field))
                    if hits > 0:
                        score += float(hits * 2)
                        matched = True

                else:
                    if keyword_lower in body.lower():
                        score += 4.0
                        matched = True
                    if keyword_lower in pack.lower():
                        score += 2.0
                        matched = True
                    if include_alias_flag and any(
                        keyword_lower in tag for tag in meta_tags_lower
                    ):
                        score += 1.5
                        matched = True

                if not matched:
                    continue

            scored.append((meta, score, meta_tags))

        total = len(scored)
        if total == 0:
            return "No stickers matched the filters."

        if sort_by_norm == "recent":
            scored.sort(
                key=lambda item: (
                    self._to_float(getattr(item[0], "last_used", 0.0)),
                    self._to_float(getattr(item[0], "created_at", 0.0)),
                ),
                reverse=True,
            )
        elif sort_by_norm == "popular":
            scored.sort(
                key=lambda item: (
                    self._to_int(getattr(item[0], "use_count", 0)),
                    self._to_float(getattr(item[0], "last_used", 0.0)),
                ),
                reverse=True,
            )
        elif sort_by_norm == "created":
            scored.sort(
                key=lambda item: self._to_float(getattr(item[0], "created_at", 0.0)),
                reverse=True,
            )
        elif sort_by_norm == "name":
            scored.sort(
                key=lambda item: str(getattr(item[0], "body", "") or "").lower()
            )
        else:
            scored.sort(
                key=lambda item: (
                    item[1],
                    self._to_int(getattr(item[0], "use_count", 0)),
                    self._to_float(getattr(item[0], "last_used", 0.0)),
                ),
                reverse=True,
            )

        page = scored[offset : offset + limit]
        if not page:
            return f"No results at offset {offset}. Total matched: {total}."

        lines = [
            (
                f"Sticker search matched {total} item(s), "
                f"returning {len(page)} from offset {offset}."
            ),
            "Use tool sticker_send with sticker_id to send one.",
        ]
        for idx, (meta, score, meta_tags) in enumerate(page, start=offset + 1):
            normalized_tags = [
                str(tag).strip() for tag in meta_tags if str(tag).strip()
            ]
            tags_text = ", ".join(normalized_tags[:8]) if normalized_tags else "-"
            file_path_text, file_exists = self._format_local_file_path(
                getattr(meta, "local_path", None)
            )
            sticker_id = str(getattr(meta, "sticker_id", "") or "-")
            body = str(getattr(meta, "body", "") or "")
            pack_name_text = str(getattr(meta, "pack_name", "") or "-")
            use_count = self._to_int(getattr(meta, "use_count", 0))
            lines.append(
                f"{idx}. id={sticker_id} shortcode=:{body}: "
                f"pack={pack_name_text} tags={tags_text} "
                f"used={use_count} "
                f"last={self._format_timestamp(getattr(meta, 'last_used', None))} "
                f"score={score:.2f} "
                f"file_path={file_path_text} "
                f"file_exists={'yes' if file_exists else 'no'}"
            )
        return "\n".join(lines)

    @filter.llm_tool(name="sticker_send")
    async def tool_sticker_send(
        self,
        event: AstrMessageEvent,
        sticker_id: str = "",
        shortcode: str = "",
        reply: bool = True,
    ) -> str:
        """Send a sticker by id or shortcode.

        Args:
            sticker_id(string): Exact sticker ID to send. Preferred when available.
            shortcode(string): Sticker shortcode/body or alias. Used when sticker_id is empty.
            reply(boolean): Whether to keep reply context to the current message when possible.
        """
        if not self._ensure_storage():
            return "Sticker storage is not ready."

        sticker_id_value = str(sticker_id or "").strip()
        shortcode_value = str(shortcode or "").strip()
        identifier = sticker_id_value or shortcode_value
        if not identifier:
            return "Please provide sticker_id or shortcode."

        sticker = None
        usage_recorded = False
        if sticker_id_value:
            sticker = self._get_storage_sticker(sticker_id_value, update_usage=True)
            usage_recorded = sticker is not None
        shortcode_candidates: list[str] = []
        if shortcode_value:
            shortcode_candidates.append(shortcode_value)
        elif identifier:
            shortcode_candidates.append(identifier)
        if sticker is None:
            for candidate in shortcode_candidates:
                sticker = self._find_sticker_by_shortcode(candidate)
                if sticker is not None:
                    identifier = candidate
                    break
        if sticker is None:
            query_candidates = [
                candidate
                for candidate in (sticker_id_value, shortcode_value, identifier)
                if candidate
            ]
            dedup_query_candidates: list[str] = []
            seen_queries: set[str] = set()
            for candidate in query_candidates:
                candidate_norm = candidate.strip().lower()
                if candidate_norm in seen_queries:
                    continue
                seen_queries.add(candidate_norm)
                dedup_query_candidates.append(candidate)
            for candidate in dedup_query_candidates:
                try:
                    results = self._storage.find_stickers(query=candidate, limit=1)
                except Exception as e:
                    logger.debug(f"Find sticker by query failed ({candidate}): {e}")
                    continue
                if results:
                    sticker = results[0]
                    identifier = candidate
                    break
        if sticker is None:
            return f"Sticker not found: {identifier}"

        chain_items = []
        reply_enabled = self._parse_bool_like(reply, True)
        if reply_enabled:
            reply_id = self._get_reply_event_id(event)
            if reply_id:
                chain_items.append(Reply(id=reply_id))

        platform_name = ""
        if hasattr(event, "get_platform_name"):
            platform_name = str(event.get_platform_name() or "").strip().lower()
        if platform_name == "matrix":
            chain_items.append(sticker)
        else:
            image = await self._build_image_component_from_sticker(sticker, event)
            if image is None:
                return (
                    "Failed to convert sticker to image component for this platform. "
                    "Try another sticker."
                )
            chain_items.append(image)

        await event.send(MessageChain(chain_items))
        if not usage_recorded:
            self._mark_sticker_used(sticker)
        sticker_id_out = getattr(sticker, "sticker_id", None) or "-"
        sticker_body_out = getattr(sticker, "body", None) or identifier
        return f"Sent sticker: :{sticker_body_out}: (id={sticker_id_out})"

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
        self._shortcode_lookup_cache = None
        self._last_storage_reload_monotonic = 0.0

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
