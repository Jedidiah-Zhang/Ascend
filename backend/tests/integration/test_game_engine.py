"""GameEngine 集成测试 — 验证引擎的启动/停止生命周期。

通过创建真实的 GameEngine 实例，测试其管理
TCP 服务器、WorldGenerator 和 MessageDispatcher 的能力。
"""

import os
import time
import pytest

from ascend.game import GameEngine


# GameEngine 默认端口 9081，确保与 test_net.py 的 19081 不冲突
GAME_ENGINE_PORT = 9081

# 原始初始区块生成（模块 import 时捕获——_patch_fast_worldgen 会
# 整体替换成 no-op，集成测试需借真实实现 + 缩小半径）
_REAL_GENERATE_INITIAL = GameEngine._generate_initial_chunks


def _patch_fast_worldgen(monkeypatch):
    """快路径世界生成：小大陆 + 跳过初始区块，避免真实生成耗时。

    只替换生成来源，生命周期/网络/存档流程全走真实路径。
    """
    from ascend.game import GameEngine
    from ascend.space.continent import ContinentGenerator, ContinentParams

    def _fast_continent(gen, *args, **kwargs):
        return ContinentGenerator(
            seed=gen._seed,
            params=ContinentParams(
                width_km=6, height_km=4, sample_resolution=200,
            ),
        ).generate()

    monkeypatch.setattr(
        GameEngine, "_generate_initial_chunks", lambda self, continent: None,
    )
    monkeypatch.setattr(
        "ascend.space.generator.WorldGenerator.ensure_continent",
        _fast_continent,
    )


def _state_at(time: int) -> dict:
    """最小可写状态（时钟 + 玩家，时间可辨）。"""
    return {
        "clock": {"time": time, "speed": 1.0, "paused": False},
        "player": {"entity_id": "e1", "x": float(time), "y": float(time)},
        "archive_max_timestamp": 0,
    }


class TestGameEngine:
    """GameEngine 生命周期测试。"""

    # ── T8: 完整生命周期 ───────────────────────────────────────────────

    def test_T8_full_lifecycle(self):
        """GameEngine start/stop 完整生命周期。

        Arrange:
            创建 GameEngine(seed=42)。
        Act:
            调用 start() → 验证子系统 → 调用 stop()。
        Assert:
            start 后 engine 内部组件已创建，stop 后已清理。
        """
        engine = GameEngine(seed=42)

        try:
            # Act: 启动
            engine.start()

            # Assert: 子系统已创建
            assert engine._running.is_set()
            assert engine.world_gen is not None, "WorldGenerator 未创建"
            assert engine.server is not None, "GameServer 未创建"
            assert engine.dispatcher is not None, "MessageDispatcher 未创建"
            assert engine.server.is_running is True

            # 验证 handlers 已注册
            handler_keys = list(engine.dispatcher._handlers.keys())
            assert "get_chunks" in handler_keys

            # 等待 tick 循环启动
            time.sleep(0.1)

        finally:
            # Act: 停止
            engine.stop()

        # Assert: 已清理
        assert not engine._running.is_set()
        assert engine.server is None
        assert engine.world_gen is None

    # ── T9: 幂等 start ─────────────────────────────────────────────────

    def test_T9_idempotent_start(self):
        """重复调用 start() 不报错（幂等）。

        Arrange:
            GameEngine 已启动。
        Act:
            再次调用 start()。
        Assert:
            不抛出异常，状态不变。
        """
        engine = GameEngine(seed=1)

        try:
            engine.start()

            # 幂等调用
            engine.start()  # 不应抛出异常

            assert engine._running.is_set()
            assert engine.server is not None
            assert engine.dispatcher is not None

        finally:
            engine.stop()

    # ── T10: 幂等 stop ─────────────────────────────────────────────────

    def test_T10_idempotent_stop(self):
        """重复调用 stop() 不报错（幂等）。

        Arrange:
            GameEngine 已启动并停止。
        Act:
            再次调用 stop()。
        Assert:
            不抛出异常。
        """
        engine = GameEngine(seed=1)

        engine.start()
        engine.stop()

        # 幂等调用 — 不应抛出异常
        engine.stop()

        assert not engine._running.is_set()

    # ── 辅助测试：start 后引擎接受连接 ─────────────────────────────────

    def test_engine_accepts_connection(self):
        """引擎启动后，可通过 TCP 连接（握手后收发消息）。"""
        import socket
        from tests.integration.test_net import send_frame, recv_frame, handshake

        engine = GameEngine(seed=42)

        try:
            engine.start()
            time.sleep(0.3)

            # 连接引擎的 TCP 服务器
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(3.0)
            try:
                sock.connect(("127.0.0.1", GAME_ENGINE_PORT))

                # 等待 accept 线程处理连接
                for _ in range(10):
                    if engine.server.client_count >= 1:
                        break
                    time.sleep(0.1)
                assert engine.server.client_count >= 1

                # 握手：token 由引擎服务器持有
                ack = handshake(sock, engine.server)
                assert ack is not None and ack["type"] == "hello_ack"

                # 发送一条请求，应得到 error（因为没有该 handler）
                msg = {
                    "type": "request",
                    "request_type": "nonexistent",
                    "seq": 1,
                    "payload": {},
                }
                send_frame(sock, msg)

                response = recv_frame(sock, timeout=2.0)
                assert response is not None
                assert response["type"] == "error"
                assert "unknown request_type" in response["error"]

            finally:
                sock.close()
                time.sleep(0.1)

        finally:
            engine.stop()

    # ── 辅助测试：stop 后服务器断开 ───────────────────────────────────

    def test_engine_stops_server(self):
        """stop 后 TCP 端口释放，新连接被拒绝。"""
        import socket

        engine = GameEngine(seed=42)
        engine.start()
        time.sleep(0.3)

        # 连接以确认引擎在运行
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        try:
            sock.connect(("127.0.0.1", GAME_ENGINE_PORT))

            # 等待 accept 线程处理连接
            for _ in range(10):
                if engine.server.client_count >= 1:
                    break
                time.sleep(0.1)
            assert engine.server.client_count >= 1
        finally:
            sock.close()

        engine.stop()
        time.sleep(0.3)

        # 停止后连接应被拒绝
        sock2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock2.settimeout(2.0)
        with pytest.raises((ConnectionRefusedError, OSError)):
            sock2.connect(("127.0.0.1", GAME_ENGINE_PORT))
        sock2.close()


