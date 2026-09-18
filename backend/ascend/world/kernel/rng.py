"""地址随机 —（种子, 地址）→ 值的纯函数（WC-5）。

地址 = 命名空间 + 实例 + 用途 + 时间 + 抽取序号（WC-5.1）；不存在流状态、
消费顺序或进度条（WC-5.2）。同一地址在任意两条干预臂中取值逐位相同，
这是 CRN 配对与反事实可复现的物理基础（WC-7.3）。

编码规则：str 带 2B 长度前缀、int 固定 32B 补码，杜绝拼接碰撞；
禁用内建 ``hash()``（进程加盐，跨进程不确定）。256-bit 派生值到离散
区间的映射用取模（WC-5.6），span ≤ 2⁶⁴ 时分布偏差 ≤ 2⁻¹⁹² 可忽略。

核心随机只提供**整数与定点**取值；随机浮点是浮点孤岛的事（WC-4.4）。
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

__all__ = [
    "ALGORITHM",
    "Address",
    "address_seed",
    "address_value",
    "draw_fixed",
]

ALGORITHM = "sha256/world-rng-1/mod"

_MASK_256 = (1 << 256) - 1
_VERSION = b"\x01"
_PARENT_BYTES = 32
_IDENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z0-9_]+)*$")


def _validate_ident(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENT_PATTERN.fullmatch(value) is None:
        raise ValueError(f"{label} 必须为点分小写标识: {value!r}")


def _encode_part(part: str | int) -> bytes:
    """单个身份分量的规范字节编码（类型标签 + 长度/定宽）。"""
    if isinstance(part, str):
        raw = part.encode("utf-8")
        if len(raw) > 0xFFFF:
            raise ValueError(f"身份分量过长: {len(raw)} 字节")
        return b"s" + len(raw).to_bytes(2, "big") + raw
    if isinstance(part, int):
        return b"i" + part.to_bytes(_PARENT_BYTES, "big", signed=True)
    raise TypeError(f"身份分量必须为 str/int，实际 {type(part).__name__}")


@dataclass(frozen=True, slots=True)
class Address:
    """一次随机抽取的结构化地址。

    Attributes:
        namespace: 命名空间（登记于模块声明，WC-5.5）。
        purpose: 用途（同一命名空间内的抽取类别）。
        instance: 实例分量（坐标、实体 ID 等；身份必须稳定，WC-5.3）。
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

    def at(self, *, time: int, draw_index: int = 0) -> "Address":
        """同地址换时间/序号（不可变值对象的派生写法）。"""
        return Address(
            namespace=self.namespace,
            purpose=self.purpose,
            instance=self.instance,
            time=time,
            draw_index=draw_index,
        )


def address_seed(root: int, address: Address) -> int:
    """地址 → 256-bit 抽取值（纯函数，无流状态）。"""
    if type(root) is not int:
        raise ValueError(f"世界种子必须为整数: {root!r}")
    if not isinstance(address, Address):
        raise ValueError(f"地址必须为 Address: {address!r}")
    h = hashlib.sha256()
    h.update(_VERSION)
    h.update((root & _MASK_256).to_bytes(_PARENT_BYTES, "big"))
    for part in address.parts():
        h.update(_encode_part(part))
    return int.from_bytes(h.digest(), "big")


def address_value(
    root: int,
    address: Address,
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


def draw_fixed(root: int, address: Address, bits: int) -> int:
    """地址 → Q(bits) 的 [0, 1) 均匀值与 raw 整数（核心随机无浮点）。"""
    if type(bits) is not int or bits < 0:
        raise ValueError(f"精度必须为非负整数: {bits!r}")
    return address_seed(root, address) % (1 << bits)
