"""快照血缘存储 — lineage.json 的读写与条目维护（时间线分叉元数据）。

纯磁盘层：不依赖快照文件/存档管理器，只读写世界目录内的血缘文件。

文件格式（签名包装）:
    {"data": {live_origin, snapshots}, "sig": "<urlsafe base64>"}
    - data 为血缘数据本体，磁盘上保持 indent 缩进（人可读）；
    - sig = HMAC-SHA256(sign_key, canonical(data))，canonical 为
      sort_keys 紧凑序列化——签名只覆盖语义内容，与磁盘缩进无关；
    - sign_key 复用世界密钥（manifest.secrets_blob 混淆层），与
      state.json.enc 同一把签名钥匙——威胁级别一致：防直读/防手贱，
      不防推导（见 crypto.py 模块文档）。

严格模式：无有效签名的血缘文件一律视为损坏（load 返回 None）——
不兼容历史无签名格式，不信任任何无法验签的内容。写侧必须能取到
世界密钥，否则跳过写入（返回 False）。
"""

import base64
import json
import os
from dataclasses import dataclass
from typing import Callable, Optional

from ascend.log import get_logger

from .crypto import SaveKeys
from .io import atomic_write

logger = get_logger(__name__)

# 快照血缘（时间线分叉）元数据：{live_origin, snapshots: {file: {parent, game_time, saved_at, seq}}}
LINEAGE_FILE: str = "lineage.json"

# 血缘文档字段（扁平键即协议契约，勿改名；改动须同步 manager/handler/前端）
LIVE_ORIGIN_KEY: str = "live_origin"
SNAPSHOTS_KEY: str = "snapshots"
PARENT_KEY: str = "parent"
GAME_TIME_KEY: str = "game_time"
SAVED_AT_KEY: str = "saved_at"
SEQ_KEY: str = "seq"

# 调用方注入的世界密钥提供者：world_id → 密钥（不可用时返回 None）
KeysProvider = Callable[[str], "SaveKeys | None"]


@dataclass(frozen=True, slots=True)
class SnapshotEntry:
    """快照血缘条目 — 单个快照的父子关系与时间线排序信息。

    Attributes:
        parent: 创建时活目录来源（回滚目标快照文件名；"" = 世界初始）。
        game_time: 创建时刻的世界时间（tick）。
        saved_at: 创建时刻的墙钟时间（展示用）。
        seq: 世界内单调递增的权威排序键（创建顺序；不受游戏时间
            倒退影响，时间线/编号/串链排序的唯一事实来源）。
    """

    parent: str
    game_time: int
    saved_at: float
    seq: int

    def to_dict(self) -> dict:
        """序列化为血缘 JSON 条目（字段顺序即文件格式）。"""
        return {
            PARENT_KEY: self.parent,
            GAME_TIME_KEY: self.game_time,
            SAVED_AT_KEY: self.saved_at,
            SEQ_KEY: self.seq,
        }

    @classmethod
    def from_dict(cls, raw: object) -> Optional["SnapshotEntry"]:
        """从血缘 JSON 条目解析（字段缺失/类型不符返回 None）。

        Args:
            raw: 血缘文件中的条目对象。
        """
        if not isinstance(raw, dict):
            return None
        entry = raw
        if not all(isinstance(entry.get(k), t) for k, t in (
            (PARENT_KEY, str), (GAME_TIME_KEY, int),
            (SAVED_AT_KEY, (int, float)), (SEQ_KEY, int),
        )):
            return None
        return cls(
            parent=entry[PARENT_KEY],
            game_time=entry[GAME_TIME_KEY],
            saved_at=float(entry[SAVED_AT_KEY]),
            seq=entry[SEQ_KEY],
        )


def _b64(data: bytes) -> str:
    """bytes → urlsafe base64 字符串。"""
    return base64.urlsafe_b64encode(data).decode("ascii")


def _unb64(text: str) -> bytes:
    """urlsafe base64 字符串 → bytes。"""
    return base64.urlsafe_b64decode(text.encode("ascii"))


def _canonical(data: dict) -> bytes:
    """血缘数据的规范序列化（签名输入）：键序稳定，与磁盘缩进无关。"""
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def parse_lineage_raw(raw: object, keys: "SaveKeys | None") -> dict | None:
    """解析并验签血缘文件的 JSON 内容（load 与墓碑合并共用）。

    严格模式：结构必须为 {"data": dict, "sig": str}，签名必须与
    世界密钥匹配；任何不符（含无密钥可用）返回 None——不信任
    无法验签的内容。

    Args:
        raw: json.load 的结果。
        keys: 世界密钥；None = 无法取钥，一律按不可验签处理。

    Returns:
        验签通过的 data 字典；否则 None。
    """
    if not isinstance(raw, dict):
        return None
    data = raw.get("data")
    sig_text = raw.get("sig")
    if not isinstance(data, dict) or not isinstance(sig_text, str) or keys is None:
        return None
    try:
        sig = _unb64(sig_text)
    except (ValueError, base64.binascii.Error):
        return None
    if not keys.verify_bytes(_canonical(data), sig):
        return None
    return data


