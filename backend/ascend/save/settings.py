"""世界设置校验 — 存档 manifest 与当前声明的一致性（fail-closed）。

世界设置 = 定义一个世界实例的全部输入：种子、生成参数，以及**机制声明
版本**（声明 ID + 全量摘要）与**观测协议版本**。前两者随存档位创建时
定案，后两者由机制注册表提供。

读档时若声明版本与当前进程的注册表不一致，说明这个世界的生成规律已经
变了：用新公式继续跑旧状态会得到一条"合法但不属于任何已声明世界"的
轨迹（静默错误）。本模块只做一件事——把这种不一致挡在加载之前。

模块边界：本模块不认识注册表，只比较调用方传入的**声明视图**
（``MechanismRegistry.declaration_settings()`` 的输出），因此存档层
不依赖因果层。
"""

from __future__ import annotations

from typing import Mapping

# 声明视图的必需字段（值必须为非空字符串）
DECLARATION_FIELDS: tuple[str, ...] = (
    "declaration_id",
    "declaration_hash",
    "observation_protocol_version",
)


def validate_world_settings(manifest, declaration: Mapping[str, object]) -> None:
    """比对 manifest 记录的世界设置与当前声明（不一致即拒绝加载）。

    Args:
        manifest: ``Manifest`` 实例（其 ``mechanism_declaration`` 可为
            None = 旧存档尚未记录，由调用方随后补写）。
        declaration: 当前声明视图，``MechanismRegistry.declaration_settings()``。

    Raises:
        ValueError: 声明视图字段缺失/非法，或 manifest 记录的声明版本与
            当前声明不符。
    """
    for field in DECLARATION_FIELDS:
        value = declaration.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"声明视图缺少字段 {field}: {value!r}")
    stored = manifest.mechanism_declaration
    if stored is None:
        # 旧存档：未记录声明版本。调用方校验通过后必须立即补写，
        # 使下一次加载有可比对的事实（不做静默"总是接受"）。
        return
    if not isinstance(stored, Mapping):
        raise ValueError(f"manifest 机制声明必须为映射: {stored!r}")
    for field in DECLARATION_FIELDS:
        recorded = stored.get(field)
        if recorded is None:
            raise ValueError(f"manifest 机制声明缺少字段 {field}")
        if recorded != declaration[field]:
            raise ValueError(
                f"世界声明与当前注册表不一致（{field}）: "
                f"存档 {recorded!r} != 当前 {declaration[field]!r}；"
                f"该存档属于另一版机制声明，拒绝加载"
            )
