"""LineageStore 单元测试 — 血缘文件读写与条目维护原语。

覆盖 ascend/save/lineage.py：签名格式的 load 验签（严格模式：
无签名/篡改/无密钥 = 损坏）、get 的归一化、record_snapshot 的
parent/seq 语义、write 失败契约。
"""

import json
import os

import pytest

from ascend.save.crypto import SaveKeys
from ascend.save.lineage import LineageStore, LINEAGE_FILE


@pytest.fixture()
def keys() -> SaveKeys:
    """固定的世界签名密钥。"""
    return SaveKeys.generate()


@pytest.fixture()
def store(tmp_path, keys) -> LineageStore:
    """带密钥提供者的血缘存储（SaveManager 注入同构）。"""
    root = str(tmp_path / "saves")

    def _keys(_world_id: str) -> SaveKeys:
        return keys

    return LineageStore(root=root, keys_provider=_keys)


@pytest.fixture()
def store_no_keys(tmp_path) -> LineageStore:
    """无密钥提供者的血缘存储（读写全部降级）。"""
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
    """load：缺失/损坏/无签名/篡改/验签通过的区分（严格模式）。"""

    def test_load_missing_returns_none(self, store) -> None:
        """文件不存在 → None（区分「世界尚无血缘」与「损坏」）。"""
        assert store.load("a" * 32) is None

    def test_load_corrupt_returns_none(self, store) -> None:
        """JSON 损坏 → None（按损坏处理，防反向对账误删）。"""
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

    def test_load_unsigned_returns_none(self, store) -> None:
        """历史无签名格式（合法 JSON）→ None（严格：不兼容、不信任）。"""
        world_id = "d" * 32
        _write_lineage_file(
            store, world_id, {"live_origin": "", "snapshots": {}},
        )
        assert store.load(world_id) is None

    def test_load_tampered_data_returns_none(self, store) -> None:
        """data 被改（签名不匹配）→ None。"""
        world_id = "e" * 32
        _world_dir_ready(store, world_id)
        lineage = {"live_origin": "@x-auto.ascendsave", "snapshots": {}}
        assert store.write(world_id, lineage)
        path = store.lineage_path(world_id)
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        payload["data"]["live_origin"] = "@hacked-auto.ascendsave"
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        assert store.load(world_id) is None

    def test_load_tampered_sig_returns_none(self, store) -> None:
        """sig 被改 → None。"""
        world_id = "f" * 32
        _world_dir_ready(store, world_id)
        assert store.write(world_id, {"live_origin": "", "snapshots": {}})
        path = store.lineage_path(world_id)
        with open(path, encoding="utf-8") as f:
            payload = json.load(f)
        payload["sig"] = "A" * 44
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        assert store.load(world_id) is None

    def test_load_without_keys_returns_none(self, store_no_keys) -> None:
        """密钥不可用 → 无法验签 → None（宁缺勿删）。"""
        world_id = "g" * 32
        store = store_no_keys
        _world_dir_ready(store, world_id)
        _write_lineage_file(
            store, world_id, {"data": {"live_origin": ""}, "sig": "x"},
        )
        assert store.load(world_id) is None

    def test_load_verifies_signed_file(self, store) -> None:
        """store 写入的签名文件可验签读回。"""
        world_id = "h" * 32
        _world_dir_ready(store, world_id)
        lineage = {"live_origin": "@x.ascendsave", "snapshots": {}}
        assert store.write(world_id, lineage)
        assert store.load(world_id) == lineage


class TestGet:
    """get：空血缘默认值 + 字段归一化（基于验签通过的签名文件）。"""

    def test_get_empty_default(self, store) -> None:
        """无文件 → 空血缘默认结构（不改动调用方期望的字段）。"""
        assert store.get("i" * 32) == {"live_origin": "", "snapshots": {}}

    def test_get_empty_when_unverifiable(self, store) -> None:
        """无签名/损坏 → 空血缘（调用方降级路径）。"""
        world_id = "j" * 32
        _write_lineage_file(store, world_id, {"live_origin": "@x", "snapshots": {}})
        assert store.get(world_id) == {"live_origin": "", "snapshots": {}}

    def test_get_normalizes_missing_fields(self, store) -> None:
        """缺字段补齐；snapshots 非 dict 归一化为空。"""
        world_id = "k" * 32
        _world_dir_ready(store, world_id)
        assert store.write(world_id, {"snapshots": "not-a-dict"})
        data = store.get(world_id)
        assert data["live_origin"] == ""
        assert data["snapshots"] == {}

    def test_get_preserves_existing_data(self, store) -> None:
        """既有血缘内容保留。"""
        world_id = "l" * 32
        _world_dir_ready(store, world_id)
        original = {
            "live_origin": "@x-auto.ascendsave",
            "snapshots": {"@x-auto.ascendsave": {"parent": "", "seq": 0}},
        }
        assert store.write(world_id, original)
        assert store.get(world_id) == original


class TestRecordSnapshot:
    """record_snapshot：parent/live_origin/seq 语义。"""

    def test_record_first_snapshot(self, store) -> None:
        """首个条目：parent=""、seq=0、live_origin 指向新文件。"""
        world_id = "m" * 32
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
        world_id = "n" * 32
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
        world_id = "o" * 32
        monkeypatch.setattr(store, "write", lambda w, d: False)
        assert store.record_snapshot(world_id, "@1.ascendsave", 0, 0.0) is False


class TestWrite:
    """write：签名写入与失败契约。"""

    def test_write_persists_lineage(self, store) -> None:
        """写入后 load 可验签读回。"""
        world_id = "p" * 32
        _world_dir_ready(store, world_id)
        lineage = {"live_origin": "@1.ascendsave", "snapshots": {}}
        assert store.write(world_id, lineage) is True
        assert store.load(world_id) == lineage

    def test_write_without_keys_returns_false(self, store_no_keys) -> None:
        """密钥不可用 → 不写无签名文件，返回 False。"""
        store = store_no_keys
        world_id = "q" * 32
        _world_dir_ready(store, world_id)
        assert store.write(world_id, {"live_origin": "", "snapshots": {}}) is False
        assert not os.path.isfile(store.lineage_path(world_id))

    def test_write_failure_returns_false(self, store, monkeypatch) -> None:
        """写入异常（如目录只读）→ False 而非抛异常。"""
        world_id = "r" * 32
        monkeypatch.setattr(
            "ascend.save.lineage.atomic_write",
            lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")),
        )
        assert store.write(world_id, {}) is False


