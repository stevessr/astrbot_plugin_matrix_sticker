"""
Matrix sticker LLM mixin - LLM 相关 hook 逻辑
"""

import re

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.provider.entities import LLMResponse, ProviderRequest

from .base import StickerBaseMixin

SHORTCODE_PATTERN = re.compile(r":([a-zA-Z0-9_-]+):")

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
        if not self._ensure_storage():
            logger.debug("Sticker storage 未初始化，跳过短码替换")
            return

        result = event.get_result()
        if result is None or not result.chain:
            logger.debug("没有消息结果或消息链为空")
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

        all_matches = list(SHORTCODE_PATTERN.finditer(full_text))
        logger.debug(
            f"在文本中找到 {len(all_matches)} 个短码匹配: {[m.group(1) for m in all_matches]}"
        )

        max_stickers = self._get_max_stickers_per_reply()
        found_stickers: dict[str, any] = {}
        new_chain = []
        modified = False

        for component in result.chain:
            if isinstance(component, Plain):
                text = component.text
                matches = list(SHORTCODE_PATTERN.finditer(text))

                if not matches:
                    new_chain.append(component)
                    continue

                last_end = 0
                component_modified = False
                for match in matches:
                    shortcode = match.group(1)

                    sticker = self._find_sticker_by_shortcode(shortcode)
                    logger.debug(
                        f"查找短码 '{shortcode}': {'找到' if sticker else '未找到'}"
                    )

                    if not sticker:
                        continue

                    sticker_id = (
                        getattr(sticker, "sticker_id", None) or sticker.body
                    )

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
                        if is_streaming:
                            if sticker_id not in found_stickers:
                                found_stickers[sticker_id] = sticker
                        else:
                            if sticker_id not in found_stickers:
                                new_chain.append(sticker)
                                found_stickers[sticker_id] = sticker
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
            f"处理完成: modified={modified}, found_stickers={len(found_stickers)}"
        )

        if modified:
            if is_streaming and found_stickers:
                unique_stickers = list(found_stickers.values())
                logger.info(
                    f"流式输出完成，发送 {len(unique_stickers)} 个去重后的 sticker"
                )
                for i, sticker in enumerate(unique_stickers):
                    try:
                        logger.info(
                            f"发送 sticker {i + 1}/{len(unique_stickers)}: {sticker.body if hasattr(sticker, 'body') else sticker}"
                        )
                        chain = MessageChain([sticker])
                        logger.info(f"创建 MessageChain: {chain}")
                        send_result = await event.send(chain)
                        logger.info(f"发送结果: {send_result}")
                    except Exception as e:
                        logger.error(f"发送 sticker 失败：{e}", exc_info=True)
            else:
                result.chain = new_chain
                logger.debug("已替换消息中的 sticker 短码")

    def hook_inject_sticker_prompt(self, event: AstrMessageEvent, req: ProviderRequest):
        """Inject available sticker shortcodes into LLM prompt."""
        if not self._ensure_storage():
            return

        stickers = self._storage.list_stickers(limit=50)
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
