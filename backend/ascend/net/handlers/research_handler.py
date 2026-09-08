"""干预执行器网络处理程序 — 研究 API（与终端 do 指令同源）。

通过 make_research_handler() 工厂函数创建，返回 {request_type: handler}
映射。全部操作落进同一干预表（单一事实源，执行前校验复用），缺省解析
与终端 do 共用 ``InterventionTable.default_frame`` / ``default_duration``。

- research_do：登记一条干预（结构化 JSON）→ ``{success, record}``。
- research_do_clear：清除指定干预（返回实际清除的替换规格）→
  ``{success, cleared}``。
- research_do_list：当前有效干预 + 完整历史（确定性快照）。

字段校验全部 fail-closed：``instance`` 必须是列表/元组（None 视作空元组），
``rep`` 必须为 value|mechanism 且仅适用于 node 空间，非法输入一律返回
``{success: false, error}`` 而不是抛异常。

特征核控制（space="feature"）转发到 ``WeatherEngine.force_feature``——
与终端 `weather feature` 同一写入路径，不产生"只登记不生效"的幽灵记录；
该空间只接受 ``space/target/instance/value{active}``，其余字段一律拒绝
（force_feature 没有帧/时长语义），成功响应为 ``{success, changed}``。
"""

from __future__ import annotations

from collections.abc import Callable

from ascend.causal import InterventionRecord, InterventionTable, default_duration
from ascend.log import get_logger
from ascend.net.protocol import make_response

logger = get_logger(__name__)

_SPACE_MAP = {
    "node": "node",
    "parameter": "parameter",
    "feature": "field_feature",
}
_REP_KINDS = ("value", "mechanism")
_FEATURE_FIELDS = frozenset({"space", "target", "instance", "value"})


def make_research_handler(
    table: InterventionTable,
    weather_engine=None,
) -> dict[str, Callable[[dict], dict]]:
    """为给定的干预表创建干预执行器处理程序。

    Args:
        table: InterventionTable 实例（干预执行器单一事实源）。
        weather_engine: WeatherEngine 实例（feature 空间转发目标）；
            None = feature 空间不可用（fail-closed）。

    Returns:
        一个字典，将 "research_do" / "research_do_clear" /
        "research_do_list" 映射到处理函数。
    """

    def handle_research_do(msg: dict) -> dict:
        payload = msg.get("payload", {})
        space = payload.get("space", "node")
        if space == "feature":
            return _handle_feature(payload, weather_engine)
        try:
            record = _record_from_payload(table, payload, space)
            stored = table.commit(record)
        except (ValueError, KeyError) as exc:
            return _fail("research_do", str(exc))
        logger.info(
            "research_do: seq=%d %s %s frame=%d duration=%s",
            stored.seq, stored.target_space, stored.target,
            stored.frame_t0, stored.duration,
        )
        return make_response(
            "research_do",
            {"success": True, "record": table.record_plain(stored)},
        )

    def handle_research_do_clear(msg: dict) -> dict:
        payload = msg.get("payload", {})
        space = payload.get("space", "node")
        target = payload.get("target", "")
        rep = payload.get("rep")
        resolved = _SPACE_MAP.get(space)
        if resolved is None:
            return _fail("research_do_clear", f"非法目标空间: {space}")
        if rep is not None and rep not in _REP_KINDS:
            return _fail(
                "research_do_clear",
                f"非法替换规格: {rep!r}（可选 {'|'.join(_REP_KINDS)}）",
            )
        if rep is not None and space != "node":
            return _fail("research_do_clear", "rep 仅适用于 node 目标空间")
        try:
            instance = _as_instance(payload)
        except ValueError as exc:
            return _fail("research_do_clear", str(exc))
        if space == "feature":
            # 特征核的单一事实源是注入核：清除必须走 force_feature，
            # 否则会留下"记录已清、核仍在"的孤儿状态。
            if weather_engine is None:
                return _fail(
                    "research_do_clear",
                    "特征核控制未挂载（缺少天气引擎）",
                )
            if len(instance) != 2:
                return _fail(
                    "research_do_clear",
                    "特征核控制必须提供 (cx, cy) 实例",
                )
            try:
                changed = weather_engine.force_feature(
                    instance[0], instance[1], target, False,
                )
            except ValueError as exc:
                return _fail("research_do_clear", str(exc))
            if changed is None:
                return _fail("research_do_clear", f"chunk 未注册: {instance}")
            return make_response(
                "research_do_clear",
                {"success": True, "cleared": ["value"] if changed else []},
            )
        cleared = table.clear(resolved, target, instance, rep=rep)
        return make_response(
            "research_do_clear",
            {"success": True, "cleared": list(cleared)},
        )

    def handle_research_do_list(_msg: dict) -> dict:
        return make_response(
            "research_do_list",
            {
                "snapshot": table.snapshot(),
                "history": table.history_plain(),
            },
        )

    return {
        "research_do": handle_research_do,
        "research_do_clear": handle_research_do_clear,
        "research_do_list": handle_research_do_list,
    }


