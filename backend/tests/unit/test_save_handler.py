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
        """无存档时返回空列表（current_world_id 恒存在）。"""
        resp = handlers["save_list"](_req("save_list"))
        assert resp["payload"]["worlds"] == []
        assert resp["payload"]["snapshots"] == []
        assert resp["payload"]["current_world_id"] == ""

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

    def test_list_carries_lineage_fields(self, manager, handlers):
        """快照条目含血缘字段，世界摘要含 live_origin（时间线分叉数据）。"""
        world_id = manager.create_world("世界", seed=1).world_id
        manager.write_state(world_id, {"clock": {"time": 300}})
        snap = manager.create_snapshot(world_id, suffix="manual")
        resp = handlers["save_list"](_req("save_list"))
        payload = resp["payload"]
        assert payload["worlds"][0]["live_origin"] == snap, "活目录来源 = 最新快照"
        s = payload["snapshots"][0]
        assert s["file"] == snap
        assert s["parent"] == ""
        assert s["game_time"] == 300
        assert s["seq"] == 0, "血缘权威排序键随条目下发"

    def test_list_carries_legacy_seq_migration(self, manager, handlers):
        """旧档（血缘无 seq）列表时经迁移合成 seq，排序键仍可用。"""
        world_id = manager.create_world("世界", seed=1).world_id
        snap_a = manager.create_snapshot(world_id, suffix="manual")
        snap_b = manager.create_snapshot(world_id, suffix="manual")
        lineage = manager.snapshot_lineage(world_id)
        # 手工剥掉 seq 并改写 saved_at，模拟旧版血缘文件（a 比 b 早）
        for entry in lineage["snapshots"].values():
            entry.pop("seq", None)
        lineage["snapshots"][snap_a]["saved_at"] = 1.0
        with open(manager.lineage_path(world_id), "w", encoding="utf-8") as f:
            import json
            json.dump(lineage, f, ensure_ascii=False, indent=2)
        resp = handlers["save_list"](_req("save_list"))
        seqs = {s["file"]: s["seq"] for s in resp["payload"]["snapshots"]}
        assert seqs[snap_a] == 0, "旧条目按 saved_at 顺序合成"
        assert seqs[snap_b] == 1

    def test_list_reports_current_world(self, manager):
        """current_world_id = 引擎当前加载的世界（最后进入标注数据源）。"""
        world_id = manager.create_world("世界", seed=1).world_id

        class _Engine:
            """最小引擎替身：仅暴露当前加载世界。"""

        _Engine.world_id = world_id

        handlers = make_save_handlers(manager, game_engine=_Engine())
        resp = handlers["save_list"](_req("save_list"))
        assert resp["payload"]["current_world_id"] == world_id


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
        """seed 缺省为 0 → 创建时随机化并写入 manifest（P0 回归）。

        旧行为：seed=0 保留到首次进入才随机化，导致 secrets_blob
        身份与 manifest 失配、state 加解密失败。
        """
        resp = handlers["save_create"](_req("save_create", {"name": "x"}))
        manifest = manager.get_manifest(resp["payload"]["world_id"])
        assert 1 <= manifest.seed <= 2**31 - 1
        manager.write_state(manifest.world_id, {"clock": {"time": 3}})
        assert manager.read_state(manifest.world_id)["clock"]["time"] == 3

    def test_duplicate_name_rejected(self, manager, handlers):
        """重名创建经协议层拒绝（错误信息含名称）。"""
        handlers["save_create"](_req("save_create", {"name": "重名档", "seed": 1}))
        with pytest.raises(ValueError, match="重名档"):
            handlers["save_create"](_req("save_create", {"name": "重名档", "seed": 2}))

    def test_duplicate_rename_rejected(self, manager, handlers):
        """重命名为已有名称经协议层拒绝。"""
        a = manager.create_world("世界A", seed=1).world_id
        manager.create_world("世界B", seed=2)
        with pytest.raises(ValueError, match="世界B"):
            handlers["save_rename"](_req("save_rename", {
                "world_id": a, "name": "世界B",
            }))


