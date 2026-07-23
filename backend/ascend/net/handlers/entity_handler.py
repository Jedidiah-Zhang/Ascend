"""实体快照网络处理程序 — 状态通道的实体全量查询。

语义（Issue #20）:
    entity_snapshot 属于状态通道（request-response）——世界外的元操作，
    供前端接入/读档后初始化实体视图，不产生历史、不进因果图。
    接入后的增量维护走因果通道（entity_born/died/moved 事件）。

协议:
    entity_snapshot 请求 → {payload: {entities: [
        {id, entity_type, controller, x, y, layer_id}, ...
    ]}}
"""

from ascend.entity import Entity, EntityManager
from ascend.log import get_logger

logger = get_logger(__name__)


def serialize_entity(entity: Entity) -> dict:
    """将实体序列化为快照条目。

    Args:
        entity: Entity 实例。

    Returns:
        {id, entity_type, controller, x, y, layer_id} 字典，
        x/y 为全局 float tile 坐标（与事件、player_state 同一坐标系）。
    """
    x, y = entity.global_xy
    return {
        "id": entity.id,
        "entity_type": entity.entity_type.name,
        "controller": entity.controller.name,
        "x": x,
        "y": y,
        "layer_id": entity.layer_id,
    }


def make_entity_handlers(entity_manager: EntityManager):
    """为给定的 EntityManager 创建实体请求处理程序。

    Args:
        entity_manager: EntityManager 实例。

    Returns:
        {"entity_snapshot": handler} 映射。
    """

    def handle_entity_snapshot(_msg: dict) -> dict:
        """处理 entity_snapshot 请求：返回当前存活实体表。

        Args:
            _msg: 请求消息（无载荷字段要求）。

        Returns:
            {type, request_type, payload: {entities: [...]}}。
        """
        entities = [
            serialize_entity(e) for e in entity_manager.all_entities()
        ]
        return {
            "type": "response",
            "request_type": "entity_snapshot",
            "payload": {"entities": entities},
        }

    return {
        "entity_snapshot": handle_entity_snapshot,
    }
