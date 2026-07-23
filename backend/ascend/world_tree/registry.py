"""Schema 注册表 — 管理所有事件类型的字段规范。"""

from __future__ import annotations

from typing import Any

from ascend.log import get_logger

from .schema import EventSchema


class SchemaRegistry:
    """事件类型注册表。

    存储所有已注册的事件 schema，提供注册、查询和校验功能。
    未注册的事件类型在校验时静默跳过。

    用法:
        registry = SchemaRegistry()
        registry.register(
            "weather_change",
            required={"temperature": float},
            optional={"humidity": float},
            description="天气变化事件",
        )
        errors = registry.validate("weather_change", {"temperature": 25.0})
    """

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._schemas: dict[str, EventSchema] = {}

    def __repr__(self) -> str:
        """返回注册表摘要。

        Returns:
            含已注册类型数量的 repr 字符串。
        """
        return f"SchemaRegistry(types={len(self._schemas)})"

    def register(
        self,
        event_type: str,
        *,
        required: dict[str, type | tuple[type, ...]] | None = None,
        optional: dict[str, type | tuple[type, ...]] | None = None,
        description: str = "",
    ) -> EventSchema:
        """注册一个事件类型的 schema。

        若 event_type 已注册则覆盖。

        Args:
            event_type: 事件类型字符串。
            required: 必填字段 → 类型映射。
            optional: 可选字段 → 类型映射。
            description: 人类可读说明。

        Returns:
            创建的 EventSchema 实例。
        """
        schema = EventSchema(
            event_type,
            required=required,
            optional=optional,
            description=description,
        )
        self._schemas[event_type] = schema
        logger = get_logger(__name__)
        logger.debug("注册事件 schema: %s", event_type)
        return schema

    def get(self, event_type: str) -> EventSchema | None:
        """获取事件类型的 schema。

        Args:
            event_type: 事件类型字符串。

        Returns:
            EventSchema 或 None。
        """
        return self._schemas.get(event_type)

    def validate(self, event_type: str, data: dict[str, Any]) -> list[str]:
        """校验事件 data，未注册的类型返回空（跳过校验）。

        Args:
            event_type: 事件类型字符串。
            data: 事件的 data 字段。

        Returns:
            错误信息列表，无错误时为空。
        """
        schema = self._schemas.get(event_type)
        if schema is None:
            return []
        return schema.validate(data)

    @property
    def registered_types(self) -> list[str]:
        """返回所有已注册的事件类型列表（排序）。"""
        return sorted(self._schemas.keys())

    def list_schemas(self) -> list[dict[str, Any]]:
        """列出所有已注册 schema 的摘要信息。

        Returns:
            包含 event_type、required/optional 字段、description 的
            字典列表，供文档和调试终端使用。
        """
        result: list[dict[str, Any]] = []
        for event_type in sorted(self._schemas.keys()):
            s = self._schemas[event_type]
            result.append({
                "event_type": s.event_type,
                "required": list(s.required.keys()),
                "optional": list(s.optional.keys()),
                "description": s.description,
            })
        return result
