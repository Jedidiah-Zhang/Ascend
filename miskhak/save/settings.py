"""世界设置校验 — 存档 manifest 与当前声明的一致性（fail-closed）。

世界设置 = 定义一个世界实例的全部输入：种子、生成参数、**机制声明
版本**（声明 ID + 全量摘要）、**观测协议版本**，以及**世界声明程序
身份**（WorldProgram 的声明编译产物摘要：槽位/地址/更新点/日历刻度的
组合）。前两者随存档位创建时定案，其余由当前进程的编译产物提供。

读档时记录与当前进程不一致即拒绝加载。

模块边界：本模块不解析世界内部结构，只比较调用方传入的两份视图——
**声明视图**（``WorldProgram.declaration_settings()``：声明 ID、全量
摘要、观测协议版本）与**程序视图**（``WorldProgram.settings()``：
程序身份摘要与分量摘要）。
"""

from __future__ import annotations

from typing import Mapping

# 声明视图的必需字段（值必须为非空字符串）
DECLARATION_FIELDS: tuple[str, ...] = (
    "declaration_id",
    "declaration_hash",
    "observation_protocol_version",
)

# 世界程序视图的比对字段（identity = 全部声明与日历刻度的组合摘要）
PROGRAM_FIELDS: tuple[str, ...] = ("identity",)


def validate_world_settings(manifest, declaration: Mapping[str, object]) -> None:
    """比对 manifest 记录的世界设置与当前声明（不一致即拒绝加载）。

    Args:
        manifest: ``Manifest`` 实例（其 ``mechanism_declaration`` 可为
            None = manifest 未记录，由调用方随后补写）。
        declaration: 当前声明视图，``WorldProgram.declaration_settings()``。

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
        # manifest 未记录声明版本：调用方校验通过后补写字段
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


def validate_world_program(manifest, program: Mapping[str, object]) -> None:
    """比对 manifest 记录的世界程序身份与当前编译产物（不一致即拒绝）。

    Args:
        manifest: ``Manifest`` 实例（其 ``world_program`` 可为 None =
            manifest 未记录，由调用方随后补写）。
        program: 当前世界设置视图，``WorldProgram.settings()``。

    Raises:
        ValueError: 程序视图字段缺失/非法，或 manifest 记录的身份与
            当前编译产物不符。
    """
    for field in PROGRAM_FIELDS:
        value = program.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"世界程序视图缺少字段 {field}: {value!r}")
    stored = manifest.world_program
    if stored is None:
        # manifest 未记录程序身份：调用方校验通过后补写字段
        return
    if not isinstance(stored, Mapping):
        raise ValueError(f"manifest 世界程序必须为映射: {stored!r}")
    for field in PROGRAM_FIELDS:
        recorded = stored.get(field)
        if recorded is None:
            raise ValueError(f"manifest 世界程序缺少字段 {field}")
        if recorded != program[field]:
            raise ValueError(
                f"世界程序身份与当前声明不一致（{field}）: "
                f"存档 {recorded!r} != 当前 {program[field]!r}；"
                f"该存档属于另一版世界程序，拒绝加载"
            )
