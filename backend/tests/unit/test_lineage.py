"""LineageStore 单元测试 — 血缘文件读写与条目维护原语。

覆盖 ascend/save/lineage.py：load 的缺失/损坏/非 dict 分支、
get 的归一化、record_snapshot 的 parent/seq 语义、write 失败契约。
"""

import json
import os

import pytest

from ascend.save.lineage import LineageStore, LINEAGE_FILE


@pytest.fixture()
def store(tmp_path) -> LineageStore:
    """隔离的血缘存储。"""
    return LineageStore(root=str(tmp_path / "saves"))


def _world_dir_ready(store: LineageStore, world_id: str) -> None:
    """建立世界目录（真实流程由 SaveManager.create_world 创建）。"""
    os.makedirs(os.path.dirname(store.lineage_path(world_id)), exist_ok=True)


def _write_lineage_file(store: LineageStore, world_id: str, data: dict) -> None:
    """直接写血缘文件（绕过 store，构造各种输入态）。"""
    _world_dir_ready(store, world_id)
    with open(store.lineage_path(world_id), "w", encoding="utf-8") as f:
        json.dump(data, f)


class TestLoad:
    """load：缺失/损坏/非 dict 的区分。"""

    def test_load_missing_returns_none(self, store) -> None:
        """文件不存在 → None（区分「世界尚无血缘」与「损坏」）。"""
        assert store.load("a" * 32) is None

    def test_load_corrupt_returns_none(self, store, tmp_path) -> None:
        """JSON 损坏 → None（按空血缘处理，防反向对账误删）。"""
        world_id = "b" * 32
        path = store.lineage_path(world_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write("{不是 JSON")
        assert store.load(world_id) is None

    def test_load_non_dict_returns_none(self, store) -> None:
        """合法 JSON 但非 dict → None。"""
        world_id = "c" * 32
        _write_lineage_file(store, world_id, [1, 2, 3])
        assert store.load(world_id) is None

    def test_load_returns_dict(self, store) -> None:
        """正常文件原样返回。"""
        world_id = "d" * 32
        _write_lineage_file(store, world_id, {"live_origin": "", "snapshots": {}})
        assert store.load(world_id) == {"live_origin": "", "snapshots": {}}


class TestGet:
    """get：空血缘默认值 + 字段归一化。"""

    def test_get_empty_default(self, store) -> None:
        """无文件 → 空血缘默认结构（不改动调用方期望的字段）。"""
        assert store.get("e" * 32) == {"live_origin": "", "snapshots": {}}

    def test_get_normalizes_missing_fields(self, store) -> None:
        """缺字段补齐；snapshots 非 dict 归一化为空。"""
        world_id = "f" * 32
        _write_lineage_file(store, world_id, {"snapshots": "not-a-dict"})
        data = store.get(world_id)
        assert data["live_origin"] == ""
        assert data["snapshots"] == {}

    def test_get_preserves_existing_data(self, store) -> None:
        """既有血缘内容保留。"""
        world_id = "g" * 32
        original = {
            "live_origin": "@x-auto.ascendsave",
            "snapshots": {"@x-auto.ascendsave": {"parent": "", "seq": 0}},
        }
        _write_lineage_file(store, world_id, original)
        assert store.get(world_id) == original


class TestRecordSnapshot:
    """record_snapshot：parent/live_origin/seq 语义。"""

    def test_record_first_snapshot(self, store) -> None:
        """首个条目：parent=""、seq=0、live_origin 指向新文件。"""
        world_id = "h" * 32
        _world_dir_ready(store, world_id)
        ok = store.record_snapshot(world_id, "@1-auto.ascendsave", 100, 1.0)
        assert ok
        data = store.get(world_id)
        entry = data["snapshots"]["@1-auto.ascendsave"]
        assert entry["parent"] == ""
        assert entry["seq"] == 0
        assert entry["game_time"] == 100
        assert data["live_origin"] == "@1-auto.ascendsave"

    def test_record_chains_and_increments_seq(self, store) -> None:
        """连续记录：parent 指向上一来源，seq 单调递增（删除后仍递增）。"""
        world_id = "i" * 32
        _world_dir_ready(store, world_id)
        store.record_snapshot(world_id, "@1-manual.ascendsave", 100, 1.0)
        store.record_snapshot(world_id, "@2-auto.ascendsave", 200, 2.0)
        store.record_snapshot(world_id, "@3-manual.ascendsave", 300, 3.0)
        data = store.get(world_id)
        assert data["snapshots"]["@2-auto.ascendsave"]["parent"] == "@1-manual.ascendsave"
        assert data["snapshots"]["@3-manual.ascendsave"]["parent"] == "@2-auto.ascendsave"
        assert data["snapshots"]["@3-manual.ascendsave"]["seq"] == 2

        # 删除中间条目后，seq 仍取最大值 + 1（空洞免疫，创建序唯一事实来源）
        snapshots = data["snapshots"]
        snapshots.pop("@2-auto.ascendsave")
        store.write(world_id, data)
        store.record_snapshot(world_id, "@4-auto.ascendsave", 400, 4.0)
        assert store.get(world_id)["snapshots"]["@4-auto.ascendsave"]["seq"] == 3

    def test_record_write_failure_returns_false(self, store, monkeypatch) -> None:
        """写入失败（磁盘错误）→ False，血缘未变更。"""
        world_id = "j" * 32
        monkeypatch.setattr(store, "write", lambda w, d: False)
        assert store.record_snapshot(world_id, "@1.ascendsave", 0, 0.0) is False


class TestWrite:
    """write：原子写入与失败契约。"""

    def test_write_persists_lineage(self, store) -> None:
        """写入后 load 可读回。"""
        world_id = "k" * 32
        _world_dir_ready(store, world_id)
        lineage = {"live_origin": "@1.ascendsave", "snapshots": {}}
        assert store.write(world_id, lineage) is True
        assert store.load(world_id) == lineage

    def test_write_failure_returns_false(self, store, monkeypatch) -> None:
        """写入异常（如目录只读）→ False 而非抛异常。"""
        world_id = "l" * 32
        monkeypatch.setattr(
            "ascend.save.lineage.atomic_write",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        assert store.write(world_id, {}) is False
