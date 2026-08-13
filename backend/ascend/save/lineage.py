"""快照血缘存储 — lineage.json 的读写与条目维护（时间线分叉元数据）。

纯磁盘层：不依赖快照文件/存档管理器，只读写世界目录内的血缘文件。
从 ascend/save/manager.py 拆出（原 SaveManager._load_lineage /
_write_lineage / _record_snapshot_lineage / snapshot_lineage /
set_live_origin），职责单一化。
"""

import json
import os

from ascend.log import get_logger
from .io import atomic_write

logger = get_logger(__name__)

# 快照血缘（时间线分叉）元数据：{live_origin, snapshots: {file: {parent, game_time, saved_at, seq}}}
LINEAGE_FILE: str = "lineage.json"


class LineageStore:
    """世界血缘文件（快照父子关系 + 当前活目录来源）的读写。

    Args:
        root: 存档根目录（与 SaveManager.root 同源）。
    """

    def __init__(self, root: str) -> None:
        self._root = root

    def lineage_path(self, world_id: str) -> str:
        """世界血缘文件路径（时间线分叉元数据）。"""
        return os.path.join(self._root, world_id, LINEAGE_FILE)

    def load(self, world_id: str) -> dict | None:
        """读取血缘文件原始数据；缺失或损坏返回 None。

        区分「世界尚无血缘」与「血缘缺失/损坏」——后者不允许做
        反向对账（否则会把全部快照文件当幽灵误删）。
        """
        path = self.lineage_path(world_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("血缘文件损坏，按空血缘处理: %s (%s)", path, exc)
            return None
        return data if isinstance(data, dict) else None

    def get(self, world_id: str) -> dict:
        """读取世界血缘：快照的父子关系与当前活目录来源。

        Returns:
            {"live_origin": str|"", "snapshots": {file: {parent, game_time,
             saved_at, seq}}}。文件缺失或损坏时返回空血缘（初始世界
             无快照 / 损坏按空处理，反向对账由 load 另行把关）。
            seq 是唯一的权威排序键，其余时间字段仅作展示。
        """
        default: dict = {"live_origin": "", "snapshots": {}}
        data = self.load(world_id)
        if data is None:
            return default
        data.setdefault("live_origin", "")
        data.setdefault("snapshots", {})
        if not isinstance(data["snapshots"], dict):
            data["snapshots"] = {}
        return data

    def write(self, world_id: str, lineage: dict) -> bool:
        """原子写入血缘文件（世界外元数据，失败不阻断主流程）。

        Returns:
            写入是否成功（失败时调用方应跳过依赖血缘一致性的
            后续步骤——如保留策略的反向对账，防止误删新文件）。
        """
        try:
            atomic_write(
                self.lineage_path(world_id),
                json.dumps(lineage, ensure_ascii=False, indent=2),
            )
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
            int(entry["seq"]) for entry in lineage["snapshots"].values()
            if isinstance(entry, dict) and "seq" in entry
        ]
        lineage["snapshots"][filename] = {
            "parent": lineage.get("live_origin", ""),
            "game_time": int(game_time),
            "saved_at": float(saved_at),
            "seq": (max(seqs) + 1) if seqs else 0,
        }
        lineage["live_origin"] = filename
        return self.write(world_id, lineage)

    def set_live_origin(self, world_id: str, snapshot_file: str) -> None:
        """记录活目录来源：回滚后调用，标记当前时间点从该快照派生。"""
        lineage = self.get(world_id)
        lineage["live_origin"] = snapshot_file
        self.write(world_id, lineage)
