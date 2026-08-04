"""存档网络处理程序 — 状态通道的存档管理请求。

语义（Issue #13）：存档是状态通道（request-response）——世界外的
元操作，不产生历史、不进因果图。

协议（docs/世界框架/存档系统/设计.md）:
    save_list       → {payload: {worlds: [摘要...], snapshots: [...]}}
    save_create     {payload: {name, seed?}} → {payload: {world_id}}
    save_snapshot   {payload: {world_id}} → {payload: {file}}
    save_load       {payload: {world_id?|snapshot?}} → {payload: {world_id}}
    save_rename     {payload: {world_id, name}} → {payload: {name}}
    save_delete     {payload: {world_id}} → {payload: {}}
    save_export     {payload: {world_id}} → {payload: {world_id: 新ID}}

save_load 为异步：置位引擎读档请求后立即返回，重建在 tick 线程
内完成；重建完成后前端经 world_initialized 事件 + 重连感知。
"""

from ascend.log import get_logger

logger = get_logger(__name__)


def _payload(msg: dict) -> dict:
    """提取并校验请求载荷为字典。"""
    payload = msg.get("payload", {})
    if not isinstance(payload, dict):
        raise ValueError("payload 必须为对象")
    return payload


def make_save_handlers(save_manager, game_engine=None):
    """为给定的存档管理器创建存档请求处理程序。

    Args:
        save_manager: SaveManager 实例。
        game_engine: GameEngine 实例（save_load 需要）；None 时
            读档请求返回错误（纯磁盘模式下不可用）。

    Returns:
        {request_type: handler} 映射。
    """

    def handle_save_list(_msg: dict) -> dict:
        """列出所有存档位与快照（存档选择页数据源）。"""
        worlds = save_manager.list_worlds()
        snapshots: list[dict] = []
        for w in worlds:
            for s in save_manager.list_snapshots(w["world_id"]):
                s["world_id"] = w["world_id"]
                snapshots.append(s)
        return {
            "type": "response",
            "request_type": "save_list",
            "payload": {"worlds": worlds, "snapshots": snapshots},
        }

    def handle_save_create(msg: dict) -> dict:
        """创建新存档位（新游戏第一步，随后 save_load 进入世界）。"""
        payload = _payload(msg)
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("存档名称不能为空")
        seed = int(payload.get("seed", 0) or 0)
        manifest = save_manager.create_world(name, seed)
        return {
            "type": "response",
            "request_type": "save_create",
            "payload": {"world_id": manifest.world_id},
        }

    def handle_save_snapshot(msg: dict) -> dict:
        """手动保存：为世界创建回退点快照。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        filename = save_manager.create_snapshot(world_id, suffix="manual")
        return {
            "type": "response",
            "request_type": "save_snapshot",
            "payload": {"file": filename},
        }

    def handle_save_load(msg: dict) -> dict:
        """读档（活目录或快照回滚），异步重建。

        回滚语义（设计文档）：快照展开为活目录前，引擎重建流程会
        先自动快照当前状态（保护回滚前分支）。
        """
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip() or None
        snapshot = str(payload.get("snapshot", "")).strip() or None
        if not world_id and not snapshot:
            raise ValueError("缺少 world_id 或 snapshot")
        if world_id:
            save_manager.get_manifest(world_id)  # 校验存在性
        if game_engine is None:
            raise ValueError("引擎不可用，无法读档")
        if getattr(game_engine, "_pending_load", None):
            raise ValueError("已有读档请求在处理中")
        game_engine._pending_load = (world_id, snapshot)
        target = world_id if world_id else snapshot
        return {
            "type": "response",
            "request_type": "save_load",
            "payload": {"world_id": target},
        }

    def handle_save_rename(msg: dict) -> dict:
        """重命名存档位。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        save_manager.rename_world(world_id, name)
        return {
            "type": "response",
            "request_type": "save_rename",
            "payload": {"world_id": world_id, "name": name},
        }

    def handle_save_delete(msg: dict) -> dict:
        """删除存档位（连带快照）。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        save_manager.delete_world(world_id)
        return {
            "type": "response",
            "request_type": "save_delete",
            "payload": {},
        }

    def handle_save_export(msg: dict) -> dict:
        """复制世界为新的存档位（Issue #14 "复制存档"）。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        new_id = save_manager.export_world(world_id)
        return {
            "type": "response",
            "request_type": "save_export",
            "payload": {"world_id": new_id},
        }

    return {
        "save_list": handle_save_list,
        "save_create": handle_save_create,
        "save_snapshot": handle_save_snapshot,
        "save_load": handle_save_load,
        "save_rename": handle_save_rename,
        "save_delete": handle_save_delete,
        "save_export": handle_save_export,
    }
