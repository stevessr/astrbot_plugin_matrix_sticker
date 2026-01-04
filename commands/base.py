"""
Matrix sticker base mixin - 存储和辅助方法
"""

from .room_emote_mixin import StickerRoomEmoteMixin
from .storage_mixin import StickerStorageMixin


class StickerBaseMixin(StickerStorageMixin, StickerRoomEmoteMixin):
    """Sticker 基础功能：存储、查询和房间表情相关命令"""

    pass
