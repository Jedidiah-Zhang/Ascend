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
