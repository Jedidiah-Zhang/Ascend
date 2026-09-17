"""地址随机 —（算法标识, 世界种子, 地址）→ 值的纯函数（WC-5）。

地址 = 命名空间 + 实例 + 用途 + 时间 + 抽取序号（WC-5.1）；不存在流状态、
消费顺序或进度条。256-bit 派生值到离散区间用取模映射（WC-5.6）：
对 span ≤ 2⁶⁴，分布偏差 ≤ 2⁻¹⁹² 视为可忽略。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .derive import derive

FATE_ALGORITHM: str = "sha256/derive-1/mod"

_IDENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")


def _validate_ident(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENT_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} 必须为点分小写标识: {value!r}")


@dataclass(frozen=True, slots=True)
class FateAddress:
    """一次随机抽取的结构化地址。

    Attributes:
        namespace: 命名空间（登记于 fate_namespaces.json，WC-5.5）。
        purpose: 用途（同一命名空间内的抽取类别）。
        instance: 实例分量（坐标、标识等；身份必须稳定，WC-5.3）。
        time: 抽取所在的世界帧（tick）。
        draw_index: 同一地址内的抽取序号，从 0 起；跳过不回收（WC-5.2）。
    """

    namespace: str
    purpose: str
    instance: tuple[str | int, ...] = ()
    time: int = 0
    draw_index: int = 0

    def __post_init__(self) -> None:
        _validate_ident(self.namespace, "namespace")
        _validate_ident(self.purpose, "purpose")
        if not isinstance(self.instance, tuple):
            raise ValueError(f"instance 必须为元组: {self.instance!r}")
        for part in self.instance:
            if isinstance(part, bool) or not isinstance(part, (str, int)):
                raise ValueError(f"实例分量必须为 str/int: {part!r}")
            if isinstance(part, str) and not part:
                raise ValueError("实例分量不得为空字符串")
        for value, label in ((self.time, "time"), (self.draw_index, "draw_index")):
            if type(value) is not int or value < 0:
                raise ValueError(f"{label} 必须为非负整数: {value!r}")

    def parts(self) -> tuple[str | int, ...]:
        """规范分量序：命名空间 + 实例 + 用途 + 时间 + 抽取序号。"""
        return (
            self.namespace,
            *self.instance,
            self.purpose,
            self.time,
            self.draw_index,
        )


def address_seed(root: int, address: FateAddress) -> int:
    """地址 → 256-bit 抽取值（纯函数，无流状态）。"""
    if type(root) is not int:
        raise ValueError(f"世界种子必须为整数: {root!r}")
    if not isinstance(address, FateAddress):
        raise ValueError(f"地址必须为 FateAddress: {address!r}")
    return derive(root, *address.parts())


def address_value(
    root: int,
    address: FateAddress,
    *,
    minimum: int,
    maximum: int,
) -> int:
    """地址 → [minimum, maximum] 上的整数（取模映射，WC-5.6）。"""
    for value, label in ((minimum, "minimum"), (maximum, "maximum")):
        if type(value) is not int:
            raise ValueError(f"{label} 必须为整数: {value!r}")
    if maximum < minimum:
        raise ValueError(f"值域为空: [{minimum}, {maximum}]")
    span = maximum - minimum + 1
    return minimum + address_seed(root, address) % span
