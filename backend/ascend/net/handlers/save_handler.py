"""存档网络处理程序 — 状态通道的存档管理请求。

语义（Issue #13）：存档是状态通道（request-response）——世界外的
元操作，不产生历史、不进因果图。

进程模型（一进程一模式）:
    save_list       → {payload: {worlds: [摘要...], snapshots: [...],
                                  current_world_id: 当前加载世界}}
    save_create     {payload: {name, seed?, gen_params?}} → {payload: {world_id}}
    save_snapshot   {payload: {world_id}} → {payload: {file}}
    save_snapshot_delete {payload: {world_id, snapshot, recursive}}
                    → {payload: {deleted: [file]}}   # 单点 / 分支裁剪
    save_rename     {payload: {world_id, name}} → {payload: {name}}
    save_delete     {payload: {world_id}} → {payload: {}}
    save_export     {payload: {world_id}} → {payload: {world_id: 新ID}}

进入世界 / 回滚不走请求通道：由前端停菜单进程、以
run_server --world-id/--snapshot 拉起世界进程完成（进程模型）。
"""

from ascend.log import get_logger
from ascend.net.protocol import make_response
from ascend.save.lineage import (GAME_TIME_KEY, LIVE_ORIGIN_KEY, PARENT_KEY,
                                 SEQ_KEY, SNAPSHOTS_KEY,
                                 parse_snapshot_entries)

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
            w[LIVE_ORIGIN_KEY] = lineage.get(LIVE_ORIGIN_KEY, "")
            entries = parse_snapshot_entries(lineage.get(SNAPSHOTS_KEY, {}))
            for s in save_manager.list_snapshots(w["world_id"]):
                s["world_id"] = w["world_id"]
                entry = entries.get(s["file"])
                s[PARENT_KEY] = entry.parent if entry else ""
                s[GAME_TIME_KEY] = entry.game_time if entry else 0
                s[SEQ_KEY] = entry.seq if entry else 0
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
        """创建新存档位（新游戏第一步，随后前端拉起世界进程进入）。

        gen_params 为创建世界流程的调参产出（Issue #8）：目前含
        land_ratio（目标陆地比例），随档定案写入 manifest。
        """
        payload = _payload(msg)
        name = str(payload.get("name", "")).strip()
        if not name:
            raise ValueError("存档名称不能为空")
        seed = int(payload.get("seed", 0) or 0)
        gen_params = payload.get("gen_params")
        if gen_params is not None and not isinstance(gen_params, dict):
            raise ValueError("gen_params 必须为对象")
        manifest = save_manager.create_world(name, seed, gen_params=gen_params)
        return make_response(
                "save_create",
                {"world_id": manifest.world_id},
            )

    def handle_save_snapshot(msg: dict) -> dict:
        """手动保存：当前 auto 记录晋升为 manual 并开启新当前记录。

        引擎可用时走 snapshot_current（目标即当前世界时 flush +
        WAL checkpoint + 打包，保证快照内 chunk/事件数据完整；
        目标为未加载世界/服务模式时其 DB 未打开，直接打包一致快照）；
        纯磁盘模式直接打包。返回晋升后的保存节点文件名。
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

    def handle_save_snapshot_delete(msg: dict) -> dict:
        """删除快照：单点（recursive=False，后代重接）或分支裁剪
        （recursive=True，节点 + 全部后代）。

        语义（Issue #32）：血缘森林节点集合移除——删除集由本处
        计算（单点或子树），血缘重接 / live_origin 回退 / seq 空洞
        由 SaveManager.remove_snapshots 原语统一保证。

        与内部清理（保留策略）不同，用户显式操作严格校验：
        目标快照不在血缘中即报错（而非静默容忍）。
        """
        payload = _payload(msg)
        world_id = str(payload.get("world_id", "")).strip()
        snapshot = str(payload.get("snapshot", "")).strip()
        recursive = bool(payload.get("recursive", False))
        if not world_id:
            raise ValueError("缺少 world_id")
        if not snapshot:
            raise ValueError("缺少 snapshot")
        save_manager.get_manifest(world_id)  # 校验目标存在性
        lineage = save_manager.snapshot_lineage(world_id)
        if snapshot not in lineage.get(SNAPSHOTS_KEY, {}):
            raise ValueError(f"快照不存在: {snapshot}")
        if recursive:
            deleted = save_manager.remove_snapshot_branch(world_id, snapshot)
        else:
            deleted = save_manager.remove_snapshots(world_id, [snapshot])
        return make_response(
            "save_snapshot_delete",
            {"deleted": deleted},
        )

    return {
        "save_list": handle_save_list,
        "save_create": handle_save_create,
        "save_snapshot": handle_save_snapshot,
        "save_snapshot_delete": handle_save_snapshot_delete,
        "save_rename": handle_save_rename,
        "save_delete": handle_save_delete,
        "save_export": handle_save_export,
    }
