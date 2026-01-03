"""
Matrix sticker LLM and message hooks.
"""

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Plain
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.provider.entities import ProviderRequest

from .sticker_constants import SHORTCODE_PATTERN, STICKER_PROMPT_TEMPLATE


class StickerLLMMixin:
    @filter.on_decorating_result()
    async def replace_shortcodes(self, event: AstrMessageEvent):
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

        all_matches = list(SHORTCODE_PATTERN.finditer(full_text))
        logger.debug(
            f"在文本中找到 {len(all_matches)} 个短码匹配: {[m.group(1) for m in all_matches]}"
        )

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
                for match in matches:
                    shortcode = match.group(1)

                    sticker = self._find_sticker_by_shortcode(shortcode)
                    logger.debug(
                        f"查找短码 '{shortcode}': {'找到' if sticker else '未找到'}"
                    )

                    if sticker:
                        sticker_id = (
                            getattr(sticker, "sticker_id", None) or sticker.body
                        )

                        if match.start() > last_end:
                            before_text = text[last_end : match.start()]
                            if before_text:
                                new_chain.append(Plain(before_text))

                        if is_streaming:
                            if sticker_id not in found_stickers:
                                found_stickers[sticker_id] = sticker
                        else:
                            if sticker_id not in found_stickers:
                                new_chain.append(sticker)
                                found_stickers[sticker_id] = sticker

                        last_end = match.end()
                        modified = True

                if last_end < len(text):
                    remaining_text = text[last_end:]
                    if remaining_text:
                        new_chain.append(Plain(remaining_text))

                if not modified:
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
                        result = await event.send(chain)
                        logger.info(f"发送结果: {result}")
                    except Exception as e:
                        logger.error(f"发送 sticker 失败：{e}", exc_info=True)
            else:
                result.chain = new_chain
                logger.debug("已替换消息中的 sticker 短码")

    @filter.on_llm_request()
    async def inject_sticker_prompt(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
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