class LineageStore:
    """世界血缘文件（快照父子关系 + 当前活目录来源）的读写。

    Args:
        root: 存档根目录（与 SaveManager.root 同源）。
        keys_provider: 世界密钥提供者（验签/签名用）；缺失时读写
            一律降级（load→None、write→False）。
    """

    def __init__(
        self,
        root: str,
        keys_provider: KeysProvider | None = None,
    ) -> None:
        self._root = root
        self._keys_provider = keys_provider

    def lineage_path(self, world_id: str) -> str:
        """世界血缘文件路径（时间线分叉元数据）。"""
        return os.path.join(self._root, world_id, LINEAGE_FILE)

    def _keys(self, world_id: str) -> "SaveKeys | None":
        if self._keys_provider is None:
            return None
        return self._keys_provider(world_id)

    def load(self, world_id: str) -> dict | None:
        """读取并验签血缘文件；缺失/损坏/验签失败均返回 None。

        严格模式：无有效签名 = 损坏——prune 只认 load 非 None 的血缘，
        不可验签时不做任何淘汰（宁缺勿删）。
        """
        path = self.lineage_path(world_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("血缘文件损坏，按不可验签处理: %s (%s)", path, exc)
            return None
        data = parse_lineage_raw(raw, self._keys(world_id))
        if data is None:
            logger.warning(
                "血缘文件签名缺失或不匹配（历史格式/被篡改），按损坏处理: %s", path,
            )
        return data

    def get(self, world_id: str) -> dict:
        """读取世界血缘：快照的父子关系与当前活目录来源。

        Returns:
            {"live_origin": str|"", "snapshots": {file: {parent, game_time,
             saved_at, seq}}}。文件缺失/损坏/验签失败时返回空血缘
            （初始世界无快照 / 不可验签按空处理，反向对账由 load
            另行把关）。seq 是唯一的权威排序键，其余时间字段仅作展示。
        """
        default: dict = {LIVE_ORIGIN_KEY: "", SNAPSHOTS_KEY: {}}
        data = self.load(world_id)
        if data is None:
            return default
        data.setdefault(LIVE_ORIGIN_KEY, "")
        data.setdefault(SNAPSHOTS_KEY, {})
        if not isinstance(data[SNAPSHOTS_KEY], dict):
            data[SNAPSHOTS_KEY] = {}
        return data

    def write(self, world_id: str, lineage: dict) -> bool:
        """签名并原子写入血缘文件（世界外元数据，失败不阻断主流程）。

        Returns:
            写入是否成功（False = 未落盘，调用方应跳过依赖血缘
            一致性的后续步骤——如保留策略的反向对账，防止误删）。
        """
        keys = self._keys(world_id)
        if keys is None:
            logger.warning("血缘签名密钥不可用，跳过写入: %s", world_id)
            return False
        sig = keys.sign_bytes(_canonical(lineage))
        payload = json.dumps(
            {"data": lineage, "sig": _b64(sig)},
            ensure_ascii=False, indent=2,
        )
        try:
            atomic_write(self.lineage_path(world_id), payload)
            return True
        except OSError as exc:
            logger.warning("血缘文件写入失败: %s (%s)", world_id, exc)
            return False

    def record_snapshot(
        self, world_id: str, filename: str,
        game_time: int, saved_at: float,
    ) -> bool:
        """记录快照血缘条目并更新活目录来源。

        parent = 创建时活目录来源（最近一个快照 / "" = 世界初始）；
        创建后活目录来源更新为该快照——活状态从快照内容继续，
        连续保存自动串链：后一个快照从最近一个派生。

        seq = 世界内单调递增的权威排序键（创建顺序，不受回滚后
        游戏时间倒退影响），时间线/编号/串链排序的唯一事实来源。

        Returns:
            血缘写入是否成功（False = 条目未落盘，调用方跳过 prune）。
        """
        lineage = self.get(world_id)
        seqs = [
            entry.seq for entry in (
                parse_snapshot_entries(lineage[SNAPSHOTS_KEY]).values())
        ]
        lineage[SNAPSHOTS_KEY][filename] = SnapshotEntry(
            parent=str(lineage.get(LIVE_ORIGIN_KEY, "")),
            game_time=int(game_time),
            saved_at=float(saved_at),
            seq=(max(seqs) + 1) if seqs else 0,
        ).to_dict()
        lineage[LIVE_ORIGIN_KEY] = filename
        return self.write(world_id, lineage)

    def set_live_origin(self, world_id: str, snapshot_file: str) -> None:
        """记录活目录来源：回滚后调用，标记当前时间点从该快照派生。"""
        lineage = self.get(world_id)
        lineage[LIVE_ORIGIN_KEY] = snapshot_file
        self.write(world_id, lineage)


def parse_snapshot_entries(snapshots: object) -> dict[str, SnapshotEntry]:
    """解析血缘的 snapshots 段为键入条目对象（非法条目跳过）。

    Args:
        snapshots: 血缘文件中的 snapshots 段（任何值）。

    Returns:
        {filename: SnapshotEntry}；空/损坏时为空字典。
    """
    entries: dict[str, SnapshotEntry] = {}
    if not isinstance(snapshots, dict):
        return entries
    for filename, raw in snapshots.items():
        entry = SnapshotEntry.from_dict(raw)
        if entry is not None:
            entries[str(filename)] = entry
    return entries
