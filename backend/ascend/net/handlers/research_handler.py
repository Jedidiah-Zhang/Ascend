"""干预执行器网络处理程序 — 研究 API（与终端 do 指令同源）。

通过 make_research_handler() 工厂函数创建，返回 {request_type: handler}
映射。全部操作落进同一干预时间线（单一事实源，执行前校验复用），缺省
解析与终端 do 共用 ``InterventionTimeline.default_frame`` /
``default_duration``。

- research_do：登记一条计划条目（结构化 JSON）→ ``{success, plan}``。
- research_do_clear：撤销指定计划（返回结束的条目数）→
  ``{success, stopped}``。
- research_do_list：当前生效计划 + 已发生记录（确定性快照）。

字段校验全部 fail-closed：``instance`` 必须是列表/元组（None 视作空元组），
未知字段一律拒绝；运行内机制替换已废除（WC-1.3，结构变体 = 换世界），
``rep`` / ``mechanism_id`` 返回 ``{success: false, error}``。

特征核控制（space="feature"）转发到 ``WeatherEngine.force_feature``——
与终端 `weather feature` 同一写入路径，不产生"只登记不生效"的幽灵记录；
该空间只接受 ``space/target/instance/value{active}``，其余字段一律拒绝。
"""

from __future__ import annotations

from collections.abc import Callable

from ascend.log import get_logger
from ascend.net.protocol import make_response
from ascend.world.research.timeline import (
    InterventionTimeline,
    PlannedIntervention,
    default_duration,
)

logger = get_logger(__name__)

# 研究日志查询的缺省/最大页长：单帧有 MAX_MESSAGE_SIZE 上限，
# 一次返回上万条会超限被前端静默丢弃。
TRACE_PAGE_DEFAULT: int = 200
TRACE_PAGE_MAX: int = 1000

_SPACE_MAP = {
    "node": "node",
    "parameter": "parameter",
    "feature": "field_feature",
}
_PLAN_FIELDS = frozenset({
    "space", "target", "instance", "value", "start_frame", "duration",
    "version",
})
_FEATURE_FIELDS = frozenset({"space", "target", "instance", "value"})