class TestSaveSnapshot:
    """手动快照。"""

    class _RecordingEngine:
        """记录 snapshot_current 调用并真实落盘的最小引擎替身。

        模拟真实引擎语义：非当前加载世界时直接打包活目录。
        """

        def __init__(self, manager, world_id: str | None = None) -> None:
            self._manager = manager
            self.world_id = world_id
            self.calls: list[tuple] = []

        def snapshot_current(self, **kwargs) -> str:
            self.calls.append(kwargs)
            return self._manager.create_snapshot(
                kwargs["world_id"], suffix=kwargs["suffix"],
            )

    def test_creates_snapshot(self, manager, handlers):
        """为世界创建 manual 快照并返回文件名。"""
        world_id = manager.create_world("世界", seed=1).world_id
        resp = handlers["save_snapshot"](_req("save_snapshot", {"world_id": world_id}))
        assert resp["payload"]["file"].endswith(".ascendsave")

    def test_missing_world_id_rejected(self, handlers):
        """缺 world_id 拒绝。"""
        with pytest.raises(ValueError):
            handlers["save_snapshot"](_req("save_snapshot", {}))

    def test_unknown_world_rejected(self, manager, handlers):
        """目标存档不存在拒绝（不落盘）。"""
        with pytest.raises(Exception):
            handlers["save_snapshot"](_req("save_snapshot", {"world_id": "nope"}))

    def test_routes_to_loaded_world_via_engine(self, manager):
        """引擎加载目标世界：走 snapshot_current（flush+checkpoint 路径）。"""
        world_id = manager.create_world("世界", seed=1).world_id
        engine = self._RecordingEngine(manager, world_id=world_id)
        handlers = make_save_handlers(manager, engine)
        resp = handlers["save_snapshot"](_req("save_snapshot", {"world_id": world_id}))
        assert engine.calls == [{"world_id": world_id, "suffix": "manual"}]
        assert resp["payload"]["file"].endswith(".ascendsave")

    def test_routes_idle_world_to_plain_snapshot(self, manager):
        """目标非当前加载世界（服务模式）：直接打包，不误用当前世界。

        回归：旧实现忽略 payload 的 world_id，engine 可用时总是
        快照当前加载世界（服务模式下报"当前无存档位"）。
        """
        idle_id = manager.create_world("闲置档", seed=1).world_id
        loaded_id = manager.create_world("当前档", seed=2).world_id
        engine = self._RecordingEngine(manager, world_id=loaded_id)
        handlers = make_save_handlers(manager, engine)
        resp = handlers["save_snapshot"](_req("save_snapshot", {"world_id": idle_id}))
        # 引擎路径收到目标 world_id（snapshot_current 内部处理非当前世界）
        assert engine.calls == [{"world_id": idle_id, "suffix": "manual"}]
        # 快照落在目标世界目录（而非当前世界）
        assert len(manager.list_snapshots(idle_id)) == 1
        assert len(manager.list_snapshots(loaded_id)) == 0


class TestSaveLoad:
    """读档（异步置位）。"""

    class _FakeEngine:
        """最小引擎替身：实现 request_load（校验并置位）。"""

        def __init__(self) -> None:
            self._pending_load = None

        def request_load(
            self, world_id: str | None = None, snapshot: str | None = None,
        ) -> None:
            if self._pending_load is not None:
                raise ValueError("已有读档请求在处理中")
            self._pending_load = (world_id, snapshot)

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

    def test_load_snapshot_with_world_id_override(self, manager, handlers):
        """world_id + snapshot 同时给出 = 目标覆盖回滚（复制档场景）。"""
        world_id = manager.create_world("世界", seed=1).world_id
        manager.create_snapshot(world_id)
        new_id = manager.export_world(world_id)
        filename = manager.list_snapshots(new_id)[0]["file"]
        engine = self._FakeEngine()
        handlers = make_save_handlers(manager, engine)
        resp = handlers["save_load"](_req("save_load", {
            "world_id": new_id, "snapshot": filename,
        }))
        assert engine._pending_load == (new_id, filename)
        assert resp["payload"]["world_id"] == new_id

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
