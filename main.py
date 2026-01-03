"""
Matrix Sticker 管理插件

提供 sticker 保存、列表、发送等管理命令
依赖 astrbot_plugin_matrix_adapter 的 sticker 模块
"""

import importlib
import sys
from pathlib import Path

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.message_components import Image, Plain, Reply
from astrbot.api.star import Context, Star


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
            yield event.plain_result("Sticker 模块未初始化，请确保已安装 Matrix 适配器插件")
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
            yield event.plain_result(f"未知子命令：{subcommand}\n" + self._get_help_text())

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

提示：
- 回复一条包含 sticker 的消息并使用 /sticker save 来保存
- 使用 /sticker send 来发送已保存的 sticker
- 使用 /sticker sync 来同步房间的自定义 sticker"""

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

    async def _save_sticker(self, event: AstrMessageEvent, name: str, pack_name: str | None) -> str:
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
                    if hasattr(platform, "client") and hasattr(platform, "_matrix_config"):
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
                    pack_info = ", ".join(f"{p.display_name} ({p.sticker_count})" for p in packs)
                    return f"房间有 sticker 包但同步数为 0：{pack_info}"
                else:
                    return "该房间没有自定义 sticker 包（im.ponies.room_emotes）"

        except Exception as e:
            logger.error(f"同步房间 sticker 失败：{e}")
            return f"同步失败：{e}"