class TestWorldProcessEntry:
    """世界进程入口（run_server --world-id 对应 load_world）。

    进程模型：一进程一模式，进入世界/回滚 = 新进程调用 load_world。
    测试以 stop() 模拟进程切换（真实流程由前端进程切换完成）。
    """

    def test_load_world_restores_world(self, monkeypatch):
        """菜单模式 → load_world：世界构建、处理程序注册、存档恢复。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            assert "get_chunks" not in engine.dispatcher._handlers
            assert "save_list" in engine.dispatcher._handlers

            world_id = engine.save_manager.create_world("测试世界", seed=7).world_id
            engine.load_world(world_id=world_id)

            assert engine.world_id == world_id
            assert engine.seed == 7, "存档 seed 应恢复"
            assert engine.birth_chunk is not None
            assert "get_chunks" in engine.dispatcher._handlers
            assert "player_move" in engine.dispatcher._handlers
            assert "save_list" in engine.dispatcher._handlers
        finally:
            engine.stop()

    def test_load_world_without_snapshot_creates_no_nodes(self, monkeypatch):
        """普通进入（无快照）不产生任何快照节点。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            world_id = engine.save_manager.create_world("普通进入", seed=7).world_id
            engine.load_world(world_id=world_id)
            assert engine.save_manager.list_snapshots(world_id) == []
        finally:
            engine.stop()

    def test_load_world_invalid_target_raises(self, monkeypatch):
        """存档不存在：load_world 抛错（run_server 以非零码退出）。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            with pytest.raises(Exception):
                engine.load_world(world_id="no-such-world")
        finally:
            engine.stop()

    def test_load_world_rollback_requires_world_id(self, monkeypatch):
        """回滚必须指定 world_id（进入语义需要目标世界定位血缘）。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            world_id = engine.save_manager.create_world("回滚", seed=7).world_id
            with pytest.raises(ValueError, match="world_id"):
                engine.load_world(snapshot="whatever.ascendsave")
            assert engine.save_manager.get_manifest(world_id).world_id == world_id
        finally:
            engine.stop()

    def test_enter_manual_snapshot_forks_at_manual_node(self, monkeypatch):
        """进入手动档：冻结离开记录，在手动节点下游开启新当前记录。

        语义：进入手动档 = 分叉点——离开的当前记录原地冻结，
        新 auto 当前记录开在目标手动档下游，分叉发生在手动节点处。
        验证链：M1→M2（保存产生 M2→rec 当前记录）→ 进入 M1（rec 冻结、
        M1 下游新开记录，M1 分叉）→ 继续玩保存 M3（parent=M1）。
        """
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            mgr = engine.save_manager
            world_id = mgr.create_world("回滚世界", seed=7).world_id
            engine.load_world(world_id=world_id)
            assert engine.world_id == world_id

            mgr.write_state(world_id, _state_at(100))
            snap_m1 = mgr.create_snapshot(world_id, suffix="manual", game_time=100)
            mgr.write_state(world_id, _state_at(200))
            snap_m2 = mgr.create_snapshot(world_id, suffix="manual", game_time=200)
            lineage = mgr.snapshot_lineage(world_id)
            assert lineage["snapshots"][snap_m2]["parent"] == snap_m1, "连续保存应串链"
            rec_2 = lineage["live_origin"]
            assert rec_2.endswith("-auto.ascendsave"), "保存后当前记录为 auto"
            assert lineage["snapshots"][rec_2]["parent"] == snap_m2, \
                "当前记录开在最新手动档下游"

            # 进入手动 M1：冻结 rec_2（M2 线记录保留），M1 下游开新记录
            engine.load_world(world_id=world_id, snapshot=snap_m1)
            lineage = mgr.snapshot_lineage(world_id)
            assert rec_2 in lineage["snapshots"], "离开的记录原地保留"
            assert lineage["snapshots"][rec_2]["parent"] == snap_m2, \
                "冻结不改变血缘位置"
            rec_3 = lineage["live_origin"]
            assert rec_3 != rec_2 and rec_3.endswith("-auto.ascendsave")
            assert lineage["snapshots"][rec_3]["parent"] == snap_m1, \
                "新当前记录开在目标手动档下游"
            children = {f for f, e in lineage["snapshots"].items()
                        if e["parent"] == snap_m1}
            assert children == {snap_m2, rec_3}, "分叉发生在手动节点 M1 处"

            # 继续玩并保存 → rec_3 晋升为 M3（parent=M1），新开 rec_4
            mgr.write_state(world_id, _state_at(150))
            snap_m3 = mgr.create_snapshot(world_id, suffix="manual", game_time=150)
            lineage = mgr.snapshot_lineage(world_id)
            assert rec_3 not in lineage["snapshots"], "auto 应晋升为手动而非残留"
            assert lineage["snapshots"][snap_m3]["parent"] == snap_m1, \
                "晋升保留原 parent（M1 下分叉）"
            assert lineage["live_origin"].endswith("-auto.ascendsave")
        finally:
            engine.stop()

    def test_multi_jump_preserves_all_branches(self, monkeypatch):
        """多次跨分支跳转：所有分支（auto 记录 + 手动链）血缘完整保留。

        防护：离开线仅剩冻结的 auto 记录——若血缘丢失或前端不可见，
        分支即"消失"。验证 auto 记录持续存在、恒为叶子且父链正确。
        """
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            mgr = engine.save_manager
            world_id = mgr.create_world("多跳世界", seed=7).world_id
            engine.load_world(world_id=world_id)

            # M1 → M2 手动链（当前记录 rec_2 在 M2 下游）
            mgr.write_state(world_id, _state_at(100))
            snap_m1 = mgr.create_snapshot(world_id, suffix="manual", game_time=100)
            mgr.write_state(world_id, _state_at(200))
            snap_m2 = mgr.create_snapshot(world_id, suffix="manual", game_time=200)

            # 进入 M1：rec_2 冻结，M1 下游新开 rec_3；M1 分支玩到 150 保存 M3
            engine.load_world(world_id=world_id, snapshot=snap_m1)
            mgr.write_state(world_id, _state_at(150))
            snap_m3 = mgr.create_snapshot(world_id, suffix="manual", game_time=150)

            # 进入 M2：rec_3 晋升后的当前记录冻结，M2 下游新开记录
            engine.load_world(world_id=world_id, snapshot=snap_m2)

            lineage = mgr.snapshot_lineage(world_id)
            entries = lineage["snapshots"]
            # 全部手动节点都在
            assert snap_m1 in entries and snap_m2 in entries and snap_m3 in entries
            autos = [f for f in entries if f.endswith("-auto.ascendsave")]
            assert len(autos) == 3, "冻结记录+新开记录应共存，实际: %s" % autos
            # auto 节点恒为叶子且 parent 恒为手动节点（永无下游）
            for a in autos:
                assert not any(e["parent"] == a for e in entries.values()), \
                    f"auto 节点不得有下游: {a}"
                assert entries[a]["parent"] in (snap_m1, snap_m2, snap_m3), \
                    f"auto 记录应挂在手动节点下: {a}"
            # 各分支记录在正确位置
            parents = {entries[a]["parent"]: a for a in autos}
            assert parents.get(snap_m2), "M2 线记录应保留（冻结的 rec_2）"
            assert parents.get(snap_m3), "M3 线记录应保留（冻结的当前记录）"
            assert lineage["live_origin"].endswith("-auto.ascendsave"), \
                "当前记录恒为 auto"
        finally:
            engine.stop()

    def test_enter_auto_snapshot_continues_in_place(self, monkeypatch):
        """进入 auto 档 = 继续：不新建任何节点，目标成为当前记录。

        语义：auto 节点是当前线的滚动记录——进入它不产生节点、
        不产生下游；保存时它原地晋升为 manual（parent/seq 保留）。
        """
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            mgr = engine.save_manager
            world_id = mgr.create_world("自动档续玩", seed=7).world_id
            engine.load_world(world_id=world_id)

            mgr.write_state(world_id, _state_at(100))
            snap_m1 = mgr.create_snapshot(world_id, suffix="manual", game_time=100)
            mgr.write_state(world_id, _state_at(200))
            snap_m2 = mgr.create_snapshot(world_id, suffix="manual", game_time=200)
            rec_2 = mgr.snapshot_lineage(world_id)["live_origin"]

            # 进入手动 M1 → M1 下游新开当前记录 rec_3
            engine.load_world(world_id=world_id, snapshot=snap_m1)
            lineage = mgr.snapshot_lineage(world_id)
            rec_3 = lineage["live_origin"]
            assert rec_3 != rec_2

            # 进入 auto rec_3：节点数不变，rec_3 成为当前记录
            n_before = len(lineage["snapshots"])
            engine.load_world(world_id=world_id, snapshot=rec_3)
            lineage = mgr.snapshot_lineage(world_id)
            assert len(lineage["snapshots"]) == n_before, "进入 auto 不新建节点"
            assert lineage["live_origin"] == rec_3, "auto 目标成为当前记录"

            # 从 auto 保存 → rec_3 晋升为 M3（parent=M1 保留），新开 rec_4
            seq_before = lineage["snapshots"][rec_3]["seq"]
            mgr.write_state(world_id, _state_at(150))
            snap_m3 = mgr.create_snapshot(world_id, suffix="manual", game_time=150)
            lineage = mgr.snapshot_lineage(world_id)
            assert rec_3 not in lineage["snapshots"], "auto 应晋升为手动而非残留"
            assert lineage["snapshots"][snap_m3]["parent"] == snap_m1, \
                "晋升保留原 parent"
            assert lineage["snapshots"][snap_m3]["seq"] == seq_before, \
                "晋升保留原 seq（出生序）"
            rec_4 = lineage["live_origin"]
            assert rec_4.endswith("-auto.ascendsave") and rec_4 != rec_3, \
                "保存后新开当前记录"
            assert lineage["snapshots"][rec_4]["parent"] == snap_m3
        finally:
            engine.stop()

    def test_leave_auto_into_manual_freezes_in_place(self, monkeypatch):
        """从 auto 位置直接进入手动档：记录原地冻结，分叉在手动档。

        语义：离开 auto 线不产生新节点——把离开时刻的活状态刷进
        该 auto 节点（冻结），并在手动档处形成分叉（冻结记录 vs
        新当前记录），树中不堆积重复节点。
        """
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            mgr = engine.save_manager
            world_id = mgr.create_world("冻结世界", seed=7).world_id
            engine.load_world(world_id=world_id)

            mgr.write_state(world_id, _state_at(100))
            snap_m1 = mgr.create_snapshot(world_id, suffix="manual", game_time=100)
            mgr.write_state(world_id, _state_at(200))
            snap_m2 = mgr.create_snapshot(world_id, suffix="manual", game_time=200)
            # 进入 M1 → M1 下游新开当前记录 rec_3
            engine.load_world(world_id=world_id, snapshot=snap_m1)
            rec_3 = mgr.snapshot_lineage(world_id)["live_origin"]

            # 进入 auto rec_3，玩到 150，不保存直接跳入手动 M2
            engine.load_world(world_id=world_id, snapshot=rec_3)
            mgr.write_state(world_id, _state_at(150))
            engine.load_world(world_id=world_id, snapshot=snap_m2)

            lineage = mgr.snapshot_lineage(world_id)
            assert rec_3 in lineage["snapshots"], "离开的记录应原地保留（冻结）"
            assert lineage["snapshots"][rec_3]["game_time"] > 100, \
                "冻结应为离开时刻（引擎实际时钟 > 创建时刻 100）"
            assert lineage["snapshots"][rec_3]["parent"] == snap_m1, \
                "冻结不改变血缘位置"
            rec_new = lineage["live_origin"]
            assert rec_new != rec_3 and rec_new.endswith("-auto.ascendsave")
            assert lineage["snapshots"][rec_new]["parent"] == snap_m2, \
                "新当前记录开在 M2 下游（M2 处与旧记录分叉）"
            autos = [f for f in lineage["snapshots"]
                     if f.endswith("-auto.ascendsave")]
            for a in autos:
                assert not any(e["parent"] == a
                               for e in lineage["snapshots"].values()), \
                    f"auto 节点不得有下游: {a}"

            # 展开后活目录应为 M2 内容（冻结与展开互不影响）
            assert mgr.read_state(world_id)["clock"]["time"] >= 200, \
                "展开目标 M2 的内容（200），冻结与展开互不影响"
        finally:
            engine.stop()


