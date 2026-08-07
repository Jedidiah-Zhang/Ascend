"""存档清单 — manifest.json 的读写与校验。

manifest 明文存储（存档选择页必须在免密钥下展示列表信息），
记录世界的元信息：名称、seed、出生点、游戏时间、运行时长等。
"""

import json
import os
import time as _real_time
from dataclasses import dataclass, asdict

from .io import atomic_write


MANIFEST_NAME: str = "manifest.json"

# ── 世界生成调参契约（Issue #8）─────────────────────────────
# 创建世界流程的参数范围：save_create 校验（_validate_gen_params）、
# map_preview 请求校验（preview_handler）、种子随机上限（create_world）
# 全部引用此处——新增调参键时在此定义范围，前后端各自落地。

# 种子范围：1 ~ 2**31-1（0 = 随机占位，创建时随机定案）
SEED_MAX: int = 2**31 - 1
# 陆地比例范围 (0, 1]：严格大于 0（无纯海洋世界）
LAND_RATIO_MAX: float = 1.0
# 地图尺寸范围 [20, 200] km（UI 档位 60/100/150 km，含余量）
SIZE_KM_MIN: float = 20.0
SIZE_KM_MAX: float = 200.0


class SaveFormatError(Exception):
    """存档格式错误（字段缺失、类型非法等）。"""


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
    birth_chunk: tuple[int, int] | None = None
    created_at: float = 0.0
    last_played_at: float = 0.0
    play_duration_sec: float = 0.0
    game_time: int = 0
    snapshot_count: int = 0
    secrets_blob: str | None = None
    # 世界生成调参（创建世界流程的产出，Issue #8）：目前含
    # land_ratio（目标陆地比例 [0-1]）；未来新增群落/物种分布等。
    # 种子之外再生的不确定性来源，创建时定案，与 seed 同权重。
    gen_params: dict | None = None

    @property
    def dict(self) -> dict:
        """转换为可 JSON 序列化的字典（birth_chunk 转 list）。"""
        d = asdict(self)
        if d["birth_chunk"] is not None:
            d["birth_chunk"] = list(d["birth_chunk"])
        return d

    @staticmethod
    def _validate_gen_params(gen_params: dict) -> dict:
        """校验并规范化生成参数（Issue #8）。

        land_ratio 必须为 (0, 1] 内的有限浮点；width_km/height_km 必须为
        [20, 200] 内的有限浮点（地图尺寸档位 60/100/150 km，含余量）。
        未知键保留（向前兼容，未来步骤的参数由各自模块校验）。
        非法时抛 SaveFormatError。
        """
        result = dict(gen_params)
        land_ratio = result.get("land_ratio")
        if land_ratio is not None:
            try:
                land_ratio = float(land_ratio)
            except (TypeError, ValueError) as exc:
                raise SaveFormatError(f"gen_params.land_ratio 非法: {exc}") from exc
            if not (0.0 < land_ratio <= LAND_RATIO_MAX):
                raise SaveFormatError(f"gen_params.land_ratio 越界: {land_ratio}")
            result["land_ratio"] = land_ratio
        for key in ("width_km", "height_km"):
            value = result.get(key)
            if value is None:
                continue
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise SaveFormatError(f"gen_params.{key} 非法: {exc}") from exc
            if not (SIZE_KM_MIN <= value <= SIZE_KM_MAX):
                raise SaveFormatError(f"gen_params.{key} 越界: {value}")
            result[key] = value
        return result

    @staticmethod
    def from_dict(data: dict) -> "Manifest":
        """从字典反序列化并校验。

        Args:
            data: manifest 字典。

        Returns:
            Manifest 实例。

        Raises:
            SaveFormatError: 关键字段缺失或类型非法。
        """
        try:
            bc = data.get("birth_chunk")
            blob = data.get("secrets_blob")
            if bc is not None:
                bc = tuple(bc)
                if len(bc) != 2:
                    raise ValueError(f"birth_chunk 长度非法: {len(bc)}")
            raw_gen_params = data.get("gen_params")
            if raw_gen_params is not None and not isinstance(raw_gen_params, dict):
                raise ValueError("gen_params 必须为对象")
            gen_params = (
                Manifest._validate_gen_params(raw_gen_params)
                if raw_gen_params else None
            )
            return Manifest(
                name=str(data["name"]),
                seed=int(data["seed"]),
                world_id=str(data["world_id"]),
                birth_chunk=bc,
                created_at=float(data.get("created_at", 0.0)),
                last_played_at=float(data.get("last_played_at", 0.0)),
                play_duration_sec=float(data.get("play_duration_sec", 0.0)),
                game_time=int(data.get("game_time", 0)),
                snapshot_count=int(data.get("snapshot_count", 0)),
                secrets_blob=str(blob) if blob else None,
                gen_params=gen_params,
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
            SaveFormatError: 文件缺失/损坏/字段非法。
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
