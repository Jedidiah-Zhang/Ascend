"""事件归档存储 — SQLite 后端。

被 trim 移出内存的事件体写入 SQLite，支持按时间、实体、空间区域查询。
对 EventBus 调用方完全透明。
"""

import json
import os
import sqlite3

from .event import Event
from .affected import AffectedParty

# SQLite DDL 文件与此文件同目录
_SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "schema.sqlite.sql")


class EventArchive:
    """事件归档存储。

    使用 SQLite 存储被 trim 移出内存的事件体。
    支持按时间范围、实体、空间区域查询，
    查询结果自动反序列化为 Event 对象。

    用法:
        archive = EventArchive("save/events.db")
        archive.archive(events)
        results = archive.query_time_range(0, 1000)
        archive.close()
    """

    def __init__(self, path: str) -> None:
        """打开或创建归档数据库。

        Args:
            path: SQLite 数据库文件路径。
        """
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
        self._db.execute("PRAGMA mmap_size=268435456")   # 256MB 内存映射
        self._db.execute("PRAGMA cache_size=-8000")      # 8MB 页缓存
        self._create_schema()

    def __repr__(self) -> str:
        """返回归档状态摘要。

        Returns:
            含路径和事件数量的 repr 字符串。
        """
        try:
            count = self._db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        except sqlite3.Error:
            count = 0
        return f"EventArchive(events={count})"

    # ── Schema ────────────────────────────────────────

    def _create_schema(self) -> None:
        """创建表和索引（幂等），迁移旧 schema。

        DDL 定义在 schema.sqlite.sql 中，使用标准 SQL 语法。
        幂等性由 Python 层保证——若表已存在则静默跳过。
        对旧表自动添加缺失的 weight 列。
        """
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            ddl = f.read()
        try:
            self._db.executescript(ddl)
        except sqlite3.OperationalError:
            pass

        # 迁移：旧 schema 可能缺少 weight 列
        try:
            self._db.execute("ALTER TABLE events ADD COLUMN weight INTEGER DEFAULT 1")
        except sqlite3.OperationalError:
            pass  # 列已存在

        # 迁移：旧 schema 可能缺少 event_edges 表
        try:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS event_edges ("
                "from_id TEXT NOT NULL, "
                "to_id TEXT NOT NULL, "
                "relation_type TEXT NOT NULL, "
                "PRIMARY KEY (from_id, to_id, relation_type))"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_edges_from "
                "ON event_edges(from_id)"
            )
            self._db.execute(
                "CREATE INDEX IF NOT EXISTS idx_event_edges_to "
                "ON event_edges(to_id)"
            )
        except sqlite3.OperationalError:
            pass

    # ── 写入 ──────────────────────────────────────────

    def archive(self, events: list[Event]) -> None:
        """批量归档事件到磁盘。

        对已存在的事件 ID 静默忽略（幂等）。

        Args:
            events: 要归档的事件列表。
        """
        if not events:
            return

        event_rows: list[tuple] = []
        entity_rows: list[tuple] = []
        edge_rows: list[tuple] = []

        for ev in events:
            event_rows.append((
                ev.id,
                ev.timestamp,
                ev.location[0],
                ev.location[1],
                ev.location[2] if len(ev.location) > 2 else None,
                ev.location[3] if len(ev.location) > 3 else None,
                ev.initiator_type,
                ev.initiator_id,
                ev.event_type,
                ev.weight,
                json.dumps(ev.data, ensure_ascii=False),
                json.dumps(ev.caused_by, ensure_ascii=False),
                ev.observes,
                json.dumps(ev.co_participants, ensure_ascii=False),
                json.dumps(
                    [{"entity_id": a.entity_id, "role": a.role}
                     for a in ev.affected],
                    ensure_ascii=False,
                ),
            ))
            # 实体索引
            entity_rows.append((ev.id, ev.initiator_id, "initiator"))
            for ap in ev.affected:
                entity_rows.append((ev.id, ap.entity_id, ap.role))
            # 图边：一并存储，支持懒加载和预热
            for cause_id in ev.caused_by:
                edge_rows.append((cause_id, ev.id, "caused_by"))
            if ev.observes:
                edge_rows.append((ev.id, ev.observes, "observes"))
            for pid in ev.co_participants:
                if pid != ev.initiator_id:
                    edge_rows.append((ev.id, pid, "co_participant"))

        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO events VALUES ("
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                ")",
                event_rows,
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO event_entities VALUES (?, ?, ?)",
                entity_rows,
            )
            if edge_rows:
                self._db.executemany(
                    "INSERT OR IGNORE INTO event_edges VALUES (?, ?, ?)",
                    edge_rows,
                )

    # ── 查询 ──────────────────────────────────────────

    def query_by_id(self, event_id: str) -> Event | None:
        """按事件 ID 查询归档中的单个事件。

        利用主键索引，O(1) 查询。用于 trim 后按 ID 取回
        已归档的事件体。

        Args:
            event_id: 事件唯一标识。

        Returns:
            重建的 Event 实例，不存在时返回 None。
        """
        row = self._db.execute(
            "SELECT * FROM events WHERE id = ?", (event_id,)
        ).fetchone()
        if row is None:
            return None
        return self._row_to_event(row)

    def query_time_range(
        self,
        start_time: int,
        end_time: int,
        *,
        event_type: str | None = None,
        initiator_type: str | None = None,
    ) -> list[Event]:
        """按时间范围查询归档事件。

        Args:
            start_time: 起始 tick（包含）。
            end_time: 结束 tick（包含）。
            event_type: 可选，按事件类型过滤。
            initiator_type: 可选，按发起方类型过滤。

        Returns:
            满足条件的事件列表，按时间排序。
        """
        sql = "SELECT * FROM events WHERE timestamp >= ? AND timestamp <= ?"
        params: list = [start_time, end_time]

        if event_type:
            sql += " AND event_type = ?"
            params.append(event_type)
        if initiator_type:
            sql += " AND initiator_type = ?"
            params.append(initiator_type)

        sql += " ORDER BY timestamp"
        rows = self._db.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query_entity(
        self,
        entity_id: str,
        start_time: int,
        end_time: int,
    ) -> list[Event]:
        """查询实体在归档中的事件。

        实体作为发起方或受影响方参与的事件均被查询。

        Args:
            entity_id: 实体唯一标识。
            start_time: 起始 tick（包含）。
            end_time: 结束 tick（包含）。

        Returns:
            该实体在时间范围内的事件列表，按时间排序。
        """
        rows = self._db.execute(
            """
            SELECT DISTINCT e.* FROM events e
            INNER JOIN event_entities ee ON e.id = ee.event_id
            WHERE ee.entity_id = ?
              AND e.timestamp >= ?
              AND e.timestamp <= ?
            ORDER BY e.timestamp
            """,
            (entity_id, start_time, end_time),
        ).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query_region(
        self,
        center_chunk: tuple[int, int],
        radius: int = 1,
        *,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[Event]:
        """按空间区域查询归档事件。

        Args:
            center_chunk: 中心 chunk 坐标 (chunk_x, chunk_y)。
            radius: 搜索半径（chunk 数），默认 1 即 3×3 区域。
            start_time: 可选，时间下界。
            end_time: 可选，时间上界。

        Returns:
            区域内满足条件的事件列表。
        """
        cx, cy = center_chunk
        sql = """
            SELECT * FROM events
            WHERE chunk_x >= ? AND chunk_x <= ?
              AND chunk_y >= ? AND chunk_y <= ?
        """
        params: list = [
            cx - radius, cx + radius,
            cy - radius, cy + radius,
        ]

        if start_time is not None:
            sql += " AND timestamp >= ?"
            params.append(start_time)
        if end_time is not None:
            sql += " AND timestamp <= ?"
            params.append(end_time)

        sql += " ORDER BY timestamp"
        rows = self._db.execute(sql, params).fetchall()
        return [self._row_to_event(r) for r in rows]

    def query_edges(
        self, event_id: str, *, direction: str = "both"
    ) -> list[tuple[str, str, str]]:
        """查询归档中某事件的所有关联边。

        Args:
            event_id: 事件 ID。
            direction: "out"（出边）、"in"（入边）或 "both"（默认）。

        Returns:
            (from_id, to_id, relation_type) 元组列表。
        """
        if direction == "out":
            rows = self._db.execute(
                "SELECT from_id, to_id, relation_type "
                "FROM event_edges WHERE from_id = ?",
                (event_id,),
            ).fetchall()
        elif direction == "in":
            rows = self._db.execute(
                "SELECT from_id, to_id, relation_type "
                "FROM event_edges WHERE to_id = ?",
                (event_id,),
            ).fetchall()
        else:
            rows = self._db.execute(
                "SELECT from_id, to_id, relation_type "
                "FROM event_edges WHERE from_id = ? OR to_id = ?",
                (event_id, event_id),
            ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def query_edges_bulk(
        self, event_ids: list[str]
    ) -> list[tuple[str, str, str]]:
        """批量查询多个事件的所有关联边，用于图预热。

        Args:
            event_ids: 事件 ID 列表。

        Returns:
            (from_id, to_id, relation_type) 元组列表。
        """
        if not event_ids:
            return []
        placeholders = ",".join(["?"] * len(event_ids))
        rows = self._db.execute(
            f"SELECT from_id, to_id, relation_type FROM event_edges "
            f"WHERE from_id IN ({placeholders}) OR to_id IN ({placeholders})",
            event_ids * 2,
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    # ── 反序列化 ──────────────────────────────────────

    @staticmethod
    def _row_to_event(row: sqlite3.Row) -> Event:
        """将 SQLite 行反序列化为 Event 对象。

        Args:
            row: sqlite3.Row 对象。

        Returns:
            重建的 Event 实例。
        """
        affected_data = json.loads(row["affected_json"])
        affected = [
            AffectedParty(entity_id=a["entity_id"], role=a["role"])
            for a in affected_data
        ]

        return Event(
            id=row["id"],
            timestamp=row["timestamp"],
            location=(
                row["chunk_x"],
                row["chunk_y"],
                row["tile_x"],
                row["tile_y"],
            ),
            initiator_type=row["initiator_type"],
            initiator_id=row["initiator_id"],
            event_type=row["event_type"],
            weight=row["weight"] if "weight" in row.keys() else 1,
            data=json.loads(row["data_json"]),
            caused_by=json.loads(row["caused_by_json"]),
            observes=row["observes"],
            co_participants=json.loads(row["co_participants_json"]),
            affected=affected,
        )

    # ── 生命周期 ──────────────────────────────────────

    def close(self) -> None:
        """关闭数据库连接。"""
        self._db.close()
