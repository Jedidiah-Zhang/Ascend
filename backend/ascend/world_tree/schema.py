"""事件 Schema — 单个事件类型的字段规范定义。"""

from __future__ import annotations

from typing import Any


class EventSchema:
    """单个事件类型的 schema 定义。

    Attributes:
        event_type: 事件类型字符串。
        required: 必填字段名 → 期望类型（或类型元组）。
        optional: 可选字段名 → 期望类型（或类型元组）。
        description: 人类可读的事件类型说明。
    """

    def __init__(
        self,
        event_type: str,
        *,
        required: dict[str, type | tuple[type, ...]] | None = None,
        optional: dict[str, type | tuple[type, ...]] | None = None,
        description: str = "",
    ) -> None:
        """创建一个事件类型 schema。

        Args:
            event_type: 事件类型字符串，如 "weather_change"。
            required: 必填字段名到类型的映射。publish 时缺少任一字段
                      则校验失败。
            optional: 可选字段名到类型的映射。字段存在时校验类型，
                      不存在不报错。
            description: 人类可读的事件类型说明。
        """
        self.event_type = event_type
        self.required: dict[str, type | tuple[type, ...]] = required or {}
        self.optional: dict[str, type | tuple[type, ...]] = optional or {}
        self.description = description

    def __repr__(self) -> str:
        """返回 schema 摘要。

        Returns:
            含事件类型和字段数的 repr 字符串。
        """
        return (
            f"EventSchema({self.event_type!r}, "
            f"required={len(self.required)}, "
            f"optional={len(self.optional)})"
        )

    def validate(self, data: dict[str, Any]) -> list[str]:
        """校验事件 data 字典，返回错误信息列表。

        空列表表示校验通过。

        Args:
            data: 事件的 data 字段。

        Returns:
            错误信息字符串列表，无错误时为空。
        """
        errors: list[str] = []

        for field_name, expected_type in self.required.items():
            if field_name not in data:
                errors.append(
                    f"[{self.event_type}] 缺少必填字段 '{field_name}'"
                )
            elif not isinstance(data[field_name], expected_type):
                actual = type(data[field_name]).__name__
                errors.append(
                    f"[{self.event_type}] 字段 '{field_name}' "
                    f"类型错误: 期望 {_type_name(expected_type)}，"
                    f"实际 {actual}"
                )

        for field_name, expected_type in self.optional.items():
            if field_name in data and not isinstance(
                data[field_name], expected_type
            ):
                actual = type(data[field_name]).__name__
                errors.append(
                    f"[{self.event_type}] 可选字段 '{field_name}' "
                    f"类型错误: 期望 {_type_name(expected_type)}，"
                    f"实际 {actual}"
                )

        return errors


def _type_name(t: type | tuple[type, ...]) -> str:
    """类型的人类可读名称。

    Args:
        t: 单个类型或类型元组。

    Returns:
        类型名称字符串，如 "int | str"。
    """
    if isinstance(t, tuple):
        return " | ".join(x.__name__ for x in t)
    return t.__name__
