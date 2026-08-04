"""存档网络处理程序单元测试 — 状态通道的存档请求。

覆盖 ascend/net/handlers/save_handler.py。
"""

import pytest

from ascend.save.manager import SaveManager
from ascend.net.handlers.save_handler import make_save_handlers


@pytest.fixture()
def manager(tmp_path) -> SaveManager:
    return SaveManager(root=str(tmp_path / "saves"))


@pytest.fixture()
def handlers(manager):
    return make_save_handlers(manager)


def _req(request_type: str, payload: dict | None = None) -> dict:
    return {"type": "request", "request_type": request_type,
            "seq": 1, "payload": payload or {}}


class TestSaveList:
    """存档列表。"""

    def test_empty_root(self, handlers):
        """无存档时返回空列表。"""
        resp = handlers["save_list"](_req("save_list"))
        assert resp["payload"] == {"worlds": [], "snapshots": []}

    def test_lists_world_and_snapshots(self, manager, handlers):
        """列表含世界摘要与快照归属。"""
        world_id = manager.create_world("世界", seed=1).world_id
        manager.create_snapshot(world_id)
        resp = handlers["save_list"](_req("save_list"))
        payload = resp["payload"]
        assert len(payload["worlds"]) == 1
        assert payload["worlds"][0]["world_id"] == world_id
        assert payload["worlds"][0]["name"] == "世界"
        assert payload["worlds"][0]["snapshot_count"] == 1
        assert payload["snapshots"][0]["world_id"] == world_id


class TestSaveCreate:
    """创建存档位。"""

    def test_creates_world(self, manager, handlers):
        """创建成功并返回 world_id。"""
        resp = handlers["save_create"](_req("save_create", {"name": "新世界", "seed": 42}))
        world_id = resp["payload"]["world_id"]
        assert world_id
        assert manager.get_manifest(world_id).seed == 42

    def test_empty_name_rejected(self, handlers):
        """空名称拒绝。"""
        with pytest.raises(ValueError):
            handlers["save_create"](_req("save_create", {"name": "  "}))

    def test_default_random_seed(self, manager, handlers):
        """seed 缺省为 0（引擎随机）。"""
        resp = handlers["save_create"](_req("save_create", {"name": "x"}))
        assert manager.get_manifest(resp["payload"]["world_id"]).seed == 0


class TestSaveSnapshot:
    """手动快照。"""

    def test_creates_snapshot(self, manager, handlers):
        """为世界创建 manual 快照并返回文件名。"""
        world_id = manager.create_world("世界", seed=1).world_id
        resp = handlers["save_snapshot"](_req("save_snapshot", {"world_id": world_id}))
        assert resp["payload"]["file"].endswith(".ascendsave")

    def test_missing_world_id_rejected(self, handlers):
        """缺 world_id 拒绝。"""
        with pytest.raises(ValueError):
            handlers["save_snapshot"](_req("save_snapshot", {}))


class TestSaveLoad:
    """读档（异步置位）。"""

    class _FakeEngine:
        """最小引擎替身：记录 pending_load。"""

        def __init__(self) -> None:
            self._pending_load = None

    def test_load_world_sets_pending(self, manager, handlers):
        """save_load 置位引擎读档请求并返回目标。"""
        world_id = manager.create_world("世界", seed=1).world_id
        engine = self._FakeEngine()
        handlers = make_save_handlers(manager, engine)
        resp = handlers["save_load"](_req("save_load", {"world_id": world_id}))
        assert engine._pending_load == (world_id, None)
        assert resp["payload"]["world_id"] == world_id

    def test_load_snapshot_sets_pending(self, manager, handlers):
        """快照回滚置位（snapshot 参数）。"""
        world_id = manager.create_world("世界", seed=1).world_id
        filename = manager.create_snapshot(world_id)
        engine = self._FakeEngine()
        handlers = make_save_handlers(manager, engine)
        resp = handlers["save_load"](_req("save_load", {"snapshot": filename}))
        assert engine._pending_load == (None, filename)

    def test_unknown_world_rejected(self, tmp_path, handlers):
        """不存在的 world_id 拒绝（不置位）。"""
        engine = self._FakeEngine()
        handlers = make_save_handlers(
            SaveManager(root=str(tmp_path / "empty")), engine,
        )
        with pytest.raises(Exception):
            handlers["save_load"](_req("save_load", {"world_id": "ghost"}))

    def test_missing_target_rejected(self, handlers):
        """既无 world_id 也无 snapshot 拒绝。"""
        with pytest.raises(ValueError):
            handlers["save_load"](_req("save_load", {}))

    def test_without_engine_rejected(self, tmp_path):
        """无引擎时读档被拒绝（先校验存档存在性）。"""
        manager = SaveManager(root=str(tmp_path / "s"))
        world_id = manager.create_world("世界", seed=1).world_id
        h = make_save_handlers(manager)["save_load"]
        with pytest.raises(ValueError):
            h(_req("save_load", {"world_id": world_id}))

    def test_busy_engine_rejected(self, manager, handlers):
        """已有待处理读档时拒绝新请求。"""
        world_id = manager.create_world("世界", seed=1).world_id
        engine = self._FakeEngine()
        engine._pending_load = (world_id, None)
        handlers = make_save_handlers(manager, engine)
        with pytest.raises(ValueError):
            handlers["save_load"](_req("save_load", {"world_id": world_id}))


class TestManageOps:
    """重命名/删除/复制。"""

    def test_rename(self, manager, handlers):
        world_id = manager.create_world("旧名", seed=1).world_id
        resp = handlers["save_rename"](_req("save_rename", {
            "world_id": world_id, "name": "新名",
        }))
        assert resp["payload"]["name"] == "新名"
        assert manager.get_manifest(world_id).name == "新名"

    def test_delete(self, manager, handlers):
        world_id = manager.create_world("要删", seed=1).world_id
        handlers["save_delete"](_req("save_delete", {"world_id": world_id}))
        with pytest.raises(Exception):
            manager.get_manifest(world_id)

    def test_export(self, manager, handlers):
        world_id = manager.create_world("要复制", seed=7).world_id
        resp = handlers["save_export"](_req("save_export", {"world_id": world_id}))
        new_id = resp["payload"]["world_id"]
        assert new_id != world_id
        assert manager.get_manifest(new_id).seed == 7
