"""存档网络处理程序 — 状态通道的存档管理请求。

语义（Issue #13）：存档是状态通道（request-response）——世界外的
元操作，不产生历史、不进因果图。

进程模型（一进程一模式）:
    save_list       → {payload: {worlds: [摘要...], snapshots: [...],
                                  current_world_id: 当前加载世界}}
    save_create     {payload: {name, seed?}} → {payload: {world_id}}
    save_snapshot   {payload: {world_id}} → {payload: {file}}
    save_rename     {payload: {world_id, name}} → {payload: {name}}
    save_delete     {payload: {world_id}} → {payload: {}}
    save_export     {payload: {world_id}} → {payload: {world_id: 新ID}}

进入世界 / 回滚不再走 save_load 请求：由前端停菜单进程、以
run_server --world-id/--snapshot 拉起世界进程完成（进程模型重构）。
"""

from ascend.log import get_logger
from ascend.net.protocol import make_response

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
        game_engine: GameEngine 实例（save_snapshot/save_list 需要）；
            None 时走纯磁盘路径（菜单进程与引擎路径等效）。

    Returns:
        {request_type: handler} 映射。
    """

    def handle_save_list(_msg: dict) -> dict:
        """列出所有存档位与快照（存档选择页数据源）。

        快照条目附血缘字段（时间线分叉视图用）：
          parent    创建时活目录来源（回滚目标快照文件名，"" = 世界初始）
          game_time 创建时刻的世界时间（tick）
          seq       世界内单调递增的权威排序键（创建顺序，时间线/
                    编号/串链排序的单一事实来源）
        世界摘要附 live_origin（当前活目录来源，即"当前时间点"的父节点）；
        顶层 current_world_id = 引擎当前加载的世界（"最后进入"标注）。
        """
        worlds = save_manager.list_worlds()
        snapshots: list[dict] = []
        for w in worlds:
            lineage = save_manager.snapshot_lineage(w["world_id"])
            w["live_origin"] = lineage.get("live_origin", "")
            for s in save_manager.list_snapshots(w["world_id"]):
                s["world_id"] = w["world_id"]
                entry = lineage.get("snapshots", {}).get(s["file"], {})
                s["parent"] = str(entry.get("parent", ""))
                s["game_time"] = int(entry.get("game_time", 0))
                s["seq"] = int(entry.get("seq", 0))
                snapshots.append(s)
        current_world_id = ""
        if game_engine is not None:
            current_world_id = getattr(game_engine, "world_id", None) or ""
        return make_response(
                "save_list",
                {
                "worlds": worlds,
                "snapshots": snapshots,
                "current_world_id": current_world_id,
            },
            )

    def handle_save_create(msg: dict) -> dict:
        """创建新存档位（新游戏第一步，随后前端拉起世界进程进入）。"""
        payload = _payload(msg)
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("存档名称不能为空")
        seed = int(payload.get("seed", 0) or 0)
        manifest = save_manager.create_world(name, seed)
        return make_response(
                "save_create",
                {"world_id": manifest.world_id},
            )

    def handle_save_snapshot(msg: dict) -> dict:
        """手动保存：为世界创建回退点快照。

        引擎可用时走 snapshot_current（目标即当前世界时 flush +
        WAL checkpoint + 打包，保证快照内 chunk/事件数据完整；
        目标为未加载世界/服务模式时其 DB 未打开，直接打包一致快照）；
        纯磁盘模式直接打包。
        """
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        save_manager.get_manifest(world_id)  # 校验目标存在性
        if game_engine is not None:
            filename = game_engine.snapshot_current(
                world_id=world_id, suffix="manual",
            )
        else:
            filename = save_manager.create_snapshot(world_id, suffix="manual")
        return make_response(
                "save_snapshot",
                {"file": filename},
            )

    def handle_save_rename(msg: dict) -> dict:
        """重命名存档位。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        name = str(payload.get("name", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        save_manager.rename_world(world_id, name)
        return make_response(
                "save_rename",
                {"world_id": world_id, "name": name},
            )

    def handle_save_delete(msg: dict) -> dict:
        """删除存档位（连带快照）。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        save_manager.delete_world(world_id)
        return make_response(
                "save_delete",
                {},
            )

    def handle_save_export(msg: dict) -> dict:
        """复制世界为新的存档位（Issue #14 "复制存档"）。"""
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        if not world_id:
            raise ValueError("缺少 world_id")
        new_id = save_manager.export_world(world_id)
        return make_response(
                "save_export",
                {"world_id": new_id},
            )

    return {
        "save_list": handle_save_list,
        "save_create": handle_save_create,
        "save_snapshot": handle_save_snapshot,
        "save_rename": handle_save_rename,
        "save_delete": handle_save_delete,
        "save_export": handle_save_export,
    }
