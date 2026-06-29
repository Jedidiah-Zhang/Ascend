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
        self._db = sqlite3.connect(path)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute("PRAGMA synchronous=NORMAL")
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
        """创建表和索引（幂等）。

        DDL 定义在 schema.sqlite.sql 中，使用标准 SQL 语法。
        幂等性由 Python 层保证——若表已存在则静默跳过。
        """
        with open(_SCHEMA_PATH, encoding="utf-8") as f:
            ddl = f.read()
        try:
            self._db.executescript(ddl)
        except sqlite3.OperationalError:
            # 表或索引已存在，幂等跳过
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
            # 实体索引：initiator + affected
            entity_rows.append((ev.id, ev.initiator_id, "initiator"))
            for ap in ev.affected:
                entity_rows.append((ev.id, ap.entity_id, ap.role))

        with self._db:
            self._db.executemany(
                "INSERT OR IGNORE INTO events VALUES ("
                "?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?"
                ")",
                event_rows,
            )
            self._db.executemany(
                "INSERT OR IGNORE INTO event_entities VALUES (?, ?, ?)",
                entity_rows,
            )

    # ── 查询 ──────────────────────────────────────────

    def query_time_range(
        self,
        start_time: float,
        end_time: float,
        *,
        event_type: str | None = None,
        initiator_type: str | None = None,
    ) -> list[Event]:
        """按时间范围查询归档事件。

        Args:
            start_time: 起始时间（包含）。
            end_time: 结束时间（包含）。
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
        start_time: float,
        end_time: float,
    ) -> list[Event]:
        """查询实体在归档中的事件。

        实体作为发起方或受影响方参与的事件均被查询。

        Args:
            entity_id: 实体唯一标识。
            start_time: 起始时间（包含）。
            end_time: 结束时间（包含）。

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
        start_time: float | None = None,
        end_time: float | None = None,
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
