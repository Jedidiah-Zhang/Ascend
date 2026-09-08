"""可执行因果机制注册表与确定性声明快照。"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
import hashlib
import inspect
import json
import math
import os
import textwrap
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from .spec import (
    AccessPolicy,
    DependencyWitness,
    ExogenousSourceSpec,
    InstanceDomain,
    MathMetadata,
    MechanismSpec,
    NodeSpec,
    ParameterBinding,
    ParameterSpec,
    ParentSpec,
    RandomBinding,
    StateOwnership,
    UpdateContract,
    ValueDomain,
)

_NODE_ROLES = {"mechanism_state", "persistent_state", "readout"}
_NODE_ORIGINS = {"slice_boundary", "mechanism"}
_VALUE_KINDS = {"float", "integer", "enum", "boolean", "string"}
_INTERVENTIONS = {"node", "persistent", "mechanism"}
_ANALYSIS_ROLES = {"forward", "inverse", "observable"}


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _digest(value: object) -> str:
    raw = _canonical(value).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _callable_name(function: object) -> str:
    return f"{function.__module__}.{function.__qualname__}"


def _sourceless() -> bool:
    """当前是否处于打包（Nuitka standalone）运行模式。

    打包分发不含 .py/.c（见 AGENTS.md 发行约定）：Nuitka 在每个编译
    模块注入 ``__compiled__`` 全局，仓库源码模式恒为 None。打包模式
    无法哈希方程源码与依赖文件，若按源码模式构造注册表会在"进入世界"
    时崩溃 —— 因此打包模式下方程版本**降级**为按名称摘要的
    "packaged" 版本（见 :func:`_source_version`），而非拒绝构造。
    源码模式行为不变（缺失来源仍 fail-closed 抛 ValueError）。

    ``ASCEND_SOURCELESS=1`` 环境变量仅供打包布局回归测试注入。
    """
    if os.environ.get("ASCEND_SOURCELESS") == "1":
        return True
    return bool(globals().get("__compiled__"))


def _source_version(
    function: object,
    dependencies: tuple[object, ...],
) -> str:
    """方程/依赖的版本摘要。

    源码模式：可调用对象哈希去缩进源码文本，文件依赖哈希文件字节
    （缺失即 ValueError —— fail-closed，防静默漂移）。
    打包（无源码）模式：不触碰磁盘与源码，按来源**名称**生成带
    ``sha256-packaged:`` 前缀的降级摘要 —— 该版本不再可比对源码，
    仅标记"无来源构建"；注册表可构造、evaluate 语义不变。
    """
    if _sourceless():
        names = []
        for item in (function, *dependencies):
            if isinstance(item, (str, os.PathLike)):
                names.append(f"file:{Path(item).name}")
            else:
                names.append(f"callable:{_callable_name(item)}")
        payload = _canonical({"sourceless_dependencies": sorted(names)})
        return "sha256-packaged:" + hashlib.sha256(
            payload.encode("utf-8")
        ).hexdigest()
    sources = []
    for item in (function, *dependencies):
        if isinstance(item, (str, os.PathLike)):
            path = Path(item)
            if not path.is_file():
                raise ValueError(
                    f"文件源码依赖不存在 {path}，拒绝生成无来源版本"
                )
            sources.append({
                "file": str(path),
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            })
            continue
        try:
            source = textwrap.dedent(inspect.getsource(item)).strip()
        except (OSError, TypeError) as exc:
            raise ValueError(
                f"无法读取方程源码 {_callable_name(item)}，拒绝生成无来源版本"
            ) from exc
        sources.append({"callable": _callable_name(item), "source": source})
    return _digest(sources)


def _plain(value: object) -> object:
    if is_dataclass(value):
        return _plain(asdict(value))
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _index_unique(items, attr: str, label: str) -> dict[str, object]:
    indexed: dict[str, object] = {}
    for item in items:
        key = getattr(item, attr)
        if key in indexed:
            raise ValueError(f"重复{label}: {key}")
        indexed[key] = item
    return indexed


def _same(left: object, right: object) -> bool:
    if isinstance(left, float) and isinstance(right, (float, int)):
        return math.isclose(left, float(right), rel_tol=1e-12, abs_tol=1e-12)
    return left == right


class MechanismRegistry:
    """不可变机制声明；构造时完成 C0/C1 校验。

    构造完成后所有属性只读：修改节点/参数/机制表会触发
    AttributeError，映射与规范数据类自身也不可变。
    """

    def __init__(
        self,
        *,
        schema_version: int,
        declaration_id: str,
        declaration_version: str,
        microstep_order: tuple[str, ...],
        slice_boundary: str,
        nodes: tuple[NodeSpec, ...],
        parameters: tuple[ParameterSpec, ...],
        exogenous_sources: tuple[ExogenousSourceSpec, ...],
        mechanisms: tuple[MechanismSpec, ...],
    ) -> None:
        self.schema_version = schema_version
        self.declaration_id = declaration_id
        self.declaration_version = declaration_version
        self.microstep_order = tuple(microstep_order)
        self.slice_boundary = slice_boundary

        node_map = _index_unique(tuple(nodes), "node_id", "节点")
        parameter_map = _index_unique(tuple(parameters), "parameter_id", "参数")
        source_map = _index_unique(
            tuple(exogenous_sources),
            "source_id",
            "外生源",
        )
        mechanism_map = _index_unique(
            tuple(mechanisms),
            "mechanism_id",
            "机制",
        )
        self.nodes = MappingProxyType(node_map)
        self.parameters = MappingProxyType(parameter_map)
        self.exogenous_sources = MappingProxyType(source_map)
        self.mechanisms = MappingProxyType(mechanism_map)

        output_map: dict[str, MechanismSpec] = {}
        for mechanism in mechanisms:
            if mechanism.output in output_map:
                raise ValueError(f"节点存在多个写者: {mechanism.output}")
            output_map[mechanism.output] = mechanism
        self._by_output = MappingProxyType(output_map)

        c0_issues = self._c0_issues()
        if c0_issues:
            raise ValueError("C0 声明不完整: " + "; ".join(c0_issues))
        c1_issues = self._c1_issues()
        if c1_issues:
            raise ValueError("C1 结构最小性失败: " + "; ".join(c1_issues))
        object.__setattr__(self, "_frozen", True)

    def __setattr__(self, name: str, value: object) -> None:
        """冻结构造完成后的全部属性（见类 docstring）。"""
        if getattr(self, "_frozen", False):
            raise AttributeError("MechanismRegistry 不可变，禁止修改属性")
        object.__setattr__(self, name, value)

    def validate_c0(self) -> tuple[str, ...]:
        """返回 C0 问题；注册表不可变，正常实例恒为空。"""
        return tuple(self._c0_issues())

    def validate_c1(self) -> tuple[str, ...]:
        """重新执行全部 C1 见证并返回问题。"""
        return tuple(self._c1_issues())

    def mechanism_for(self, output: str) -> MechanismSpec:
        """按输出节点取得其唯一结构方程。"""
        try:
            return self._by_output[output]
        except KeyError as exc:
            raise KeyError(f"节点无注册机制: {output}") from exc

    def evaluate(
        self,
        target: str,
        parent_values: Mapping[str, object],
        *,
        random_values: Mapping[str, object] | None = None,
    ) -> object:
        """以显式父值和随机源值执行一个注册方程。

        target 语义为输出节点 ID（mechanism_id 查询仅为调试回退，
        两者当前无交集）。父值按节点声明值域校验，越界抛 ValueError；
        父集/随机源集与声明不一致抛 KeyError（fail-closed）。
        """
        mechanism = self.mechanisms.get(target)
        if mechanism is None:
            mechanism = self.mechanism_for(target)

        expected_parents = {parent.parent for parent in mechanism.parents}
        actual_parents = set(parent_values)
        if actual_parents != expected_parents:
            missing = sorted(expected_parents - actual_parents)
            extra = sorted(actual_parents - expected_parents)
            raise KeyError(
                f"{mechanism.mechanism_id} 父输入不匹配: "
                f"缺少={missing}, 多余={extra}"
            )

        expected_sources = {item.source for item in mechanism.random_sources}
        supplied_sources = dict(random_values or {})
        if set(supplied_sources) != expected_sources:
            missing = sorted(expected_sources - set(supplied_sources))
            extra = sorted(set(supplied_sources) - expected_sources)
            raise KeyError(
                f"{mechanism.mechanism_id} 随机输入不匹配: "
                f"缺少={missing}, 多余={extra}"
            )

        kwargs: dict[str, object] = {}
        for parent in mechanism.parents:
            value = parent_values[parent.parent]
            self._require_value(self.nodes[parent.parent].value, value, parent.parent)
            kwargs[parent.argument] = value
        for binding in mechanism.parameters:
            parameter = self.parameters[binding.parameter]
            kwargs[binding.argument] = parameter.value
        for binding in mechanism.random_sources:
            kwargs[binding.argument] = supplied_sources[binding.source]

        output = mechanism.function(**kwargs)
        self._require_value(
            self.nodes[mechanism.output].value,
            output,
            mechanism.output,
        )
        return output

    def snapshot(self) -> dict[str, object]:
        """返回默认值已展开、可确定性序列化的声明快照。"""
        nodes = {
            node_id: self._node_snapshot(spec)
            for node_id, spec in sorted(self.nodes.items())
        }
        parameters = {
            parameter_id: self._parameter_snapshot(spec)
            for parameter_id, spec in sorted(self.parameters.items())
        }
        sources = {
            source_id: self._source_snapshot(spec)
            for source_id, spec in sorted(self.exogenous_sources.items())
        }
        mechanisms = {
            mechanism_id: self._mechanism_snapshot(spec)
            for mechanism_id, spec in sorted(self.mechanisms.items())
        }
        declaration = {
            "id": self.declaration_id,
            "version": self.declaration_version,
            "microstep_order": list(self.microstep_order),
            "slice_boundary": self.slice_boundary,
        }
        core = {
            "schema_version": self.schema_version,
            "declaration": declaration,
            "nodes": nodes,
            "parameters": parameters,
            "exogenous_sources": sources,
            "mechanisms": mechanisms,
        }
        declaration = {**declaration, "hash": _digest(core)}
        return {
            "version": self.schema_version,
            "schema_version": self.schema_version,
            "comment": (
                "AUTO-GENERATED from the ASCEND_MECHANISMS registry "
                "(weather/mechanisms.py + space/mechanisms.py slices); "
                "do not edit by hand. variables/edges are the research "
                "graph projection."
            ),
            "declaration": declaration,
            "nodes": nodes,
            "parameters": parameters,
            "exogenous_sources": sources,
            "mechanisms": mechanisms,
            "variables": self._variable_projection(),
            "edges": self._edge_projection(),
        }

    def to_json(self) -> str:
        """返回稳定排序、末尾带换行的 JSON。"""
        return json.dumps(
            self.snapshot(),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        ) + "\n"

    def _c0_issues(self) -> list[str]:
        issues: list[str] = []
        if self.schema_version < 1:
            issues.append("schema_version 必须为正整数")
        for label, value in (
            ("declaration_id", self.declaration_id),
            ("declaration_version", self.declaration_version),
            ("slice_boundary", self.slice_boundary),
        ):
            if not isinstance(value, str) or not value.strip():
                issues.append(f"{label} 不能为空")
        if not self.microstep_order or len(set(self.microstep_order)) != len(
            self.microstep_order
        ):
            issues.append("microstep_order 必须非空且无重复")
        microsteps = set(self.microstep_order)
        microstep_index = {
            name: index for index, name in enumerate(self.microstep_order)
        }

        for node_id, node in self.nodes.items():
            if not node_id or node.role not in _NODE_ROLES:
                issues.append(f"{node_id}: 非法节点 role={node.role!r}")
            if node.origin not in _NODE_ORIGINS:
                issues.append(f"{node_id}: 非法 origin={node.origin!r}")
            issues.extend(self._validate_instance(node_id, node.instance_domain))
            issues.extend(self._validate_domain(node_id, node.value))
            issues.extend(self._validate_state(node_id, node.state))
            issues.extend(self._validate_update(node_id, node.update, microsteps))
            issues.extend(self._validate_access(node_id, node.access))
            issues.extend(self._validate_math(node_id, node.math))

        for parameter_id, parameter in self.parameters.items():
            if not parameter_id or parameter.value_type not in _VALUE_KINDS:
                issues.append(
                    f"{parameter_id}: 非法参数类型 {parameter.value_type!r}"
                )
            if not parameter.unit or not parameter.version or not parameter.source:
                issues.append(f"{parameter_id}: 参数 unit/version/source 不能为空")
            if (
                parameter.value_type in ("enum", "boolean", "string")
                and parameter.bounds is not None
            ):
                issues.append(f"{parameter_id}: 非数值参数不应声明 bounds")
            if parameter.bounds is not None:
                if len(parameter.bounds) != 2:
                    issues.append(f"{parameter_id}: 参数 bounds 必须恰为二元")
                else:
                    lo, hi = parameter.bounds
                    if lo > hi:
                        issues.append(f"{parameter_id}: 参数 bounds 倒置")
            issues.extend(
                self._validate_parameter_value(parameter_id, parameter)
            )

        for source_id, source in self.exogenous_sources.items():
            if not source_id or not source.distribution:
                issues.append(f"{source_id}: 外生源标识/分布不能为空")
            if source.draw_microstep not in microsteps:
                issues.append(f"{source_id}: 外生源微步未声明")
            if not source.address_template:
                issues.append(f"{source_id}: 随机地址模板不能为空")

        for mechanism_id, mechanism in self.mechanisms.items():
            if not mechanism_id or not mechanism.equation.strip():
                issues.append(f"{mechanism_id}: 机制标识/方程不能为空")
            if mechanism.output not in self.nodes:
                issues.append(f"{mechanism_id}: 输出节点未声明 {mechanism.output}")
                continue
            if self.nodes[mechanism.output].origin != "mechanism":
                issues.append(f"{mechanism_id}: 输出节点不是 mechanism origin")
            if not mechanism.boundary_cases:
                issues.append(f"{mechanism_id}: 未声明边界情况")

            seen_parents: set[str] = set()
            for parent in mechanism.parents:
                if parent.parent in seen_parents:
                    issues.append(f"{mechanism_id}: 重复父模板 {parent.parent}")
                seen_parents.add(parent.parent)
                if parent.parent not in self.nodes:
                    issues.append(f"{mechanism_id}: 父节点未声明 {parent.parent}")
                    continue
                if parent.source_microstep not in microsteps:
                    issues.append(
                        f"{mechanism_id}: 父 {parent.parent} 的源微步未声明"
                    )
                elif parent.source_microstep != self.nodes[parent.parent].update.microstep:
                    issues.append(
                        f"{mechanism_id}: 父 {parent.parent} 的源微步 "
                        f"{parent.source_microstep} != 父节点自身更新微步 "
                        f"{self.nodes[parent.parent].update.microstep}"
                    )
                if parent.lag < 0:
                    issues.append(f"{mechanism_id}: 父 {parent.parent} lag < 0")
                if parent.lipschitz < 0:
                    issues.append(f"{mechanism_id}: 父 {parent.parent} L < 0")
                if parent.analysis_role not in _ANALYSIS_ROLES:
                    issues.append(
                        f"{mechanism_id}: 非法 analysis_role={parent.analysis_role!r}"
                    )
                output_step = self.nodes[mechanism.output].update.microstep
                if (
                    parent.lag == 0
                    and parent.source_microstep in microstep_index
                    and output_step in microstep_index
                    and microstep_index[parent.source_microstep]
                    >= microstep_index[output_step]
                ):
                    issues.append(
                        f"{mechanism_id}: 同帧父 {parent.parent} 未位于更早微步"
                    )
                for field_name in (
                    "argument",
                    "entity_relation",
                    "aggregation",
                    "broadcast",
                    "boundary_operator",
                    "guard",
                    "metric",
                    "valid_domain",
                ):
                    if not getattr(parent, field_name):
                        issues.append(
                            f"{mechanism_id}: 父 {parent.parent} 缺 {field_name}"
                        )

            for binding in mechanism.parameters:
                if binding.parameter not in self.parameters:
                    issues.append(
                        f"{mechanism_id}: 参数未声明 {binding.parameter}"
                    )
            for binding in mechanism.random_sources:
                if binding.source not in self.exogenous_sources:
                    issues.append(
                        f"{mechanism_id}: 外生源未声明 {binding.source}"
                    )
            issues.extend(self._validate_signature(mechanism))
            issues.extend(self._validate_witnesses(mechanism))
            try:
                _source_version(mechanism.function, mechanism.source_dependencies)
            except ValueError as exc:
                issues.append(f"{mechanism_id}: {exc}")

        produced = set(self._by_output)
        for node_id, node in self.nodes.items():
            if node.origin == "mechanism" and node_id not in produced:
                issues.append(f"{node_id}: mechanism 节点没有写者")
            if node.origin == "slice_boundary" and node_id in produced:
                issues.append(f"{node_id}: slice_boundary 节点不应有写者")
        return issues

    def _c1_issues(self) -> list[str]:
        issues: list[str] = []
        for mechanism in self.mechanisms.values():
            parents = {item.parent for item in mechanism.parents}
            witnessed = {item.parent for item in mechanism.witnesses}
            missing = sorted(parents - witnessed)
            extra = sorted(witnessed - parents)
            if missing or extra:
                issues.append(
                    f"{mechanism.mechanism_id}: 见证覆盖缺少={missing}, 多余={extra}"
                )
                continue
            for witness in mechanism.witnesses:
                inputs = dict(witness.inputs)
                if len(inputs) != len(witness.inputs) or set(inputs) != parents:
                    issues.append(
                        f"{mechanism.mechanism_id}/{witness.label}: "
                        "见证上下文必须逐父唯一且完整"
                    )
                    continue
                alternate = dict(inputs)
                alternate[witness.parent] = witness.alternate_value
                if _same(inputs[witness.parent], witness.alternate_value):
                    issues.append(
                        f"{mechanism.mechanism_id}/{witness.label}: 父值未改变"
                    )
                    continue
                try:
                    first = self.evaluate(mechanism.output, inputs)
                    second = self.evaluate(mechanism.output, alternate)
                except (KeyError, TypeError, ValueError) as exc:
                    issues.append(
                        f"{mechanism.mechanism_id}/{witness.label}: {exc}"
                    )
                    continue
                expected_first, expected_second = witness.expected_outputs
                if not _same(first, expected_first) or not _same(
                    second,
                    expected_second,
                ):
                    issues.append(
                        f"{mechanism.mechanism_id}/{witness.label}: "
                        f"输出 {(first, second)!r} != "
                        f"预期 {witness.expected_outputs!r}"
                    )
                elif _same(first, second):
                    issues.append(
                        f"{mechanism.mechanism_id}/{witness.label}: 输出未变化"
                    )
        return issues

    @staticmethod
    def _validate_instance(
        node_id: str,
        spec: InstanceDomain,
    ) -> list[str]:
        if not spec.kind or not spec.creation or not spec.destruction:
            return [f"{node_id}: 实例域 kind/creation/destruction 不能为空"]
        if len(set(spec.axes)) != len(spec.axes):
            return [f"{node_id}: 实例域 axes 重复"]
        return []

    @staticmethod
    def _validate_domain(node_id: str, spec: ValueDomain) -> list[str]:
        issues: list[str] = []
        if spec.kind not in _VALUE_KINDS:
            issues.append(f"{node_id}: 非法值类型 {spec.kind!r}")
        if not spec.unit or not spec.missing or not spec.quantization:
            issues.append(f"{node_id}: unit/missing/quantization 不能为空")
        if spec.bounds is not None:
            if len(spec.bounds) != 2:
                issues.append(f"{node_id}: bounds 必须恰为二元")
            elif spec.bounds[0] > spec.bounds[1]:
                issues.append(f"{node_id}: bounds 倒置")
        if spec.kind == "enum" and not spec.choices:
            issues.append(f"{node_id}: enum choices 不能为空")
        if spec.kind != "enum" and spec.choices:
            issues.append(f"{node_id}: 非 enum 不应声明 choices")
        if spec.kind in ("enum", "boolean", "string") and spec.bounds is not None:
            issues.append(f"{node_id}: 非数值类型不应声明 bounds")
        return issues

    @staticmethod
    def _validate_state(node_id: str, spec: StateOwnership) -> list[str]:
        if not spec.reconstruction:
            return [f"{node_id}: reconstruction 不能为空"]
        return []

    @staticmethod
    def _validate_update(
        node_id: str,
        spec: UpdateContract,
        microsteps: set[str],
    ) -> list[str]:
        issues = []
        if spec.microstep not in microsteps:
            issues.append(f"{node_id}: 更新微步未声明 {spec.microstep!r}")
        for name in ("schedule", "when_not_updated", "writer", "merge_rule"):
            if not getattr(spec, name):
                issues.append(f"{node_id}: update.{name} 不能为空")
        return issues

    @staticmethod
    def _validate_access(node_id: str, spec: AccessPolicy) -> list[str]:
        unknown = sorted(set(spec.interventions) - _INTERVENTIONS)
        issues = [f"{node_id}: 非法干预权限 {unknown}"] if unknown else []
        if not spec.observation_protocols:
            issues.append(f"{node_id}: 观测权限不能为空")
        return issues

    @staticmethod
    def _validate_math(node_id: str, spec: MathMetadata) -> list[str]:
        issues = []
        if spec.error_budget < 0:
            issues.append(f"{node_id}: error_budget < 0")
        if not math.isfinite(spec.error_budget):
            issues.append(f"{node_id}: error_budget 必须有限")
        if not spec.metric or not spec.valid_domain:
            issues.append(f"{node_id}: math metric/valid_domain 不能为空")
        return issues

    @staticmethod
    def _validate_parameter_value(
        parameter_id: str,
        parameter: ParameterSpec,
    ) -> list[str]:
        """按声明类型校验参数当前值（bounds=None 时同样执行）。"""
        value = parameter.value
        if parameter.value_type == "boolean":
            if not isinstance(value, bool):
                return [
                    f"{parameter_id}: 值 {value!r} 与声明类型 boolean 不符"
                ]
            return []
        if isinstance(value, bool):
            return [f"{parameter_id}: bool 不属于参数值域 {parameter.value_type}"]
        type_ok = {
            "float": isinstance(value, (int, float)),
            "integer": isinstance(value, int),
            "enum": isinstance(value, str),
            "string": isinstance(value, str),
        }.get(parameter.value_type, False)
        if not type_ok:
            return [
                f"{parameter_id}: 值 {value!r} 与声明类型 "
                f"{parameter.value_type} 不符"
            ]
        if isinstance(value, float) and not math.isfinite(value):
            return [f"{parameter_id}: 参数值必须有限"]
        if (
            parameter.value_type in ("float", "integer")
            and parameter.bounds is not None
        ):
            lo, hi = parameter.bounds
            if not lo <= value <= hi:
                return [
                    f"{parameter_id}: 参数当前值 {value!r} 超出 bounds "
                    f"[{lo}, {hi}]"
                ]
        return []

    @staticmethod
    def _validate_witnesses(mechanism: MechanismSpec) -> list[str]:
        """C1 见证形状校验：输出恰为二元、label 非空且机制内唯一。"""
        issues: list[str] = []
        labels: set[str] = set()
        for witness in mechanism.witnesses:
            if not witness.label:
                issues.append(f"{mechanism.mechanism_id}: 见证 label 不能为空")
                continue
            if witness.label in labels:
                issues.append(
                    f"{mechanism.mechanism_id}: 见证 label 重复 {witness.label}"
                )
            labels.add(witness.label)
            if len(witness.expected_outputs) != 2:
                issues.append(
                    f"{mechanism.mechanism_id}/{witness.label}: "
                    "expected_outputs 必须恰为二元"
                )
        return issues

    @staticmethod
    def _validate_signature(mechanism: MechanismSpec) -> list[str]:
        expected = [item.argument for item in mechanism.parents]
        expected.extend(item.argument for item in mechanism.parameters)
        expected.extend(item.argument for item in mechanism.random_sources)
        signature = inspect.signature(mechanism.function)
        actual = []
        for parameter in signature.parameters.values():
            if parameter.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
                inspect.Parameter.POSITIONAL_ONLY,
            ):
                return [
                    f"{mechanism.mechanism_id}: 函数签名不允许变参或位置专用参数"
                ]
            if parameter.default is not inspect.Parameter.empty:
                return [f"{mechanism.mechanism_id}: 函数签名不允许隐式默认值"]
            actual.append(parameter.name)
        if actual != expected:
            return [
                f"{mechanism.mechanism_id}: 函数签名 {actual} != 声明 {expected}"
            ]
        return []

    @staticmethod
    def _require_value(spec: ValueDomain, value: object, label: str) -> None:
        valid_type = {
            "float": isinstance(value, (int, float)) and not isinstance(value, bool),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "enum": value in spec.choices,
            "boolean": isinstance(value, bool),
            "string": isinstance(value, str),
        }.get(spec.kind, False)
        if not valid_type:
            raise ValueError(f"{label}={value!r} 不属于声明值域 {spec.kind}")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{label}={value!r} 不属于有限值域")
        if spec.kind in ("float", "integer") and spec.bounds is not None:
            lo, hi = spec.bounds
            if not lo <= value <= hi:
                raise ValueError(
                    f"{label}={value!r} 超出声明值域 [{lo}, {hi}]"
                )

    @staticmethod
    def _node_snapshot(spec: NodeSpec) -> dict[str, object]:
        return {
            "role": spec.role,
            "origin": spec.origin,
            "instance_domain": _plain(spec.instance_domain),
            "value": _plain(spec.value),
            "state": _plain(spec.state),
            "update": _plain(spec.update),
            "access": _plain(spec.access),
            "math": _plain(spec.math),
        }

    @staticmethod
    def _parameter_snapshot(spec: ParameterSpec) -> dict[str, object]:
        data = _plain(spec)
        del data["parameter_id"]
        return data

    @staticmethod
    def _source_snapshot(spec: ExogenousSourceSpec) -> dict[str, object]:
        data = _plain(spec)
        del data["source_id"]
        return data

    def _mechanism_snapshot(self, spec: MechanismSpec) -> dict[str, object]:
        equation_version = _source_version(spec.function, spec.source_dependencies)
        parents = [_plain(item) for item in spec.parents]
        parameters = [_plain(item) for item in spec.parameters]
        random_sources = [_plain(item) for item in spec.random_sources]
        resolved_version = _digest({
            "mechanism_id": spec.mechanism_id,
            "output": spec.output,
            "equation": spec.equation,
            "equation_version": equation_version,
            "parents": parents,
            "parameters": [
                {
                    "binding": _plain(binding),
                    "spec": self._parameter_snapshot(
                        self.parameters[binding.parameter]
                    ),
                }
                for binding in spec.parameters
            ],
            "random_sources": [
                {
                    "binding": _plain(binding),
                    "spec": self._source_snapshot(
                        self.exogenous_sources[binding.source]
                    ),
                }
                for binding in spec.random_sources
            ],
            "boundary_cases": list(spec.boundary_cases),
        })
        witnesses = []
        for witness in spec.witnesses:
            first = dict(witness.inputs)
            second = dict(first)
            second[witness.parent] = witness.alternate_value
            witnesses.append({
                "label": witness.label,
                "parent": witness.parent,
                "inputs_a": first,
                "inputs_b": second,
                "expected_outputs": list(witness.expected_outputs),
                "equation_version": equation_version,
            })
        return {
            "output": spec.output,
            "equation": spec.equation,
            "function": _callable_name(spec.function),
            "source_dependencies": [
                str(item) if isinstance(item, (str, os.PathLike))
                else _callable_name(item)
                for item in spec.source_dependencies
            ],
            "equation_version": equation_version,
            "resolved_version": resolved_version,
            "parents": parents,
            "parameters": parameters,
            "random_sources": random_sources,
            "boundary_cases": list(spec.boundary_cases),
            "witnesses": witnesses,
        }

    def _variable_projection(self) -> dict[str, object]:
        projection = {}
        for node_id, node in sorted(self.nodes.items()):
            projection[node_id] = {
                "domain": (
                    "continuous" if node.value.kind == "float" else "discrete"
                ),
                "exogenous": node.origin == "slice_boundary",
                "bounds": (
                    list(node.value.bounds)
                    if node.value.bounds is not None
                    else None
                ),
                "eps": node.math.error_budget,
            }
        return projection

    def _edge_projection(self) -> list[dict[str, object]]:
        edges = []
        for mechanism in sorted(
            self.mechanisms.values(),
            key=lambda item: item.mechanism_id,
        ):
            for parent in mechanism.parents:
                edges.append({
                    "parent": parent.parent,
                    "child": mechanism.output,
                    "role": "structural",
                    "analysis_role": parent.analysis_role,
                    "L": parent.lipschitz,
                    "equation": mechanism.mechanism_id,
                    "lag": parent.lag,
                    "source_microstep": parent.source_microstep,
                    "spatial_offsets": _plain(parent.spatial_offsets),
                    "boundary_operator": parent.boundary_operator,
                })
        return edges
