"""玩家状态网络处理程序 — 权威玩家位置的查询与移动上报。

通过 make_player_handler() 工厂函数创建，返回 {request_type: handler} 映射，
与 map_handler / weather_handler 模式一致。

协议:
    player_state 请求 → {payload: {entity_id, x, y}}
        查询"玩家控制的实体"——核心职责是告知前端本地控制的 entity_id
        （实体全量位置由 entity_snapshot 统一提供），x/y 为便捷冗余
    player_move  请求 {payload: {x, y}} → {payload: {x, y}}
        上报本地位置，返回裁决后的权威位置（壳子阶段原样接受）
"""

from ascend.log import get_logger

logger = get_logger(__name__)


def _parse_float(value) -> float | None:
    """校验并解析坐标值（有限 float/int，排除 bool）。

    Args:
        value: 载荷中的坐标字段。

    Returns:
        float 或 None（非法时）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    v = float(value)
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def make_player_handler(player_service):
    """为给定的 PlayerService 创建玩家请求处理程序。

    Args:
        player_service: PlayerService 实例。

    Returns:
        {"player_state": handler, "player_move": handler} 映射。
    """

    def handle_player_state(_msg: dict) -> dict:
        """处理 player_state 请求：返回玩家控制的实体标识与权威位置。

        Args:
            _msg: 请求消息（无载荷字段要求）。

        Returns:
            {type, request_type, payload: {entity_id, x, y}}。
        """
        x, y = player_service.position
        entity = player_service.entity
        return {
            "type": "response",
            "request_type": "player_state",
            "payload": {
                "entity_id": entity.id if entity else "",
                "x": x,
                "y": y,
            },
        }

    def handle_player_move(msg: dict) -> dict:
        """处理 player_move 请求：上报位置 → 权威裁决 → 回传。

        非法坐标忽略上报，返回当前权威位置（前端据此纠正）。

        Args:
            msg: 请求消息，payload 含 "x"、"y"。

        Returns:
            {type, request_type, payload: {x, y}}，为裁决后权威位置。
        """
        payload = msg.get("payload", {})
        if not isinstance(payload, dict):
            payload = {}
        x = _parse_float(payload.get("x"))
        y = _parse_float(payload.get("y"))
        if x is None or y is None:
            logger.warning("player_move: 非法坐标 %r，忽略", payload)
            ax, ay = player_service.position
        else:
            ax, ay = player_service.move_to(x, y)
        return {
            "type": "response",
            "request_type": "player_move",
            "payload": {"x": ax, "y": ay},
        }

    return {
        "player_state": handle_player_state,
        "player_move": handle_player_move,
    }