class TestSavePulseEndToEnd:
    """保存脉搏端到端（Issue #40）：事件跨重启持久化 + 快照含近期事件。

    _final_pulse() 同步执行完整脉搏（事件 flush → state → chunk），
    确定性模拟保存线程的落盘（不等真实 SAVE_PULSE_INTERVAL）。
    """

    def _publish_chain(self):
        """发布因果链：雨 → 观测 → 行动（observes + caused_by）。"""
        from ascend.world_tree import world_tree, Event, AffectedParty

        world_tree.publish(Event(
            id="rain", timestamp=100, location=(0, 0, None, None),
            initiator_type="system", initiator_id="weather_system",
            event_type="precipitation_start", weight=3,
            affected=[AffectedParty("world", "subject")],
            data={"intensity": 10},
        ))
        world_tree.publish(Event(
            id="obs", timestamp=101, location=(0, 0, None, None),
            initiator_type="npc", initiator_id="npc_1",
            event_type="observation", caused_by=["rain"], observes="rain",
            affected=[AffectedParty("npc_1", "subject")],
            data={"saw": "rain"},
        ))
        world_tree.publish(Event(
            id="action", timestamp=102, location=(0, 0, None, None),
            initiator_type="npc", initiator_id="npc_1",
            event_type="npc_action", caused_by=["obs"],
            affected=[AffectedParty("npc_1", "subject")],
            data={"action": "seek_shelter"},
        ))

    def test_events_persist_across_restart_via_pulse(self, monkeypatch):
        """脉搏 flush 后重启（新进程语义）：事件完整、因果链可追溯。

        崩溃语义：flush 后事件即落盘，重启不丢；丢失窗口 = 脉搏间隔。
        """
        _patch_fast_worldgen(monkeypatch)
        from ascend.world_tree import world_tree

        engine1 = GameEngine(seed=42)
        try:
            engine1.start_service()
            world_id = engine1.save_manager.create_world("脉搏世界", seed=7).world_id
            engine1.load_world(world_id=world_id)
            self._publish_chain()
            engine1._final_pulse()  # 模拟保存脉搏落盘
        finally:
            engine1.stop()

        # 新进程（新引擎实例）读档
        engine2 = GameEngine(seed=42)
        try:
            engine2.start_service()
            engine2.load_world(world_id=world_id)
            for eid in ("rain", "obs", "action"):
                assert world_tree.get_event_by_id(eid) is not None, \
                    f"事件 {eid} 应跨重启可追溯"
            chain = world_tree.graph.get_causal_chain(
                "action", lookup=world_tree.get_event_by_id,
            )
            assert chain == ["rain", "obs"], f"因果链应完整: {chain}"
        finally:
            engine2.stop()

    def test_snapshot_contains_recent_events(self, monkeypatch):
        """快照强一致点：checkpoint 前同步完整脉搏，快照含近期事件。"""
        import shutil
        import tempfile

        from ascend.save import SaveManager
        from ascend.world_tree import world_tree, Event, AffectedParty
        from ascend.world_tree.archive import EventArchive

        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            world_id = engine.save_manager.create_world("快照世界", seed=7).world_id
            engine.load_world(world_id=world_id)
            for i in range(5):
                world_tree.publish(Event(
                    id=f"snap{i}", timestamp=200 + i,
                    location=(0, 0, None, None),
                    initiator_type="system", initiator_id="test",
                    event_type="test",
                    affected=[AffectedParty("t", "subject")],
                ))
            filename = engine.snapshot_current(world_id, suffix="manual")

            # 展开到全新存档根：快照内 events.db 应含近期事件
            new_root = tempfile.mkdtemp()
            try:
                mgr2 = SaveManager(root=new_root)
                mgr2.extract_snapshot(
                    engine.save_manager.snapshot_dir(world_id) + os.sep + filename,
                )
                ar = EventArchive(mgr2.events_db_path(world_id))
                try:
                    for i in range(5):
                        assert ar.query_by_id(f"snap{i}") is not None, \
                            f"快照应含事件 snap{i}"
                finally:
                    ar.close()
            finally:
                shutil.rmtree(new_root, ignore_errors=True)
        finally:
            engine.stop()

    def test_loaded_chunks_persist_across_restart(self, monkeypatch):
        """已加载 chunk（未改动）经脉搏落盘，重启后直接命中免重生成。"""
        from ascend.space import BiomeType, ClimateZone, WeatherParams
        from ascend.space.chunk import ChunkData
        from ascend.space.tile_grid import TileGrid

        _patch_fast_worldgen(monkeypatch)
        engine1 = GameEngine(seed=42)
        try:
            engine1.start_service()
            world_id = engine1.save_manager.create_world("chunk世界", seed=7).world_id
            engine1.load_world(world_id=world_id)

            # 模拟首次加载：clean chunk（未改动）进入缓存
            chunk = ChunkData(
                cx=3, cy=5,
                biome=BiomeType.TEMPERATE_MIXED_FOREST,
                climate_zone=ClimateZone.TEMPERATE_FOREST,
                annual_baseline=WeatherParams(15.0, 800.0, 12.0, 100.0, 60.0, 5.0),
            )
            chunk.generate_tiles(TileGrid())
            engine1.chunk_store.put(chunk)
            assert engine1.chunk_store.flush() == 1, "首次加载落盘"
        finally:
            engine1.stop()

        # 重启（新进程）：库中已有记录，直接命中
        engine2 = GameEngine(seed=42)
        try:
            engine2.start_service()
            engine2.load_world(world_id=world_id)
            assert engine2.chunk_store.contains_tiles(3, 5), "重启直接命中"
            grid = engine2.chunk_store.load_tiles(3, 5)
            assert grid is not None
            assert grid == TileGrid(), "内容一致（确定性生成）"
        finally:
            engine2.stop()


