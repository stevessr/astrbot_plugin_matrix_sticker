"""
Matrix sticker LLM mixin - LLM 相关 hook 逻辑
"""

import hashlib
import re
from pathlib import Path
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from ..emoji_shortcodes import convert_emoji_shortcodes
from .base import StickerBaseMixin

STRICT_SHORTCODE_PATTERN = re.compile(r"(?<!\\)(?<![A-Za-z0-9_]):([A-Za-z0-9_+\-.]+):")
RELAXED_SHORTCODE_PATTERN = re.compile(
    r"(?<!\\)(?<![A-Za-z0-9_]):([A-Za-z0-9_+\-.]+):?(?=$|[^A-Za-z0-9_+\-.])"
)

STICKER_PROMPT_TEMPLATE = """
## 可用的表情贴纸

你可以在回复中使用以下表情贴纸短码，格式为 :短码:，系统会自动将其替换为对应的贴纸图片。

可用短码列表：
{sticker_list}

使用示例：
- 表达思考时可以用 :thinking:
- 根据语境选择合适的表情来增强表达效果
- 短码区分大小写，请使用准确的短码
"""


class StickerLLMMixin(StickerBaseMixin):
    """Sticker LLM hook 逻辑"""

    _DEFAULT_PROMPT_INJECTION_MODE = "on"
    _SUPPORTED_PROMPT_INJECTION_MODES = {"on", "off"}
    _PROMPT_INJECTION_MODE_ALIASES = {
        "on": "on",
        "enable": "on",
        "enabled": "on",
        "true": "on",
        "1": "on",
        "yes": "on",
        "inject": "on",
        "injection": "on",
        "runtime": "on",
        "prompt": "on",
        "hybrid": "on",
        "both": "on",
        "off": "off",
        "disable": "off",
        "disabled": "off",
        "false": "off",
        "0": "off",
        "no": "off",
        "fc": "off",
        "tool": "off",
        "tools": "off",
    }

    @staticmethod
    def _parse_bool_like_config(value: object, default: bool = False) -> bool:
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

    def _get_reply_event_id(self, event: AstrMessageEvent) -> str | None:
        message_obj = getattr(event, "message_obj", None)
        if not message_obj:
            return None
        reply_id = getattr(message_obj, "message_id", None)
        if not reply_id and hasattr(message_obj, "raw_message"):
            reply_id = getattr(message_obj.raw_message, "event_id", None)
        if reply_id:
            return str(reply_id)
        return None

    @staticmethod
    def _get_event_platform_name(event: AstrMessageEvent) -> str:
        if hasattr(event, "get_platform_name"):
            return str(event.get_platform_name() or "").strip().lower()
        return ""

    def _is_full_intercept_enabled(self) -> bool:
        config = getattr(self, "config", None) or {}
        return self._parse_bool_like_config(
            config.get("matrix_sticker_full_intercept", False),
            False,
        )

    def _is_shortcode_strict_mode(self) -> bool:
        config = getattr(self, "config", None) or {}
        if "emoji_shortcodes_strict_mode" in config:
            return self._parse_bool_like_config(
                config.get("emoji_shortcodes_strict_mode"),
                False,
            )
        if "matrix_sticker_shortcode_strict_mode" in config:
            return self._parse_bool_like_config(
                config.get("matrix_sticker_shortcode_strict_mode"),
                False,
            )
        return self._parse_bool_like_config(
            config.get("matrix_emoji_shortcodes_strict_mode", False),
            False,
        )

    def _is_emoji_shortcodes_enabled(self) -> bool:
        config = getattr(self, "config", None) or {}
        if "emoji_shortcodes" in config:
            return self._parse_bool_like_config(config.get("emoji_shortcodes"), False)
        if "matrix_sticker_emoji_shortcodes" in config:
            return self._parse_bool_like_config(
                config.get("matrix_sticker_emoji_shortcodes"),
                False,
            )
        return self._parse_bool_like_config(
            config.get("matrix_emoji_shortcodes", False), False
        )

    def _normalize_prompt_injection_mode(self, mode: str | None) -> str:
        raw = str(mode or "").strip().lower()
        normalized = self._PROMPT_INJECTION_MODE_ALIASES.get(raw, raw)
        if normalized in self._SUPPORTED_PROMPT_INJECTION_MODES:
            return normalized
        return self._DEFAULT_PROMPT_INJECTION_MODE

    def _get_prompt_injection_mode(self) -> str:
        config = getattr(self, "config", None) or {}
        prompt_injection = config.get("matrix_sticker_prompt_injection")
        if prompt_injection is None:
            return self._DEFAULT_PROMPT_INJECTION_MODE
        if isinstance(prompt_injection, bool):
            return "on" if prompt_injection else "off"
        return self._normalize_prompt_injection_mode(prompt_injection)

    def _is_runtime_injection_enabled(self) -> bool:
        return self._get_prompt_injection_mode() == "on"

    def _is_other_platforms_extension_enabled(self) -> bool:
        config = getattr(self, "config", None) or {}
        if "matrix_sticker_cross_platform" in config:
            return self._parse_bool_like_config(
                config.get("matrix_sticker_cross_platform"),
                False,
            )
        return self._parse_bool_like_config(
            config.get("matrix_sticker_enable_other_platforms", False),
            False,
        )

    def _get_shortcode_pattern(self) -> re.Pattern[str]:
        if self._is_shortcode_strict_mode():
            return STRICT_SHORTCODE_PATTERN
        return RELAXED_SHORTCODE_PATTERN

    def _resolve_shortcode_sticker_map(self, shortcodes: list[str]) -> dict[str, Any]:
        resolved: dict[str, Any] = {}
        for shortcode in shortcodes:
            shortcode_norm = str(shortcode or "").strip().lower()
            if not shortcode_norm or shortcode_norm in resolved:
                continue
            try:
                resolved[shortcode_norm] = self._find_sticker_by_shortcode(shortcode)
            except Exception as e:
                logger.debug(f"查找短码 '{shortcode_norm}' 失败：{e}")
                resolved[shortcode_norm] = None
        return resolved

    def _convert_emoji_shortcodes_in_chain(self, chain: list) -> tuple[list, bool]:
        if not self._is_emoji_shortcodes_enabled():
            return chain, False

        converted_chain = []
        modified = False
        for component in chain:
            if isinstance(component, Plain):
                source_text = component.text or ""
                converted_text = convert_emoji_shortcodes(source_text)
                if converted_text != source_text:
                    converted_chain.append(
                        Plain(
                            text=converted_text,
                            convert=getattr(component, "convert", True),
                        )
                    )
                    modified = True
                else:
                    converted_chain.append(component)
            else:
                converted_chain.append(component)
        return converted_chain, modified

    def _convert_emoji_shortcodes_in_result(self, result) -> None:
        converted_chain, emoji_modified = self._convert_emoji_shortcodes_in_chain(
            result.chain
        )
        if emoji_modified:
            result.chain = converted_chain
            logger.debug("已替换消息中的 emoji 短码")

    def _resolve_sticker_local_path(self, sticker) -> str | None:
        storage = getattr(self, "_storage", None)
        sticker_id = getattr(sticker, "sticker_id", None)
        if not storage or not sticker_id:
            return None

        index = getattr(storage, "_index", None)
        if not isinstance(index, dict):
            return None

        meta = index.get(sticker_id)
        local_path = getattr(meta, "local_path", None) if meta else None
        if local_path and Path(local_path).exists():
            return str(local_path)
        return None

    def _resolve_matrix_download_client(self, event: AstrMessageEvent | None = None):
        matrix_client_getter = getattr(self, "_get_matrix_client", None)
        if event is not None and callable(matrix_client_getter):
            try:
                client = matrix_client_getter(event)
                if client is not None and hasattr(client, "download_file"):
                    return client
            except Exception as e:
                logger.debug(f"Resolve matrix client from event failed: {e}")

        iter_platform_instances = getattr(self, "_iter_platform_instances", None)
        if not callable(iter_platform_instances):
            return None

        try:
            for platform in iter_platform_instances():
                client = getattr(platform, "client", None)
                if client is None or not hasattr(client, "download_file"):
                    continue
                client_user_id = str(getattr(client, "user_id", "") or "")
                if not client_user_id.startswith("@") or ":" not in client_user_id:
                    continue
                return client
        except Exception as e:
            logger.debug(f"Resolve fallback matrix client failed: {e}")
        return None

    @staticmethod
    def _build_telegram_sticker_cache_key(sticker) -> str | None:
        sticker_id = str(getattr(sticker, "sticker_id", "") or "").strip()
        if sticker_id:
            stable_id = sticker_id
        else:
            fallback_source = str(getattr(sticker, "url", "") or "").strip()
            if not fallback_source:
                fallback_source = str(getattr(sticker, "body", "") or "").strip()
            if not fallback_source:
                return None
            stable_id = hashlib.sha256(fallback_source.encode("utf-8")).hexdigest()
        return f"matrix_sticker:tg:image:{stable_id}"

    @staticmethod
    def _attach_telegram_file_unique(
        image: Image,
        sticker,
        event: AstrMessageEvent | None,
    ) -> None:
        if event is None:
            return
        platform_name = str(getattr(event, "get_platform_name", lambda: "")() or "")
        if platform_name.strip().lower() != "telegram":
            return
        file_unique = StickerLLMMixin._build_telegram_sticker_cache_key(sticker)
        if file_unique:
            image.file_unique = file_unique

    async def _build_image_component_from_sticker(
        self,
        sticker,
        event: AstrMessageEvent | None = None,
    ) -> Image | None:
        try:
            local_path = self._resolve_sticker_local_path(sticker)
            if local_path:
                image = Image.fromFileSystem(local_path)
                self._attach_telegram_file_unique(image, sticker, event)
                return image

            sticker_url = str(getattr(sticker, "url", "") or "")
            if not sticker_url:
                return None

            image: Image | None = None
            if sticker_url.startswith("mxc://"):
                matrix_client = self._resolve_matrix_download_client(event)
                if matrix_client is None:
                    logger.debug(
                        f"Skip mxc sticker without matrix client: {sticker_url}"
                    )
                    return None
                try:
                    try:
                        image_bytes = await matrix_client.download_file(
                            sticker_url, allow_thumbnail_fallback=True
                        )
                    except TypeError:
                        image_bytes = await matrix_client.download_file(sticker_url)
                    if image_bytes:
                        image = Image.fromBytes(bytes(image_bytes))
                except Exception as e:
                    logger.debug(f"Download mxc sticker failed: {e}")
                    return None
                if image is None:
                    return None
            elif sticker_url.startswith("http://") or sticker_url.startswith("https://"):
                image = Image.fromURL(sticker_url)
            elif sticker_url.startswith("file:///") or sticker_url.startswith(
                "base64://"
            ):
                image = Image(file=sticker_url)
            elif Path(sticker_url).exists():
                image = Image.fromFileSystem(sticker_url)
            else:
                image = Image(file=sticker_url)

            self._attach_telegram_file_unique(image, sticker, event)
            return image
        except Exception as e:
            logger.debug(f"Convert sticker to image failed: {e}")
            return None

    def _get_max_stickers_per_reply(self) -> int | None:
        config = getattr(self, "config", None) or {}
        value = config.get("matrix_sticker_max_per_reply", 5)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 5
        if value <= 0:
            return None
        return value

    def _get_prompt_sticker_limit(self) -> int:
        config = getattr(self, "config", None) or {}
        value = config.get("matrix_sticker_prompt_limit", 50)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return 50
        return max(1, value)

    def hook_cache_llm_response(
        self, event: AstrMessageEvent, response: LLMResponse | None
    ):
        """Cache LLM completion text for streaming finish hooks."""
        if not response:
            return
        completion_text = response.completion_text
        if completion_text:
            event.set_extra("_sticker_llm_completion", completion_text)

    async def hook_replace_shortcodes(self, event: AstrMessageEvent):
        """Replace :shortcode: with sticker components."""
        result = event.get_result()
        if result is None or not result.chain:
            logger.debug("没有消息结果或消息链为空")
            return

        if not self._is_runtime_injection_enabled():
            self._convert_emoji_shortcodes_in_result(result)
            return

        platform_name = self._get_event_platform_name(event)
        is_matrix_platform = platform_name == "matrix"
        extension_enabled = self._is_other_platforms_extension_enabled()

        if not is_matrix_platform and not extension_enabled:
            self._convert_emoji_shortcodes_in_result(result)
            return

        if not self._ensure_storage():
            logger.debug("Sticker storage 未初始化，跳过 sticker 短码替换")
            self._convert_emoji_shortcodes_in_result(result)
            return

        result_type = getattr(result, "result_content_type", None)
        is_streaming = result_type == ResultContentType.STREAMING_FINISH
        logger.debug(
            f"处理消息，result_content_type={result_type}, is_streaming={is_streaming}"
        )

        full_text = ""
        for component in result.chain:
            if isinstance(component, Plain):
                full_text += component.text

        if not full_text:
            cached_text = event.get_extra("_sticker_llm_completion", "")
            if cached_text:
                result.chain = [Plain(cached_text)]
                full_text = cached_text

        shortcode_pattern = self._get_shortcode_pattern()
        all_matches = list(shortcode_pattern.finditer(full_text))
        resolved_shortcodes = self._resolve_shortcode_sticker_map(
            [match.group(1) for match in all_matches]
        )
        logger.debug(
            f"在文本中找到 {len(all_matches)} 个短码匹配：{[m.group(1) for m in all_matches]}"
        )

        if is_matrix_platform and self._is_full_intercept_enabled() and all_matches:
            missing_shortcodes = []
            for match in all_matches:
                shortcode = match.group(1)
                shortcode_norm = shortcode.strip().lower()
                if not resolved_shortcodes.get(shortcode_norm):
                    missing_shortcodes.append(shortcode)
            if not missing_shortcodes:
                await self._send_split_messages(
                    event,
                    full_text,
                    is_streaming,
                    resolved_shortcodes,
                )
                if result:
                    result.chain = []
                event.set_extra("_streaming_finished", True)
                return
            logger.debug(f"存在未匹配短码，跳过分段发送：{missing_shortcodes}")

        max_stickers = self._get_max_stickers_per_reply()
        found_stickers: dict[str, Any] = {}
        marked_usage_ids: set[str] = set()
        new_chain = []
        modified = False

        for component in result.chain:
            if isinstance(component, Plain):
                text = component.text
                matches = list(shortcode_pattern.finditer(text))

                if not matches:
                    new_chain.append(component)
                    continue

                last_end = 0
                component_modified = False
                for match in matches:
                    shortcode = match.group(1)
                    shortcode_norm = shortcode.strip().lower()

                    sticker = resolved_shortcodes.get(shortcode_norm)
                    logger.debug(
                        f"查找短码 '{shortcode}': {'找到' if sticker else '未找到'}"
                    )

                    if not sticker:
                        continue

                    sticker_id = getattr(sticker, "sticker_id", None) or sticker.body
                    replacement_component = sticker
                    if not is_matrix_platform:
                        replacement_component = (
                            await self._build_image_component_from_sticker(
                                sticker,
                                event,
                            )
                        )
                        if replacement_component is None:
                            logger.debug(
                                f"无法将短码 '{shortcode}' 对应 sticker 转为图片组件"
                            )
                            continue

                    if match.start() > last_end:
                        before_text = text[last_end : match.start()]
                        if before_text:
                            new_chain.append(Plain(before_text))

                    within_limit = (
                        max_stickers is None
                        or sticker_id in found_stickers
                        or len(found_stickers) < max_stickers
                    )

                    if within_limit:
                        if is_streaming and is_matrix_platform:
                            if sticker_id not in found_stickers:
                                found_stickers[sticker_id] = sticker
                        else:
                            if sticker_id not in found_stickers:
                                new_chain.append(replacement_component)
                                found_stickers[sticker_id] = replacement_component
                                usage_id = str(
                                    getattr(sticker, "sticker_id", "") or sticker_id
                                )
                                if usage_id and usage_id not in marked_usage_ids:
                                    self._mark_sticker_used(sticker)
                                    marked_usage_ids.add(usage_id)
                        modified = True
                        component_modified = True
                    else:
                        new_chain.append(Plain(text[match.start() : match.end()]))
                        component_modified = True

                    last_end = match.end()

                if last_end < len(text):
                    remaining_text = text[last_end:]
                    if remaining_text:
                        new_chain.append(Plain(remaining_text))
                        component_modified = True

                if not component_modified:
                    new_chain.append(component)
            else:
                new_chain.append(component)

        logger.debug(
            f"处理完成：modified={modified}, found_stickers={len(found_stickers)}"
        )

        if modified:
            if is_matrix_platform and is_streaming and found_stickers:
                unique_stickers = list(found_stickers.values())
                reply_id = self._get_reply_event_id(event)
                logger.info(
                    f"流式输出完成，发送 {len(unique_stickers)} 个去重后的 sticker"
                )
                for i, sticker in enumerate(unique_stickers):
                    try:
                        logger.info(
                            f"发送 sticker {i + 1}/{len(unique_stickers)}: {sticker.body if hasattr(sticker, 'body') else sticker}"
                        )
                        chain_comps = []
                        if reply_id:
                            chain_comps.append(Reply(id=reply_id))
                        chain_comps.append(sticker)
                        chain = MessageChain(chain_comps)
                        logger.info(f"创建 MessageChain: {chain}")
                        send_result = await event.send(chain)
                        logger.info(f"发送结果：{send_result}")
                        self._mark_sticker_used(sticker)
                    except Exception as e:
                        logger.error(f"发送 sticker 失败：{e}", exc_info=True)
            else:
                result.chain = new_chain
                logger.debug("已替换消息中的 sticker 短码")

        self._convert_emoji_shortcodes_in_result(result)

    async def _send_split_messages(
        self,
        event: AstrMessageEvent,
        full_text: str,
        is_streaming: bool,
        resolved_shortcodes: dict[str, Any] | None = None,
    ) -> None:
        max_stickers = self._get_max_stickers_per_reply()
        found_stickers: dict[str, Any] = {}
        segments: list[Plain | Any] = []
        reply_id = self._get_reply_event_id(event)

        last_end = 0
        shortcode_pattern = self._get_shortcode_pattern()
        if resolved_shortcodes is None:
            resolved_shortcodes = self._resolve_shortcode_sticker_map(
                [match.group(1) for match in shortcode_pattern.finditer(full_text)]
            )
        for match in shortcode_pattern.finditer(full_text):
            if match.start() > last_end:
                before_text = full_text[last_end : match.start()]
                if before_text:
                    segments.append(Plain(before_text))

            shortcode = match.group(1)
            sticker = resolved_shortcodes.get(shortcode.strip().lower())
            if sticker:
                sticker_id = getattr(sticker, "sticker_id", None) or sticker.body
                within_limit = (
                    max_stickers is None
                    or sticker_id in found_stickers
                    or len(found_stickers) < max_stickers
                )
                if within_limit:
                    if sticker_id not in found_stickers:
                        found_stickers[sticker_id] = sticker
                    segments.append(sticker)
                else:
                    segments.append(Plain(full_text[match.start() : match.end()]))
            else:
                segments.append(Plain(full_text[match.start() : match.end()]))

            last_end = match.end()

        if last_end < len(full_text):
            remaining_text = full_text[last_end:]
            if remaining_text:
                segments.append(Plain(remaining_text))

        for segment in segments:
            if isinstance(segment, Plain) and not segment.text.strip():
                continue
            try:
                chain_comps = []
                if reply_id:
                    chain_comps.append(Reply(id=reply_id))
                chain_comps.append(segment)
                chain = MessageChain(chain_comps)
                await event.send(chain)
                if not isinstance(segment, Plain):
                    self._mark_sticker_used(segment)
            except Exception as e:
                logger.error(f"发送分段消息失败：{e}", exc_info=True)
                if is_streaming:
                    continue
                break

    def hook_inject_sticker_prompt(self, event: AstrMessageEvent, req: ProviderRequest):
        """Inject available sticker shortcodes into LLM prompt."""
        if not self._is_runtime_injection_enabled():
            return

        platform_name = self._get_event_platform_name(event)
        is_matrix_platform = platform_name == "matrix"
        if not is_matrix_platform and not self._is_other_platforms_extension_enabled():
            return
        if is_matrix_platform and self._is_full_intercept_enabled():
            event.set_extra("enable_streaming", False)
        if not self._ensure_storage():
            return

        try:
            stickers = self._storage.list_stickers(
                limit=self._get_prompt_sticker_limit()
            )
        except Exception as e:
            logger.debug(f"读取 sticker 列表失败，跳过提示词注入：{e}")
            return
        if not stickers:
            return

        shortcode_list = []
        for meta in stickers:
            pack_info = f" ({meta.pack_name})" if meta.pack_name else ""
            shortcode_list.append(f"- :{meta.body}:{pack_info}")

        if not shortcode_list:
            return

        sticker_prompt = STICKER_PROMPT_TEMPLATE.format(
            sticker_list="\n".join(shortcode_list)
        )

        if req.system_prompt:
            req.system_prompt = req.system_prompt + "\n\n" + sticker_prompt
        else:
            req.system_prompt = sticker_prompt

        logger.debug(f"已注入 {len(shortcode_list)} 个 sticker 短码到 LLM 提示词")
