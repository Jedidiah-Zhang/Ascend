"""MessageDispatcher 单元测试。

通过 mock GameServer 验证消息路由和行为。
"""

import pytest
from unittest.mock import MagicMock, call
from ascend.net.dispatcher import MessageDispatcher


class TestMessageDispatcher:
    """MessageDispatcher 消息路由测试。"""

    # ── T1: 正常注册和分发 ─────────────────────────────────────────────

    def test_T1_register_and_dispatch(self):
        """注册 handler，模拟消息入队，process() 后验证 handler 被调用。

        Arrange:
            创建 mock server，返回一条 request 消息。
            MessageDispatcher 注册一个测试 handler。
        Act:
            调用 process()。
        Assert:
            handler 被正确调用，broadcast 收到响应。
        """
        # Arrange
        mock_server = MagicMock()
        mock_server.receive_all.return_value = [
            {"type": "request", "request_type": "ping", "seq": 1, "payload": {}}
        ]

        handler_called = False

        def handle_ping(msg: dict) -> dict:
            nonlocal handler_called
            handler_called = True
            return {"type": "response", "request_type": "ping", "payload": {"result": "pong"}}

        dispatcher = MessageDispatcher(mock_server)
        dispatcher.register("ping", handle_ping)

        # Act
        dispatcher.process()

        # Assert
        assert handler_called, "handler 未被调用"
        # broadcast 应被调用两次（一次是 register 过程没有广播，一次是 dispatch 后）
        # 但 register 不涉及 broadcast, process 中 handler 返回了响应
        # 验证 broadcast 收到正确的响应
        expected_response = {
            "type": "response",
            "request_type": "ping",
            "payload": {"result": "pong"},
            "seq": 1,
        }
        mock_server.broadcast.assert_called_once_with(expected_response)

    # ── T2: 未知 request_type ──────────────────────────────────────────

    def test_T2_unknown_request_type(self):
        """未知 request_type 返回 error 响应。

        Arrange:
            创建 mock server，返回一条未知 request_type 的消息。
        Act:
            调用 process()。
        Assert:
            broadcast 收到 error 响应，包含 "unknown request_type"。
        """
        mock_server = MagicMock()
        mock_server.receive_all.return_value = [
            {"type": "request", "request_type": "unknown_action", "seq": 1, "payload": {}}
        ]

        dispatcher = MessageDispatcher(mock_server)
        dispatcher.process()

        expected_error = {
            "type": "error",
            "request_type": "unknown_action",
            "seq": 1,
            "error": "unknown request_type: unknown_action",
        }
        mock_server.broadcast.assert_called_once_with(expected_error)

    # ── T3: Handler 异常 ──────────────────────────────────────────────

    def test_T3_handler_exception(self):
        """handler 抛出异常时返回 error 响应。

        Arrange:
            注册一个会抛出异常的 handler。
        Act:
            调用 process()。
        Assert:
            broadcast 收到 error 响应，error 字段包含异常信息。
        """
        mock_server = MagicMock()
        mock_server.receive_all.return_value = [
            {"type": "request", "request_type": "crash", "seq": 2, "payload": {}}
        ]

        def broken_handler(msg: dict) -> dict:
            raise RuntimeError("something went wrong")

        dispatcher = MessageDispatcher(mock_server)
        dispatcher.register("crash", broken_handler)

        dispatcher.process()

        expected_error = {
            "type": "error",
            "request_type": "crash",
            "seq": 2,
            "error": "something went wrong",
        }
        mock_server.broadcast.assert_called_once_with(expected_error)

    # ── T4: 忽略非 request 消息 ───────────────────────────────────────

    def test_T4_ignore_non_request(self):
        """忽略 type != "request" 的消息。

        Arrange:
            mock server 返回一条 type="event" 的消息。
        Act:
            调用 process()。
        Assert:
            handler 未被调用，broadcast 未被调用。
        """
        mock_server = MagicMock()
        mock_server.receive_all.return_value = [
            {"type": "event", "event_type": "test", "payload": {}}
        ]

        handler_called = False

        def handler(msg: dict) -> dict:
            nonlocal handler_called
            handler_called = True
            return {"type": "response", "request_type": "test", "payload": {}}

        dispatcher = MessageDispatcher(mock_server)
        dispatcher.register("test", handler)

        dispatcher.process()

        assert not handler_called, "handler 不应被调用"
        mock_server.broadcast.assert_not_called()

    # ── 辅助测试：重复注册会报错 ─────────────────────────────────────

    def test_register_duplicate_raises(self):
        """重复注册相同 request_type 应抛出 ValueError。"""
        mock_server = MagicMock()
        dispatcher = MessageDispatcher(mock_server)

        dispatcher.register("dup", lambda msg: None)

        with pytest.raises(ValueError, match="已注册"):
            dispatcher.register("dup", lambda msg: None)

    # ── 辅助测试：process 空队列不报错 ────────────────────────────────

    def test_process_empty_queue(self):
        """process() 空消息队列不报错。

        Arrange:
            mock server 返回空列表。
        Act:
            调用 process()。
        Assert:
            无异常。
        """
        mock_server = MagicMock()
        mock_server.receive_all.return_value = []

        dispatcher = MessageDispatcher(mock_server)

        # 不应抛出任何异常
        dispatcher.process()
        mock_server.broadcast.assert_not_called()

    # ── 辅助测试：handler 返回 None 不广播 ────────────────────────────

    def test_handler_returns_none_no_broadcast(self):
        """handler 返回 None 时不调用 broadcast。"""
        mock_server = MagicMock()
        mock_server.receive_all.return_value = [
            {"type": "request", "request_type": "silent", "seq": 1, "payload": {}}
        ]

        dispatcher = MessageDispatcher(mock_server)
        dispatcher.register("silent", lambda msg: None)

        dispatcher.process()
        mock_server.broadcast.assert_not_called()

    # ── 辅助测试：repr ────────────────────────────────────────────────

    def test_repr(self):
        """__repr__ 返回包含已注册处理程序列表的字符串。"""
        mock_server = MagicMock()
        dispatcher = MessageDispatcher(mock_server)
        dispatcher.register("a", lambda msg: None)
        dispatcher.register("b", lambda msg: None)

        r = repr(dispatcher)
        assert "a" in r
        assert "b" in r
        assert "MessageDispatcher" in r
