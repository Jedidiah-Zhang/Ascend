"""神迹系统网络处理程序 — 研究 API（与终端 do 指令同源）。

通过 make_research_handler() 工厂函数创建，返回 {request_type: handler}
映射，全部操作落进同一神迹表（单一事实源，执行前校验复用）。

- research_do：登记一条神迹（结构化 JSON）。
- research_do_clear：清除指定神迹。
- research_do_list：列出当前有效神迹（确定性快照）。
"""

from __future__ import annotations

from collections.abc import Callable

from ascend.causal import MiracleRecord, MiracleTable
from ascend.log import get_logger
from ascend.net.protocol import make_response

logger = get_logger(__name__)


def make_research_handler(table: MiracleTable) -> dict[str, Callable[[dict], dict]]:
    """为给定的神迹表创建神迹系统处理程序。

    Args:
        table: MiracleTable 实例（神迹系统单一事实源）。

    Returns:
        一个字典，将 "research_do" / "research_do_clear" /
        "research_do_list" 映射到处理函数。
    """

    def handle_research_do(msg: dict) -> dict:
        payload = msg.get("payload", {})
        try:
            record = _record_from_payload(table, payload)
            stored = table.commit(record, applied_at=payload.get("applied_at"))
        except (ValueError, KeyError) as exc:
            return make_response(
                "research_do",
                {"success": False, "error": str(exc)},
            )
        logger.info("research_do: seq=%d target=%s", stored.seq, stored.target)
        return make_response(
            "research_do",
            {"success": True, "seq": stored.seq},
        )

    def handle_research_do_clear(msg: dict) -> dict:
        payload = msg.get("payload", {})
        space = payload.get("space", "node")
        target = payload.get("target", "")
        instance = tuple(payload.get("instance", ()))
        rep = payload.get("rep")
        space_map = {"node": "node", "parameter": "parameter", "feature": "field_feature"}
        resolved = space_map.get(space)
        if resolved is None:
            return make_response(
                "research_do_clear",
                {"success": False, "error": f"非法目标空间: {space}"},
            )
        cleared = table.clear(resolved, target, instance, rep=rep)
        return make_response(
            "research_do_clear",
            {"success": True, "cleared": cleared},
        )

    def handle_research_do_list(_msg: dict) -> dict:
        return make_response(
            "research_do_list",
            {"snapshot": table.snapshot()},
        )

    return {
        "research_do": handle_research_do,
        "research_do_clear": handle_research_do_clear,
        "research_do_list": handle_research_do_list,
    }


def _record_from_payload(table, payload: dict) -> MiracleRecord:
    space = payload.get("space", "node")
    if space not in ("node", "parameter", "feature"):
        raise ValueError(f"非法目标空间: {space!r}")
    target = payload["target"]
    instance = tuple(payload.get("instance", ()))
    rep = payload.get("rep", "value")
    kwargs = {
        "target_space": {
            "node": "node", "parameter": "parameter", "feature": "field_feature",
        }[space],
        "target": target,
        "instance": instance,
        "rep": rep,
        "frame_t0": int(payload.get("frame_t0", 0)),
        "duration": payload.get("duration"),
        "version": payload.get("version", ""),
    }
    if rep == "value":
        if "value" not in payload:
            raise ValueError("值神迹缺少 value")
        kwargs["value"] = payload["value"]
    elif rep == "mechanism":
        mechanism_id = payload.get("mechanism_id", "")
        mechanism = table.registry.mechanisms.get(mechanism_id)
        if mechanism is None:
            raise ValueError(f"机制未登记: {mechanism_id}")
        kwargs["mechanism"] = mechanism
    else:
        raise ValueError(f"非法替换规格: {rep!r}")
    return MiracleRecord(**kwargs)