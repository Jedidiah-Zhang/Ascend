"""GameEngine 集成测试 — 验证引擎的启动/停止生命周期。

通过创建真实的 GameEngine 实例，测试其管理
TCP 服务器、WorldGenerator 和 MessageDispatcher 的能力。
"""

import time
import pytest

from ascend.game import GameEngine


# GameEngine 默认端口 9081，确保与 test_net.py 的 19081 不冲突
GAME_ENGINE_PORT = 9081


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
        """引擎启动后，可通过 TCP 连接。"""
        import socket
        from tests.integration.test_net import send_frame, recv_frame

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


class TestServerWorldDecoupling:
    """服务器与世界观解耦：读档重建不重启服务器，客户端不断线。"""

    def test_service_mode_then_reload_keeps_server(self, monkeypatch):
        """服务模式 → 读档：服务器实例不变、世界请求注册、世界观可换。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            old_server = engine.server
            assert old_server is not None and old_server.is_running
            assert "get_chunks" not in engine.dispatcher._handlers
            assert "save_list" in engine.dispatcher._handlers

            world_id = engine.save_manager.create_world("测试世界", seed=7).world_id
            engine._reload(world_id=world_id)

            # 服务器保持同一实例且运行中（客户端未断线）
            assert engine.server is old_server
            assert engine.server.is_running
            assert engine.world_id == world_id
            assert engine._service_mode is False
            assert engine.birth_chunk is not None
            assert "get_chunks" in engine.dispatcher._handlers
            assert "player_move" in engine.dispatcher._handlers
            assert "save_list" in engine.dispatcher._handlers

            # 清理世界观：网络层保留，世界请求被注销
            engine._cleanup_world()
            assert engine.server is old_server
            assert engine.server.is_running
            assert "get_chunks" not in engine.dispatcher._handlers
            assert "save_list" in engine.dispatcher._handlers
            assert engine.chunk_store is None
            assert engine.player_service is None
        finally:
            engine.stop()

    def test_reload_failure_falls_back_to_service_mode(self, monkeypatch):
        """读档失败（目标不存在）：回到服务模式，服务器保持在线。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            old_server = engine.server

            with pytest.raises(Exception):
                engine._reload(world_id="no-such-world")

            # 兜底：服务模式（无世界），网络层与存档管理仍可用
            assert engine.server is old_server
            assert engine.server.is_running
            assert engine._service_mode is True
            assert engine.world_id is None
            assert engine._running.is_set()
            assert "get_chunks" not in engine.dispatcher._handlers
            assert "save_list" in engine.dispatcher._handlers
        finally:
            engine.stop()

    def test_world_swap_switches_handlers(self, monkeypatch):
        """世界 A → 世界 B 连续重建：新世界闭包覆盖旧闭包。"""
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            old_server = engine.server
            mgr = engine.save_manager
            world_a = mgr.create_world("世界A", seed=7).world_id
            world_b = mgr.create_world("世界B", seed=8).world_id

            engine._reload(world_id=world_a)
            assert engine.world_id == world_a

            engine._reload(world_id=world_b)
            assert engine.world_id == world_b
            assert engine.seed == 8
            assert engine.server is old_server
            assert engine.server.is_running
            assert "get_chunks" in engine.dispatcher._handlers
            # 旧世界的存档/快照处理仍可用
            assert "save_list" in engine.dispatcher._handlers
        finally:
            engine.stop()

    def test_rollback_with_bare_filename_rebuilds_and_forks(
        self, monkeypatch,
    ):
        """回滚（save_load 下发裸文件名）→ 重建 → 血缘分叉。

        回归：extract_snapshot 按裸文件名直接 open 失败，
        _reload 整体回退服务模式——前端看到的「点节点不退回」。

        验证链：快照 A/B（同线）→ 回滚到 A（live_origin=A）
        → 继续玩再快照 C（parent=A，分叉形成）。
        """
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            mgr = engine.save_manager
            world_id = mgr.create_world("回滚世界", seed=7).world_id
            engine._reload(world_id=world_id)
            assert engine.world_id == world_id

            mgr.write_state(world_id, _state_at(100))
            snap_a = mgr.create_snapshot(world_id, suffix="manual", game_time=100)
            mgr.write_state(world_id, _state_at(200))
            snap_b = mgr.create_snapshot(world_id, suffix="manual", game_time=200)
            lineage = mgr.snapshot_lineage(world_id)
            assert lineage["live_origin"] == snap_b, "活目录来源 = 最新快照"
            assert lineage["snapshots"][snap_b]["parent"] == snap_a, "连续保存应串链"

            # 回滚：裸文件名（协议下发形式）
            engine._reload(world_id=world_id, snapshot=snap_a)
            assert engine.world_id == world_id, "回滚后世界应保持加载"
            lineage = mgr.snapshot_lineage(world_id)
            assert lineage["live_origin"] == snap_a, "活目录来源应为回滚目标"
            # 回滚保护自动快照应从最近的手动存档派生（保护其所在分支）
            auto = [f for f in lineage["snapshots"]
                    if lineage["snapshots"][f]["saved_at"] > lineage["snapshots"][snap_b]["saved_at"]
                    and f != snap_a]
            assert auto, "回滚保护应产生自动快照"
            assert lineage["snapshots"][auto[0]]["parent"] == snap_b, \
                "自动快照应从最近手动存档派生"

            # 回滚后继续玩 → 新快照挂在 A 下（分叉）
            mgr.write_state(world_id, _state_at(150))
            snap_c = mgr.create_snapshot(world_id, suffix="manual", game_time=150)
            lineage = mgr.snapshot_lineage(world_id)
            assert lineage["snapshots"][snap_c]["parent"] == snap_a, "应形成分叉"
        finally:
            engine.stop()

    def test_multi_jump_preserves_all_branches(self, monkeypatch):
        """多次跨分支跳转：所有分支（含自动保护点）血缘完整保留。

        回归：跳转后旧分支仅剩自动保护快照，若血缘丢失或前端不可见，
        旧分支即"消失"。验证自动保护点在血缘中持续存在且父链正确。
        """
        _patch_fast_worldgen(monkeypatch)
        engine = GameEngine(seed=42)
        try:
            engine.start_service()
            mgr = engine.save_manager
            world_id = mgr.create_world("多跳世界", seed=7).world_id
            engine._reload(world_id=world_id)

            # S1 → S2 手动链
            mgr.write_state(world_id, _state_at(100))
            snap_s1 = mgr.create_snapshot(world_id, suffix="manual", game_time=100)
            mgr.write_state(world_id, _state_at(200))
            snap_s2 = mgr.create_snapshot(world_id, suffix="manual", game_time=200)

            # 跳转到 S1（S2 分支被自动保护 A1）
            engine._reload(world_id=world_id, snapshot=snap_s1)
            # S1 分支玩到 150，手动 S3
            mgr.write_state(world_id, _state_at(150))
            snap_s3 = mgr.create_snapshot(world_id, suffix="manual", game_time=150)

            # 跳转到 S2（S3 分支被自动保护 A2）
            engine._reload(world_id=world_id, snapshot=snap_s2)

            lineage = mgr.snapshot_lineage(world_id)
            entries = lineage["snapshots"]
            # 全部手动 + 自动快照都在
            assert snap_s1 in entries and snap_s2 in entries and snap_s3 in entries
            auto = [f for f in entries if f.endswith("-auto.ascendsave")]
            assert len(auto) == 2, "两次跳转应产生两个自动保护点，实际: %s" % auto
            # A1 保护 S2 分支（父 = S2）
            parents = {entries[f]["parent"]: f for f in auto}
            assert parents.get(snap_s2), "A1 应从 S2 派生（保护 S2 分支）"
            assert parents.get(snap_s3), "A2 应从 S3 派生（保护 S3 分支）"
            assert lineage["live_origin"] == snap_s2, "当前在 S2 分支"
        finally:
            engine.stop()