def _fail(request_type: str, error: str) -> dict:
    return make_response(request_type, {"success": False, "error": error})


def _as_instance(payload: dict) -> tuple:
    """解析实例字段：仅列表/元组（None 视作空元组），否则 ValueError。"""
    raw = payload.get("instance", ())
    if raw is None:
        return ()
    if not isinstance(raw, (tuple, list)):
        raise ValueError(f"instance 必须为列表或元组: {raw!r}")
    return tuple(raw)


def _handle_feature(payload: dict, weather_engine) -> dict:
    """feature 空间 → WeatherEngine.force_feature（同一写入路径）。"""
    extra = sorted(set(payload) - _FEATURE_FIELDS)
    if extra:
        return _fail(
            "research_do",
            f"特征核控制不接受字段 {extra}（仅 {sorted(_FEATURE_FIELDS)}）",
        )
    if weather_engine is None:
        return _fail("research_do", "特征核控制未挂载（缺少天气引擎）")
    try:
        instance = _as_instance(payload)
    except ValueError as exc:
        return _fail("research_do", str(exc))
    target = payload.get("target", "")
    if len(instance) != 2 or not all(
        isinstance(axis, int) and not isinstance(axis, bool)
        for axis in instance
    ):
        return _fail("research_do", "特征核控制必须提供整数 (cx, cy) 实例")
    value = payload.get("value", {"active": True})
    if not isinstance(value, dict) or not isinstance(value.get("active"), bool):
        return _fail("research_do", '特征核控制 value 必须为 {"active": bool}')
    try:
        changed = weather_engine.force_feature(
            instance[0], instance[1], target, value["active"],
        )
    except ValueError as exc:
        return _fail("research_do", str(exc))
    if changed is None:
        return _fail("research_do", f"chunk 未注册: {instance}")
    return make_response(
        "research_do",
        {"success": True, "changed": bool(changed)},
    )


def _record_from_payload(
    table: InterventionTable, payload: dict, space: str,
) -> InterventionRecord:
    if space not in _SPACE_MAP:
        raise ValueError(f"非法目标空间: {space!r}")
    target = payload.get("target", "")
    if not target:
        raise ValueError("缺少 target")
    instance = _as_instance(payload)
    rep = payload.get("rep", "value")
    frame_raw = payload.get("frame_t0")
    if frame_raw is None:
        frame_t0 = table.default_frame()
    elif isinstance(frame_raw, bool) or not isinstance(frame_raw, int):
        raise ValueError(f"frame_t0 必须为整数: {frame_raw!r}")
    else:
        frame_t0 = frame_raw
    kwargs = {
        "target_space": _SPACE_MAP[space],
        "target": target,
        "instance": instance,
        "rep": rep,
        "frame_t0": frame_t0,
        "duration": payload.get(
            "duration", default_duration(_SPACE_MAP[space], rep),
        ),
        "version": payload.get("version", ""),
    }
    if rep == "value":
        if "value" not in payload:
            raise ValueError("值干预缺少 value")
        kwargs["value"] = payload["value"]
    elif rep == "mechanism":
        mechanism_id = payload.get("mechanism_id", "")
        mechanism = table.registry.mechanisms.get(mechanism_id)
        if mechanism is None:
            raise ValueError(f"机制未登记: {mechanism_id}")
        kwargs["mechanism"] = mechanism
    else:
        raise ValueError(f"非法替换规格: {rep!r}")
    return InterventionRecord(**kwargs)
