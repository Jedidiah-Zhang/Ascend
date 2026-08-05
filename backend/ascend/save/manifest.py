"""存档清单 — manifest.json 的读写与校验。

manifest 明文存储（存档选择页必须在免密钥下展示列表信息），
记录世界的元信息：名称、seed、出生点、游戏时间、运行时长等。

格式版本: format_version 不匹配时拒绝加载（预留迁移机制，
见 docs/世界框架/存档系统/设计.md 未来优化）。
"""

import json
import os
import time as _real_time
from dataclasses import dataclass, asdict

from ascend.config import SAVE_FORMAT_VERSION
from .io import atomic_write


MANIFEST_NAME: str = "manifest.json"


class SaveFormatError(Exception):
    """存档格式错误（版本不兼容、字段缺失等）。"""


@dataclass(slots=True)
class Manifest:
    """存档位元信息。

    secrets_blob: 密钥混淆串（SaveKeys.protect 输出）。密钥不落盘为
        明文 key.json，而是加密后藏于此字段随档分发（混淆层，防直读；
        真实防线仍是 HMAC，见 crypto.py 威胁模型说明）。
    """

    name: str
    seed: int
    world_id: str
    format_version: int = SAVE_FORMAT_VERSION
    birth_chunk: tuple[int, int] | None = None
    created_at: float = 0.0
    last_played_at: float = 0.0
    play_duration_sec: float = 0.0
    game_time: int = 0
    snapshot_count: int = 0
    secrets_blob: str | None = None

    @property
    def dict(self) -> dict:
        """转换为可 JSON 序列化的字典（birth_chunk 转 list）。"""
        d = asdict(self)
        if d["birth_chunk"] is not None:
            d["birth_chunk"] = list(d["birth_chunk"])
        return d

    @staticmethod
    def from_dict(data: dict) -> "Manifest":
        """从字典反序列化并校验。

        Args:
            data: manifest 字典。

        Returns:
            Manifest 实例。

        Raises:
            SaveFormatError: format_version 不兼容或关键字段缺失。
        """
        version = data.get("format_version", 1)
        if version != SAVE_FORMAT_VERSION:
            raise SaveFormatError(
                f"存档格式版本 {version} 与当前支持的 {SAVE_FORMAT_VERSION} 不兼容"
            )
        try:
            bc = data.get("birth_chunk")
            blob = data.get("secrets_blob")
            return Manifest(
                name=str(data["name"]),
                seed=int(data["seed"]),
                world_id=str(data["world_id"]),
                format_version=version,
                birth_chunk=tuple(bc) if bc else None,
                created_at=float(data.get("created_at", 0.0)),
                last_played_at=float(data.get("last_played_at", 0.0)),
                play_duration_sec=float(data.get("play_duration_sec", 0.0)),
                game_time=int(data.get("game_time", 0)),
                snapshot_count=int(data.get("snapshot_count", 0)),
                secrets_blob=str(blob) if blob else None,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise SaveFormatError(f"manifest 字段非法: {exc}") from exc

    # ── 磁盘读写 ──────────────────────────────────────────

    def write(self, path: str) -> None:
        """原子写入 manifest 文件。"""
        atomic_write(path, json.dumps(self.dict, ensure_ascii=False, indent=2))

    @staticmethod
    def read(path: str) -> "Manifest":
        """从文件读取并校验。

        Raises:
            SaveFormatError: 文件缺失/损坏/版本不兼容。
        """
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError as exc:
            raise SaveFormatError(f"manifest 缺失: {path}") from exc
        except json.JSONDecodeError as exc:
            raise SaveFormatError(f"manifest 损坏: {exc}") from exc
        return Manifest.from_dict(data)

    def touch(self, path: str, *, game_time: int, play_duration_sec: float) -> None:
        """更新游玩信息并写盘（存档选择页展示用）。"""
        self.last_played_at = _real_time.time()
        self.game_time = game_time
        self.play_duration_sec = play_duration_sec
        self.write(path)
