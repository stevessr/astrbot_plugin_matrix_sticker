"""
Matrix Sticker 管理插件

提供 sticker 保存、列表、发送等管理命令
依赖 astrbot_plugin_matrix_adapter 的 sticker 模块
"""

from astrbot.api.star import Context, Star, register

from .sticker_alias import StickerAliasMixin
from .sticker_commands import StickerCommandMixin
from .sticker_llm import StickerLLMMixin
from .sticker_storage import StickerStorageMixin


@register(
    name="astrbot_plugin_matrix_sticker",
    desc="Matrix Sticker 管理插件，提供 sticker 保存、列表和发送命令",
    version="1.0.0",
    author="AstrBot",
)
class MatrixStickerPlugin(
    Star,
    StickerStorageMixin,
    StickerCommandMixin,
    StickerAliasMixin,
    StickerLLMMixin,
):
    """Matrix Sticker 管理插件"""

    def __init__(self, context: Context, config: dict | None = None):
        super().__init__(context, config)
        self._storage = None
        self._Sticker = None
        self._StickerInfo = None
        self._init_sticker_module()