class TestTerrainStatePersistence:
    """地形状态层集成（Issue #37）：settled_day 随存档持久化、
    LRU 淘汰后重载续算、不重放历史。"""

    def _start_world(self, engine, name=None, seed=7):
        import uuid
        engine.start_service()
        world_id = engine.save_manager.create_world(
            name or f"状态世界-{uuid.uuid4().hex[:6]}", seed=seed,
        ).world_id
        engine.load_world(world_id=world_id)
        return world_id

    def _patch_single_chunk(self, monkeypatch):
        """初始生成只出出生点 1 个 chunk（真实生成路径，radius=0）。

        _patch_fast_worldgen 把 _generate_initial_chunks 整体替换成
        no-op（快路径），此处恢复真实实现并借模块级常量缩小半径。
        """
        from ascend.game import GameEngine as GE
        monkeypatch.setattr("ascend.game.INITIAL_CHUNK_RADIUS", 0)
        monkeypatch.setattr(
            GE, "_generate_initial_chunks",
            lambda self, continent: _REAL_GENERATE_INITIAL(self, continent),
        )

    def test_settled_day_persists_across_restart(self, monkeypatch):
        """装配结算到 epoch day 1 → 快进到 day 5 → 重启后 settled_day=5。"""
        from ascend.config import GAME_DAY

        _patch_fast_worldgen(monkeypatch)
        self._patch_single_chunk(monkeypatch)

        engine1 = GameEngine(seed=42)
        try:
            world_id = self._start_world(engine1)
            assert len(engine1.chunk_store) == 1
            (cx, cy), chunk = next(iter(engine1.chunk_store.items()))
            assert chunk.settled_day == 1, "装配即结算到 epoch day 1"

            # skip 快进 4 天（真实日历发布 day_change，skipped=3）→ 缺口结算
            engine1.clock.skip(4 * GAME_DAY)
            assert chunk.settled_day == 5, "快进补结算到 day 5"
            assert engine1.chunk_store.flush() >= 1, "settled_day 落盘"
        finally:
            engine1.stop()

        # 重启（新进程）：库中 settled_day 随 chunk 持久化，不重放历史
        engine2 = GameEngine(seed=42)
        try:
            engine2.start_service()
            engine2.load_world(world_id=world_id)
            saved = engine2.chunk_store.load_tiles_with_day(cx, cy)
            assert saved is not None, "库中保留 engine1 的 chunk"
            assert saved[1] == 5, "读档恢复结算日，不重放历史"
        finally:
            engine2.stop()

    def test_lru_evict_then_reload_continues(self, monkeypatch):
        """LRU 淘汰注销 → map 请求重载 → 续算缺口到当前日。"""
        from ascend.config import GAME_DAY

        _patch_fast_worldgen(monkeypatch)
        self._patch_single_chunk(monkeypatch)

        engine = GameEngine(seed=42)
        try:
            self._start_world(engine)
            assert len(engine.chunk_store) == 1
            (cx, cy), chunk = next(iter(engine.chunk_store.items()))
            assert chunk.settled_day == 1

            # skip 快进 2 天（真实日历）→ 结算到 day 3
            engine.clock.skip(2 * GAME_DAY)
            assert chunk.settled_day == 3

            # 缩小 max_size 强制淘汰出生点 chunk（落盘 + 引擎注销）
            engine.chunk_store._max_size = 1
            from ascend.space import BiomeType, ClimateZone, WeatherParams
            from ascend.space.chunk import ChunkData
            filler = ChunkData(
                cx=cx + 5, cy=cy,
                biome=BiomeType.TEMPERATE_MIXED_FOREST,
                climate_zone=ClimateZone.TEMPERATE_FOREST,
                annual_baseline=WeatherParams(15.0, 800.0, 12.0, 100.0, 60.0, 5.0),
            )
            engine.chunk_store.put(filler)
            assert (cx, cy) not in engine.chunk_store, "LRU 已淘汰"
            assert engine.tile_state_engine._chunks == {}, "引擎同步注销"

            # map 请求重载 → 恢复路径续算到当前日（不重放 [1,3)）
            engine.clock.skip(1 * GAME_DAY)  # 现在 day 4
            response = engine.dispatcher._handlers["get_chunks"]({
                "type": "request", "request_type": "get_chunks",
                "seq": 1,
                "payload": {"chunks": [[cx, cy]]},
            })
            assert response is not None
            assert (cx, cy) in engine.chunk_store, "重载入缓存"
            reloaded = engine.chunk_store.get(cx, cy)
            assert reloaded.settled_day == 4, "重载续算到当前日"
            assert reloaded.settled_day == 4, "不重放历史（起点=持久化结算日）"
        finally:
            engine.stop()
