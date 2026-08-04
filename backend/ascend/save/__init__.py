"""存档系统 — 存档位的创建、实时写入、手动快照与读档。

世界外元操作（状态通道）：不产生历史、不进因果图。
实体表全量持久化在 Issue #25 落地，本模块已预留 entities.json.enc 位置。
"""

from .crypto import SaveKeys, SaveCryptoError
from .manifest import Manifest, SaveFormatError, MANIFEST_NAME
from .manager import SaveManager
from .serializer import (
    collect_state, apply_state, apply_clock, apply_player, aligned_time,
)

__all__ = [
    "SaveKeys",
    "SaveCryptoError",
    "Manifest",
    "SaveFormatError",
    "MANIFEST_NAME",
    "SaveManager",
    "collect_state",
    "apply_state",
    "apply_clock",
    "apply_player",
    "aligned_time",
]