def make_research_handler(
    table: InterventionTimeline,
    weather_engine=None,
) -> dict[str, Callable[[dict], dict]]:
    """为给定的干预时间线创建干预执行器处理程序。

    Args:
        table: InterventionTimeline 实例（干预执行器单一事实源）。
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
            entry = _plan_from_payload(table, payload, space)
            stored = table.plan(entry)
        except (ValueError, KeyError) as exc:
            return _fail("research_do", str(exc))
        logger.info(
            "research_do: seq=%d %s %s window=[%d, %s)",
            stored.seq, stored.target_space, stored.target,
            stored.start_frame, stored.stop_frame,
        )
        return make_response(
            "research_do",
            {"success": True, "plan": stored.plain()},
        )

    def handle_research_do_clear(msg: dict) -> dict:
        payload = msg.get("payload", {})
        space = payload.get("space", "node")
        target = payload.get("target", "")
        resolved = _SPACE_MAP.get(space)
        if resolved is None:
            return _fail("research_do_clear", f"非法目标空间: {space}")
        try:
            instance = _as_instance(payload)
        except ValueError as exc:
            return _fail("research_do_clear", str(exc))
        extra = sorted(set(payload) - {"space", "target", "instance"})
        if extra:
            return _fail("research_do_clear", f"未知字段: {extra}")
        if space == "feature":
            # 特征核的单一事实源是注入核：解除必须走 force_feature，
            # 否则会留下"计划已停、核仍在"的孤儿状态。
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
                {"success": True, "changed": bool(changed)},
            )
        stopped = table.revoke(resolved, target, instance)
        return make_response(
            "research_do_clear",
            {"success": True, "stopped": stopped},
        )

    def handle_research_do_list(_msg: dict) -> dict:
        return make_response(
            "research_do_list",
            {
                "snapshot": table.snapshot(table.current_frame()),
                "history": table.history_plain(),
            },
        )

    # ── 研究日志：与终端 trace 指令组同源同一实例 ──────────

    def _trace_log():
        """取挂载的研究日志；未挂载抛 ValueError（fail-closed）。"""
        log = getattr(weather_engine, "trace", None) if weather_engine else None
        if log is None:
            raise ValueError("研究日志未挂载（缺少天气引擎或未开启）")
        return log

    def handle_trace_list(msg: dict) -> dict:
        payload = msg.get("payload", {})
        try:
            log = _trace_log()
            frame = _optional_int(payload, "frame")
            node_id = payload.get("node_id")
            if node_id is not None and not isinstance(node_id, str):
                raise ValueError(f"node_id 必须为字符串: {node_id!r}")
            kind = payload.get("kind")
            if kind is not None and kind not in ("eval", "recompute"):
                raise ValueError(
                    f"kind 必须为 eval/recompute（#50 双账分离）: {kind!r}"
                )
            offset = _optional_int(payload, "offset") or 0
            limit = _optional_int(payload, "limit") or TRACE_PAGE_DEFAULT
            if limit < 1 or limit > TRACE_PAGE_MAX:
                raise ValueError(
                    f"limit 必须在 [1, {TRACE_PAGE_MAX}]: {limit}"
                )
            if offset < 0:
                raise ValueError(f"offset 必须 ≥ 0: {offset}")
            page, total = log.page(
                frame=frame, node_id=node_id, kind=kind,
                offset=offset, limit=limit,
            )
        except ValueError as exc:
            return _fail("research_trace_list", str(exc))
        return make_response(
            "research_trace_list",
            {
                "success": True,
                "records": [r.plain() for r in page],
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "total": total,
                # 双账与丢失报告（#50）：发生/重算各多少、容量上界丢了多少
                "counts": log.counts(),
                "dropped": log.dropped,
            },
        )

    def handle_trace_replay(msg: dict) -> dict:
        payload = msg.get("payload", {})
        try:
            log = _trace_log()
            frame = _optional_int(payload, "frame")
            node_id = payload.get("node_id")
            if not isinstance(node_id, str) or not node_id:
                raise ValueError("research_trace_replay 需要 node_id")
        except ValueError as exc:
            return _fail("research_trace_replay", str(exc))
        records = log.records(frame=frame, node_id=node_id)
        if not records:
            return _fail("research_trace_replay", f"无匹配记录: {node_id}")
        entry = records[-1]
        try:
            replayed = log.replay(entry)
        except (KeyError, ValueError) as exc:
            return _fail("research_trace_replay", f"重算失败: {exc}")
        return make_response(
            "research_trace_replay",
            {
                "success": True,
                "record": entry.plain(),
                "replayed": replayed,
                "consistent": replayed == entry.output,
            },
        )

    def handle_trace_clear(_msg: dict) -> dict:
        try:
            log = _trace_log()
        except ValueError as exc:
            return _fail("research_trace_clear", str(exc))
        return make_response(
            "research_trace_clear",
            {"success": True, "cleared": log.clear()},
        )

    return {
        "research_do": handle_research_do,
        "research_do_clear": handle_research_do_clear,
        "research_do_list": handle_research_do_list,
        "research_trace_list": handle_trace_list,
        "research_trace_replay": handle_trace_replay,
        "research_trace_clear": handle_trace_clear,
    }


def _fail(request_type: str, error: str) -> dict:
    return make_response(request_type, {"success": False, "error": error})


def _optional_int(payload: dict, key: str) -> int | None:
    """解析可选整数字段；缺省 None，类型非法抛 ValueError。"""
    raw = payload.get(key)
    if raw is None:
        return None
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise ValueError(f"{key} 必须为整数: {raw!r}")
    return raw


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


def _plan_from_payload(
    table: InterventionTimeline, payload: dict, space: str,
) -> PlannedIntervention:
    if space not in _SPACE_MAP:
        raise ValueError(f"非法目标空间: {space!r}")
    if "rep" in payload or "mechanism_id" in payload:
        raise ValueError(
            "运行内机制替换已废除（WC-1.3）；结构变体 = 换世界，"
            "见独立参考对拍"
        )
    extra = sorted(set(payload) - _PLAN_FIELDS)
    if extra:
        raise ValueError(f"计划条目含未知字段: {extra}")
    target = payload.get("target", "")
    if not target:
        raise ValueError("缺少 target")
    instance = _as_instance(payload)
    start_raw = payload.get("start_frame")
    if start_raw is None:
        start_frame = table.default_frame()
    elif isinstance(start_raw, bool) or not isinstance(start_raw, int):
        raise ValueError(f"start_frame 必须为整数: {start_raw!r}")
    else:
        start_frame = start_raw
    duration = payload.get("duration", default_duration(_SPACE_MAP[space]))
    if duration is None:
        stop_frame = None
    else:
        if isinstance(duration, bool) or not isinstance(duration, int):
            raise ValueError(f"duration 必须为整数或 null: {duration!r}")
        stop_frame = start_frame + duration
    if "value" not in payload:
        raise ValueError("值干预缺少 value")
    return PlannedIntervention(
        target_space=_SPACE_MAP[space],
        target=target,
        instance=instance,
        value=payload["value"],
        start_frame=start_frame,
        stop_frame=stop_frame,
        source="research",
        version=payload.get("version", ""),
    )
