"""玩家请求处理程序单元测试。

通过真实 PlayerService（隔离 WorldTree）验证 make_player_handler
创建的 player_state / player_move 处理函数行为。
"""

import pytest

from ascend.entity import EntityManager, PlayerService
from ascend.net.handlers.player_handler import make_player_handler
from ascend.time import WorldClock
from ascend.world_tree import WorldTree


@pytest.fixture
def service():
    """出生 chunk (0, 0)、已 birth 的 PlayerService 固件。"""
    wt = WorldTree()
    manager = EntityManager(world_tree_arg=wt)
    svc = PlayerService(manager, WorldClock(), birth_chunk=(0, 0), world_tree_arg=wt)
    svc.birth()
    return svc


@pytest.fixture
def handlers(service):
    """make_player_handler 返回的处理程序映射。"""
    return make_player_handler(service)


class TestRegistration:
    """工厂函数注册测试。"""

    def test_returns_both_handlers(self, handlers):
        """返回 player_state 与 player_move 两个可调用处理程序。"""
        assert set(handlers.keys()) == {"player_state", "player_move"}
        assert all(callable(h) for h in handlers.values())


class TestPlayerState:
    """player_state 请求测试。"""

    def test_state_returns_position_and_id(self, handlers, service):
        """player_state 返回本地控制的 entity_id 与权威位置。

        Arrange:
            已 birth 的 service（出生点 (0,0) → 位置 (0.0, 0.0)）。
        Act:
            调用 player_state handler。
        Assert:
            响应含 entity_id/x/y，与 service 状态一致。
        """
        resp = handlers["player_state"]({"payload": {}})
        assert resp["type"] == "response"
        assert resp["request_type"] == "player_state"
        payload = resp["payload"]
        assert payload["entity_id"] == service.entity.id
        assert (payload["x"], payload["y"]) == service.position

    def test_state_reflects_moves(self, handlers, service):
        """移动后 player_state 返回新位置。"""
        service.move_to(123.5, 456.5)
        payload = handlers["player_state"]({})["payload"]
        assert (payload["x"], payload["y"]) == (123.5, 456.5)


class TestPlayerMove:
    """player_move 请求测试。"""

    def test_move_accepts_and_echoes(self, handlers, service):
        """合法上报被接受，回传权威位置（壳子=原样）。

        Arrange:
            已 birth 的 service。
        Act:
            上报 (10.5, 20.25)。
        Assert:
            响应回显该位置，service 状态更新。
        """
        msg = {"payload": {"x": 10.5, "y": 20.25}}
        resp = handlers["player_move"](msg)
        assert resp["request_type"] == "player_move"
        assert resp["payload"] == {"x": 10.5, "y": 20.25}
        assert service.position == (10.5, 20.25)

    @pytest.mark.parametrize("payload", [
        {},                                  # 缺字段
        {"x": 1.0},                          # 缺 y
        {"x": "abc", "y": 2.0},              # 非数值
        {"x": True, "y": 2.0},               # bool 排除
        {"x": float("nan"), "y": 2.0},       # NaN
        {"x": float("inf"), "y": 2.0},       # inf
        "not-a-dict",                        # payload 非字典
    ])
    def test_move_invalid_ignored(self, handlers, service, payload):
        """非法上报被忽略，回传当前权威位置（前端据此纠正）。"""
        before = service.position
        resp = handlers["player_move"]({"payload": payload})
        assert resp["payload"] == {"x": before[0], "y": before[1]}
        assert service.position == before

    def test_move_int_coords_ok(self, handlers, service):
        """整数坐标合法（JSON 整数不带小数点）。"""
        resp = handlers["player_move"]({"payload": {"x": 3, "y": 4}})
        assert resp["payload"] == {"x": 3.0, "y": 4.0}
