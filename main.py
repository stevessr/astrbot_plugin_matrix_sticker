"""
Matrix Sticker 管理插件

提供 sticker 保存、列表、发送等管理命令
依赖 astrbot_plugin_matrix_adapter 的 sticker 模块
"""

import importlib
import re
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import ResultContentType
from astrbot.core.provider.entities import ProviderRequest

# 表情短码正则：匹配 :shortcode: 格式
SHORTCODE_PATTERN = re.compile(r":([a-zA-Z0-9_-]+):")

# LLM 提示词模板
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


class MatrixStickerPlugin(Star):
    """Matrix Sticker 管理插件"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self._storage = None
        self._Sticker = None
        self._StickerInfo = None
        self._init_sticker_module()

    def _init_sticker_module(self):
        """初始化 sticker 模块（从 matrix adapter 导入）"""
        try:
            # 确保 plugins 目录在 sys.path 中
            plugins_dir = Path(__file__).parent.parent
            if str(plugins_dir) not in sys.path:
                sys.path.insert(0, str(plugins_dir))

            # 使用 importlib 动态导入
            sticker_module = importlib.import_module(
                "astrbot_plugin_matrix_adapter.sticker"
            )
            self._storage = sticker_module.StickerStorage()
            self._Sticker = sticker_module.Sticker
            self._StickerInfo = sticker_module.StickerInfo
            logger.info("Matrix Sticker 插件初始化成功")
        except ImportError as e:
            logger.warning(f"无法导入 matrix sticker 模块：{e}")
            logger.warning("请确保已安装 astrbot_plugin_matrix_adapter 插件")
            self._storage = None
            self._Sticker = None
            self._StickerInfo = None

    def _ensure_storage(self):
        """确保存储已初始化并刷新索引"""
        if self._storage is None:
            self._init_sticker_module()
        if self._storage is not None:
            # 重新加载索引以获取最新数据
            if hasattr(self._storage, "reload_index"):
                self._storage.reload_index()
            elif hasattr(self._storage, "_load_index"):
                self._storage._load_index()
        return self._storage is not None

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

        # 解析参数
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
            # 如果是 MessageChain，已经通过 event.send() 发送

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
            # 手动同步当前房间的 sticker 包
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

    async def _list_stickers(self, pack_name: str | None = None) -> str:
        """列出 sticker"""
        stickers = self._storage.list_stickers(pack_name=pack_name, limit=20)

        if not stickers:
            if pack_name:
                return f"包 '{pack_name}' 中没有 sticker"
            return "没有保存的 sticker"

        lines = ["已保存的 sticker："]
        for meta in stickers:
            pack_info = f" [{meta.pack_name}]" if meta.pack_name else ""
            lines.append(f"  {meta.sticker_id[:8]}: {meta.body}{pack_info}")

        if len(stickers) == 20:
            lines.append("  ... (显示前 20 个)")

        return "\n".join(lines)

    def _list_packs(self) -> str:
        """列出所有包"""
        packs = self._storage.list_packs()

        if not packs:
            return "没有 sticker 包"

        lines = ["Sticker 包列表："]
        for pack in packs:
            # 统计每个包的数量
            count = len(self._storage.list_stickers(pack_name=pack, limit=1000))
            lines.append(f"  {pack}: {count} 个 sticker")

        return "\n".join(lines)

    async def _save_sticker(
        self, event: AstrMessageEvent, name: str, pack_name: str | None
    ) -> str:
        """保存 sticker"""
        # 检查消息链中是否有 sticker
        sticker_to_save = None

        for component in event.message_obj.message:
            if hasattr(component, "type") and component.type == "Sticker":
                sticker_to_save = component
                break
            # 也检查是否是回复消息中的 sticker
            if isinstance(component, Reply):
                # 需要获取原始消息
                # 这里简化处理，假设原始消息已经在消息链中
                pass

        # 如果当前消息没有 sticker，检查消息链中的图片
        # 用户可能想把图片保存为 sticker
        if sticker_to_save is None:
            for component in event.message_obj.message:
                if isinstance(component, Image):
                    # 将图片转换为 sticker
                    try:
                        if self._Sticker is None or self._StickerInfo is None:
                            raise ImportError("Sticker 模块未加载")

                        sticker_to_save = self._Sticker(
                            body=name,
                            url=component.file or component.url,
                            info=self._StickerInfo(mimetype="image/png"),
                        )
                        break
                    except Exception as e:
                        logger.warning(f"转换图片为 sticker 失败：{e}")

        if sticker_to_save is None:
            return "未找到可保存的 sticker 或图片。请回复包含 sticker/图片 的消息，或发送包含图片的消息。"

        # 更新 sticker 的 body 为用户指定的名称
        sticker_to_save.body = name
        if pack_name:
            sticker_to_save.pack_name = pack_name

        try:
            # 获取 Matrix client（如果需要下载 MXC URL）
            client = None
            # 尝试从平台管理器获取 Matrix client
            try:
                for platform in self.context.platform_manager.get_insts():
                    if hasattr(platform, "client") and hasattr(
                        platform, "_matrix_config"
                    ):
                        client = platform.client
                        break
            except Exception:
                pass

            meta = await self._storage.save_sticker(
                sticker_to_save,
                client=client,
                pack_name=pack_name,
            )
            return f"已保存 sticker: {meta.sticker_id[:8]} ({name})"
        except Exception as e:
            logger.error(f"保存 sticker 失败：{e}")
            return f"保存失败：{e}"

    async def _send_sticker(self, event: AstrMessageEvent, identifier: str):
        """发送 sticker"""
        # 首先尝试按 ID 查找
        sticker = self._storage.get_sticker(identifier)

        # 如果没找到，按名称搜索
        if sticker is None:
            results = self._storage.find_stickers(query=identifier, limit=1)
            if results:
                sticker = results[0]

        if sticker is None:
            return f"未找到 sticker: {identifier}"

        try:
            # 发送 sticker
            chain = MessageChain([sticker])
            await event.send(chain)
            return None  # 成功发送，不返回文本
        except Exception as e:
            logger.error(f"发送 sticker 失败：{e}")
            return f"发送失败：{e}"

    def _delete_sticker(self, sticker_id: str) -> str:
        """删除 sticker"""
        if self._storage.delete_sticker(sticker_id):
            return f"已删除 sticker: {sticker_id}"
        return f"未找到 sticker: {sticker_id}"

    def _get_stats(self) -> str:
        """获取统计信息"""
        stats = self._storage.get_stats()

        lines = [
            "Sticker 统计信息：",
            f"  总数量：{stats['total_count']}",
            f"  占用空间：{stats['total_size_mb']} MB",
            f"  包数量：{stats['pack_count']}",
        ]

        if stats["packs"]:
            lines.append(f"  包列表：{', '.join(stats['packs'][:5])}")
            if len(stats["packs"]) > 5:
                lines.append(f"    ... 共 {len(stats['packs'])} 个包")

        return "\n".join(lines)

    async def _sync_room_stickers(self, event: AstrMessageEvent) -> str:
        """同步当前房间的 sticker 包"""
        try:
            # 获取当前房间 ID
            room_id = event.session_id
            if not room_id:
                return "无法获取当前房间 ID"

            # 尝试获取 Matrix 适配器和同步器
            syncer = None
            for platform in self.context.platform_manager.get_insts():
                if hasattr(platform, "sticker_syncer"):
                    syncer = platform.sticker_syncer
                    break

            if syncer is None:
                return "未找到 Matrix 适配器的 sticker 同步器"

            # 强制同步（即使之前已同步过）
            count = await syncer.sync_room_stickers(room_id, force=True)

            if count > 0:
                return f"成功同步 {count} 个 sticker（房间：{room_id[:20]}...）"
            else:
                # 检查是否有 sticker 包
                packs = await syncer.get_room_sticker_packs(room_id)
                if packs:
                    pack_info = ", ".join(
                        f"{p.display_name} ({p.sticker_count})" for p in packs
                    )
                    return f"房间有 sticker 包但同步数为 0：{pack_info}"
                else:
                    return "该房间没有自定义 sticker 包（im.ponies.room_emotes）"

        except Exception as e:
            logger.error(f"同步房间 sticker 失败：{e}")
            return f"同步失败：{e}"

    @filter.on_decorating_result()
    async def replace_shortcodes(self, event: AstrMessageEvent):
        """
        在消息发送前，将文本中的 :shortcode: 替换为对应的 sticker

        例如：文本中的 :thinking: 会被替换为名为 "thinking" 的 sticker
        """
        if not self._ensure_storage():
            logger.debug("Sticker storage 未初始化，跳过短码替换")
            return

        result = event.get_result()
        if result is None or not result.chain:
            logger.debug("没有消息结果或消息链为空")
            return

        # 检查是否是流式输出
        result_type = getattr(result, "result_content_type", None)
        is_streaming = result_type == ResultContentType.STREAMING_FINISH
        logger.debug(f"处理消息，result_content_type={result_type}, is_streaming={is_streaming}")

        # 提取所有文本内容
        full_text = ""
        for component in result.chain:
            if isinstance(component, Plain):
                full_text += component.text

        # 查找所有短码
        all_matches = list(SHORTCODE_PATTERN.finditer(full_text))
        logger.debug(f"在文本中找到 {len(all_matches)} 个短码匹配: {[m.group(1) for m in all_matches]}")

        # 收集所有找到的 sticker
        found_stickers = []
        new_chain = []
        modified = False

        for component in result.chain:
            if isinstance(component, Plain):
                # 查找文本中的所有短码
                text = component.text
                matches = list(SHORTCODE_PATTERN.finditer(text))

                if not matches:
                    new_chain.append(component)
                    continue

                # 处理每个短码
                last_end = 0
                for match in matches:
                    shortcode = match.group(1)

                    # 查找对应的 sticker
                    sticker = self._find_sticker_by_shortcode(shortcode)
                    logger.debug(f"查找短码 '{shortcode}': {'找到' if sticker else '未找到'}")

                    if sticker:
                        # 添加短码之前的文本
                        if match.start() > last_end:
                            before_text = text[last_end : match.start()]
                            if before_text:
                                new_chain.append(Plain(before_text))

                        if is_streaming:
                            # 流式输出时，收集 sticker 稍后单独发送
                            found_stickers.append(sticker)
                            # 保留短码文本（已经显示给用户了）
                        else:
                            # 非流式输出时，直接替换
                            new_chain.append(sticker)

                        last_end = match.end()
                        modified = True
                    # 如果没找到对应的 sticker，保留原文本

                # 添加最后一段文本
                if last_end < len(text):
                    remaining_text = text[last_end:]
                    if remaining_text:
                        new_chain.append(Plain(remaining_text))

                if not modified:
                    # 没有替换任何短码，保留原组件
                    new_chain.append(component)
            else:
                new_chain.append(component)

        logger.debug(f"处理完成: modified={modified}, found_stickers={len(found_stickers)}")

        if modified:
            if is_streaming and found_stickers:
                # 流式输出完成后，单独发送找到的 sticker
                logger.info(f"流式输出完成，发送 {len(found_stickers)} 个 sticker")
                for i, sticker in enumerate(found_stickers):
                    try:
                        logger.info(f"发送 sticker {i+1}/{len(found_stickers)}: {sticker.body if hasattr(sticker, 'body') else sticker}")
                        chain = MessageChain([sticker])
                        logger.info(f"创建 MessageChain: {chain}")
                        result = await event.send(chain)
                        logger.info(f"发送结果: {result}")
                    except Exception as e:
                        logger.error(f"发送 sticker 失败：{e}", exc_info=True)
            else:
                # 非流式输出时，替换消息链
                result.chain = new_chain
                logger.debug("已替换消息中的 sticker 短码")

    def _find_sticker_by_shortcode(self, shortcode: str):
        """根据短码查找 sticker（支持 body 和别名）"""
        if self._storage is None:
            return None

        # 获取所有 sticker 元数据用于别名匹配
        all_stickers = self._storage.list_stickers(limit=1000)

        # 首先尝试精确匹配 body
        for meta in all_stickers:
            if meta.body.lower() == shortcode.lower():
                return self._storage.get_sticker(meta.sticker_id)

        # 然后尝试匹配别名（tags）
        for meta in all_stickers:
            if meta.tags and shortcode.lower() in [t.lower() for t in meta.tags]:
                return self._storage.get_sticker(meta.sticker_id)

        # 最后尝试模糊匹配 body
        results = self._storage.find_stickers(query=shortcode, limit=1)
        if results:
            return results[0]

        return None

    @filter.on_llm_request()
    async def inject_sticker_prompt(
        self, event: AstrMessageEvent, req: ProviderRequest
    ):
        """
        在 LLM 请求前注入可用的 sticker 短码列表到系统提示词

        这让 LLM 知道可以使用哪些表情短码
        """
        if not self._ensure_storage():
            return

        # 获取所有可用的 sticker 短码
        stickers = self._storage.list_stickers(limit=50)
        if not stickers:
            return

        # 构建短码列表
        shortcode_list = []
        for meta in stickers:
            pack_info = f" ({meta.pack_name})" if meta.pack_name else ""
            shortcode_list.append(f"- :{meta.body}:{pack_info}")

        if not shortcode_list:
            return

        # 生成提示词
        sticker_prompt = STICKER_PROMPT_TEMPLATE.format(
            sticker_list="\n".join(shortcode_list)
        )

        # 注入到系统提示词
        if req.system_prompt:
            req.system_prompt = req.system_prompt + "\n\n" + sticker_prompt
        else:
            req.system_prompt = sticker_prompt

        logger.debug(f"已注入 {len(shortcode_list)} 个 sticker 短码到 LLM 提示词")

    def _get_sticker_shortcodes(self) -> list[str]:
        """获取所有可用的 sticker 短码"""
        if self._storage is None:
            return []

        stickers = self._storage.list_stickers(limit=100)
        return [meta.body for meta in stickers]

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
        # 查找 sticker
        sticker_meta = None
        for meta in self._storage.list_stickers(limit=1000):
            if meta.sticker_id.startswith(sticker_id):
                sticker_meta = meta
                break

        if sticker_meta is None:
            return f"未找到 sticker: {sticker_id}"

        # 添加别名到 tags
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
