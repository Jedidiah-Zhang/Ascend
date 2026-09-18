"""元模型 — 六种声明的数据结构（世界架构 00 §5）。

六种声明：**实例与关系、槽位、机制、时间、随机、不变量**。模块包是它们
的集合 + 参数 + 旋钮 + 证据义务；世界装配 = 模块集 + 参数 + 旋钮 + 调度 + 种子。

本模块只定义数据形态与字段级不变量；引用存在性、单写者、无环、见证覆盖
一类全局校验在 ``ascend.world.compile``（编译器）完成。

声明载体是 Python 冻结数据类而非 JSON：机制必须绑定实现函数，函数无法
序列化；**内容**（地形/气候/群系/天气参数）仍在 ``data/*.json``，改内容
不改代码（数据与算法拆分）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

from ascend.world.kernel import TABLE_BITS

__all__ = [
    "ARITHMETIC_DOMAINS",
    "INSTANCE_KINDS",
    "PERSIST_CLASSES",
    "TIME_MODES",
    "AddressUse",
    "Arithmetic",
    "InstanceDecl",
    "InvariantDecl",
    "KnobDecl",
    "MechanismDecl",
    "ModulePack",
    "ParameterDecl",
    "Parent",
    "Permissions",
    "RelationDecl",
    "Schedule",
    "SlotDecl",
    "ValueDomain",
    "When",
    "Witness",
    "WorldSpec",
]

INSTANCE_KINDS = ("lattice", "entity", "global")
PERSIST_CLASSES = ("state", "derived", "parameter", "external")
TIME_MODES = ("phase", "period", "event")
ARITHMETIC_DOMAINS = ("fixed", "table", "island")
BOUNDARY_KINDS = ("reject", "clamp", "wrap", "identity")
AGGREGATIONS = ("identity", "sum", "mean", "min", "max")


def _require_ident(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} 必须为非空字符串: {value!r}")
    if not all(part and part[0].islower() for part in value.split(".")):
        raise ValueError(f"{label} 必须为点分小写标识: {value!r}")


@dataclass(frozen=True, slots=True)
class ValueDomain:
    """槽位值域：类型、字宽、范围、单位、缺失与枚举选择。"""

    kind: str = "any"
    bits: int | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    unit: str = ""
    missing: object | None = None
    choices: tuple[object, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in ("any", "int", "float", "bool", "enum"):
            raise ValueError(f"未知值域类型: {self.kind!r}")
        if self.kind == "int":
            if type(self.bits) is not int or self.bits <= 0:
                raise ValueError(f"int 值域必须给出正整数 bits: {self.bits!r}")
        if self.kind == "enum" and not self.choices:
            raise ValueError("enum 值域必须给出 choices")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(
                f"值域上下界倒置: [{self.minimum}, {self.maximum}]"
            )


@dataclass(frozen=True, slots=True)
class Permissions:
    """槽位权限：可干预 / 可观测 / 可记录（WC-3.6 / WC-10）。"""

    intervene: bool = False
    observe: bool = False
    record: bool = False


@dataclass(frozen=True, slots=True)
class InstanceDecl:
    """实例类型：场格（lattice）/ 实体（entity）/ 全局单例（global）。

    Attributes:
        id: 实例类型标识（如 ``lattice.tile``、``global``）。
        kind: 三类之一（``INSTANCE_KINDS``）。
        identity: 身份规则说明（坐标 / 稳定派生 ID / 单例）。
        size: lattice 的固定尺寸；``None`` 表示按 chunk 流式物化。
        parent: 层级父实例 ID（多分辨率）；``None`` 表示顶层。
        ratio: 相对父实例的细化倍率（``parent`` 非空时为正整数）。
        lifecycle: 实体生命期规则说明（创建/销毁条件；实体专用）。
    """

    id: str
    kind: str = "global"
    identity: str = "singleton"
    size: tuple[int, ...] | None = None
    parent: str | None = None
    ratio: int = 1
    lifecycle: str = ""

    def __post_init__(self) -> None:
        _require_ident(self.id, "实例类型 id")
        if self.kind not in INSTANCE_KINDS:
            raise ValueError(f"未知实例类型: {self.kind!r}")
        if self.kind == "lattice":
            if self.size is not None and (
                not self.size
                or any(type(n) is not int or n <= 0 for n in self.size)
            ):
                raise ValueError(f"lattice 尺寸必须为正整数元组: {self.size!r}")
        elif self.size is not None:
            raise ValueError("只有 lattice 实例可声明 size")
        if self.parent is not None:
            if type(self.ratio) is not int or self.ratio <= 0:
                raise ValueError(f"层级倍率必须为正整数: {self.ratio!r}")


@dataclass(frozen=True, slots=True)
class RelationDecl:
    """关系（边）：空间偏移、层级父子、实体链接。

    Attributes:
        id: 关系标识（父引用以它索引进空间偏移集合）。
        kind: ``spatial``（同层偏移）/ ``link``（实体链接）/ ``level``。
        source: 源实例类型 ID（spatial 时与 ``target`` 相同）。
        target: 目标实例类型 ID。
        offsets: 整数偏移元组（spatial 专用）。
        boundary: 边界算子（``BOUNDARY_KINDS``）。
    """

    id: str
    kind: str = "spatial"
    source: str = ""
    target: str = ""
    offsets: tuple[tuple[int, ...], ...] = ()
    boundary: str = "reject"

    def __post_init__(self) -> None:
        _require_ident(self.id, "关系 id")
        if self.kind not in ("spatial", "link", "level"):
            raise ValueError(f"未知关系类型: {self.kind!r}")
        if self.boundary not in BOUNDARY_KINDS:
            raise ValueError(f"未知边界算子: {self.boundary!r}")
        if self.kind == "spatial":
            if not self.source or self.source != self.target:
                raise ValueError("spatial 关系要求 source == target")
            if not self.offsets:
                raise ValueError("spatial 关系必须给出 offsets")
            widths = {len(offset) for offset in self.offsets}
            if len(widths) != 1:
                raise ValueError("offsets 各分量维数必须一致")
        else:
            if self.offsets:
                raise ValueError(f"{self.kind} 关系不得携带 offsets")
            if not self.source or not self.target:
                raise ValueError(f"{self.kind} 关系必须给出 source/target")


@dataclass(frozen=True, slots=True)
class SlotDecl:
    """槽位：挂在实例类型上的类型化量。

    ``persist`` 四类（``PERSIST_CLASSES``）：

    - ``state``：进入 W_t，必须有机制写者（``writer``）；
    - ``derived``：W_t 的纯函数，只读、不得影响未来（``recompute`` 说明）；
    - ``parameter``：由世界装配的参数解析（同名 ParameterDecl 校验）；
    - ``external``：由运行时输入提供（干预/驱动/研究协议写入）。
    """

    id: str
    on: str
    persist: str = "state"
    domain: ValueDomain = ValueDomain()
    permissions: Permissions = Permissions()
    writer: str | None = None
    recompute: str = ""
    initial: object = 0
    # 研究投影元数据（P3b；缺省 = 未认证，投影期 fail-closed）
    role: str = ""
    schedule: str = ""
    quantization: str = ""
    metric: str = "absolute_difference"
    epsilon: float | None = None
    access_interventions: tuple[str, ...] = ()
    research_trace: bool = False
    observation_protocols: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_ident(self.id, "槽位 id")
        _require_ident(self.on, "槽位载体实例 id")
        if self.persist not in PERSIST_CLASSES:
            raise ValueError(f"未知持久类: {self.persist!r}")
        if self.persist in ("state", "derived") and not self.writer:
            raise ValueError("state/derived 槽位必须声明 writer")
        if self.persist == "derived" and not self.recompute:
            raise ValueError("derived 槽位必须声明 recompute")
        if self.persist in ("parameter", "external") and self.writer:
            raise ValueError(f"{self.persist} 槽位不得声明 writer")
        if self.epsilon is not None and self.epsilon < 0:
            raise ValueError(f"ε 必须非负: {self.epsilon!r}")
        if self.metric not in ("absolute_difference", "discrete"):
            raise ValueError(f"未知误差度量: {self.metric!r}")
        for kind in self.access_interventions:
            if kind not in ("node", "persistent", "parameter", "field_feature"):
                raise ValueError(f"未知干预种类: {kind!r}")


@dataclass(frozen=True, slots=True)
class Parent:
    """机制的一条父引用（含研究侧模数元数据）。

    Attributes:
        slot: 父槽位 ID。
        argument: 机制实现里的参数名。
        lag: 0 = 同帧读取（本帧更早写入，否则帧初值）；k≥1 = 帧初 k 帧前的值。
        relation: ``same``（同实例）或关系 ID（按偏移取邻居）。
        aggregation: 邻居聚合（``AGGREGATIONS``）。
        analysis_role: 分析角色（forward/inverse/...；研究投影用）。
        valid_domain: 父值有效域描述（研究投影用）。
        modulus_kind: ``linear``（Lipschitz 界）或 ``jump``（有界跳变）。
        lipschitz: 线性边的 Lipschitz 常数；``None`` = 未认证。
        jump_bound: 跳变边的跳幅上界（jump 边必填）。
        metric: 误差度量（``absolute_difference`` / ``discrete``）。
    """

    slot: str
    argument: str
    lag: int = 0
    relation: str = "same"
    aggregation: str = "identity"
    analysis_role: str = "forward"
    valid_domain: str = ""
    modulus_kind: str = "linear"
    lipschitz: float | None = None
    jump_bound: float | None = None
    metric: str = "absolute_difference"

    def __post_init__(self) -> None:
        _require_ident(self.slot, "父槽位 id")
        if not self.argument:
            raise ValueError("父引用必须给出 argument")
        if type(self.lag) is not int or self.lag < 0:
            raise ValueError(f"lag 必须为非负整数: {self.lag!r}")
        if self.aggregation not in AGGREGATIONS:
            raise ValueError(f"未知聚合: {self.aggregation!r}")
        if self.modulus_kind not in ("linear", "jump"):
            raise ValueError(f"未知模数类型: {self.modulus_kind!r}")
        if self.modulus_kind == "linear":
            if self.jump_bound is not None:
                raise ValueError("linear 边不得携带 jump_bound")
            if self.lipschitz is not None and self.lipschitz < 0:
                raise ValueError(
                    f"Lipschitz 常数必须非负: {self.lipschitz!r}"
                )
        else:
            if self.lipschitz is not None:
                raise ValueError("jump 边不得携带 lipschitz")
            if self.jump_bound is None or self.jump_bound <= 0:
                raise ValueError(
                    f"jump 边必须给出正 jump_bound: {self.jump_bound!r}"
                )
        if self.metric not in ("absolute_difference", "discrete"):
            raise ValueError(f"未知误差度量: {self.metric!r}")


@dataclass(frozen=True, slots=True)
class When:
    """机制的时间模式：阶段 / 周期 / 事件（架构 00 §6）。"""

    mode: str = "phase"
    key: str = "main"

    def __post_init__(self) -> None:
        if self.mode not in TIME_MODES:
            raise ValueError(f"未知时间模式: {self.mode!r}")
        if not self.key:
            raise ValueError("时间模式必须给出 key")


@dataclass(frozen=True, slots=True)
class Arithmetic:
    """算术域：定点 / 冻表 / 显式浮点孤岛（WC-4.4）。"""

    domain: str = "fixed"
    bits: int = TABLE_BITS
    rounding: str = "half_even"

    def __post_init__(self) -> None:
        if self.domain not in ARITHMETIC_DOMAINS:
            raise ValueError(f"未知算术域: {self.domain!r}")
        if self.domain == "fixed" and (
            type(self.bits) is not int or self.bits <= 0
        ):
            raise ValueError(f"定点精度必须为正整数: {self.bits!r}")
        if self.rounding != "half_even":
            raise ValueError(f"未知舍入规则: {self.rounding!r}")


@dataclass(frozen=True, slots=True)
class AddressUse:
    """机制消费的随机地址（命名空间 + 用途；全局唯一）。"""

    namespace: str
    purpose: str

    def __post_init__(self) -> None:
        _require_ident(self.namespace, "地址命名空间")
        _require_ident(self.purpose, "地址用途")


@dataclass(frozen=True, slots=True)
class Witness:
    """结构最小性见证：输入 → 输出（编译期重跑，C1）。

    ``inputs`` 以父引用的 ``argument`` 为键；``params`` 提供参数上下文；
    ``seed`` 提供随机上下文。
    """

    label: str
    inputs: Mapping[str, object] = field(default_factory=dict)
    outputs: tuple[object, ...] = ()
    params: Mapping[str, object] = field(default_factory=dict)
    seed: int = 0
    tick: int = 0

    def __post_init__(self) -> None:
        if not self.label:
            raise ValueError("见证必须给出 label")
        if type(self.tick) is not int or self.tick < 0:
            raise ValueError(f"见证 tick 必须为非负整数: {self.tick!r}")


@dataclass(frozen=True, slots=True)
class MechanismDecl:
    """机制：声明式纯函数（父引用 + 方程 + 算术域 + 地址 + 实现）。

    ``impl(ctx) -> value`` 为参考实现（模板求值）；``accelerated`` 为可选的
    加速实现（内核绑定），两者必须逐位一致。
    """

    id: str
    output: str | tuple[str, ...]
    parents: tuple[Parent, ...]
    impl: Callable[..., object]
    when: When = When()
    equation: str = ""
    arithmetic: Arithmetic = Arithmetic()
    address: AddressUse | None = None
    witnesses: tuple[Witness, ...] = ()
    params: tuple[str, ...] = ()
    accelerated: Callable[..., object] | None = None
    scope: str = "instance"
    boundary_cases: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        _require_ident(self.id, "机制 id")
        for slot in self.outputs():
            _require_ident(slot, "机制输出槽位")
        if not callable(self.impl):
            raise ValueError("机制必须绑定参考实现 impl")
        if self.accelerated is not None and not callable(self.accelerated):
            raise ValueError("accelerated 必须可调用")
        if self.scope not in ("instance", "field"):
            raise ValueError(f"未知机制作用域: {self.scope!r}")
        for parameter_id in self.params:
            _require_ident(parameter_id, "机制参数 id")

    def outputs(self) -> tuple[str, ...]:
        """规范化的输出槽位元组。"""
        return (self.output,) if isinstance(self.output, str) else self.output


@dataclass(frozen=True, slots=True)
class InvariantDecl:
    """不变量：对槽位值的声明式断言（reject = 帧失败；record = 留痕）。"""

    id: str
    slot: str
    check: Callable[[object], bool]
    severity: str = "reject"
    message: str = ""

    def __post_init__(self) -> None:
        _require_ident(self.id, "不变量 id")
        _require_ident(self.slot, "不变量槽位")
        if self.severity not in ("reject", "record"):
            raise ValueError(f"未知不变量级别: {self.severity!r}")
        if not callable(self.check):
            raise ValueError("不变量必须绑定 check")


@dataclass(frozen=True, slots=True)
class ParameterDecl:
    """参数声明：默认值、范围、单位（值由世界装配解析）。"""

    id: str
    default: object | None = None
    minimum: object | None = None
    maximum: object | None = None
    unit: str = ""

    def __post_init__(self) -> None:
        _require_ident(self.id, "参数 id")
        if self.default is None and (self.minimum is None or self.maximum is None):
            raise ValueError("无默认值的参数必须给出范围")
        if (
            self.minimum is not None
            and self.maximum is not None
            and self.minimum > self.maximum
        ):
            raise ValueError(f"参数范围倒置: [{self.minimum}, {self.maximum}]")


@dataclass(frozen=True, slots=True)
class KnobDecl:
    """复杂度旋钮：离散取值，世界装配按旋钮配置规模/周期/结构。"""

    id: str
    values: tuple[object, ...]
    default: object | None = None
    note: str = ""

    def __post_init__(self) -> None:
        _require_ident(self.id, "旋钮 id")
        if not self.values:
            raise ValueError("旋钮必须给出候选值")
        if self.default is not None and self.default not in self.values:
            raise ValueError("旋钮默认值必须在候选值内")


@dataclass(frozen=True, slots=True)
class ModulePack:
    """模块包：六种声明 + 参数 + 旋钮 + 证据义务。"""

    id: str
    version: str = "1"
    instances: tuple[InstanceDecl, ...] = ()
    relations: tuple[RelationDecl, ...] = ()
    slots: tuple[SlotDecl, ...] = ()
    mechanisms: tuple[MechanismDecl, ...] = ()
    invariants: tuple[InvariantDecl, ...] = ()
    parameters: tuple[ParameterDecl, ...] = ()
    knobs: tuple[KnobDecl, ...] = ()
    depends_on: tuple[str, ...] = ()
    source_deps: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    notes: str = ""

    def __post_init__(self) -> None:
        _require_ident(self.id, "模块 id")
        if not self.version:
            raise ValueError("模块必须给出版本")

    def declarations(self) -> tuple[object, ...]:
        """六种声明的扁平序列（校验/摘要遍历用）。"""
        return (
            *self.instances,
            *self.relations,
            *self.slots,
            *self.mechanisms,
            *self.invariants,
            *self.parameters,
            *self.knobs,
        )


@dataclass(frozen=True, slots=True)
class Schedule:
    """世界调度：帧内阶段序 + 日历周期（tick 倍数）。

    执行次序：阶段组（按 ``phases`` 顺序）→ 周期组（按 ``periods`` 顺序）
    → 事件组（按触发顺序）。周期在 ``tick % ticks == 0`` 的帧触发。
    """

    phases: tuple[str, ...] = ("main",)
    periods: tuple[tuple[str, int], ...] = ()
    tick_unit: str = "tick"

    def __post_init__(self) -> None:
        if not self.phases:
            raise ValueError("调度必须至少一个阶段")
        if len(set(self.phases)) != len(self.phases):
            raise ValueError("阶段名不得重复")
        seen: set[str] = set()
        for name, ticks in self.periods:
            if not name or name in seen:
                raise ValueError(f"周期名不得重复/为空: {name!r}")
            if type(ticks) is not int or ticks <= 0:
                raise ValueError(f"周期刻度必须为正整数: {ticks!r}")
            seen.add(name)

    def phase_rank(self, key: str) -> int:
        """阶段在帧内的序（未知阶段即拒绝）。"""
        if key not in self.phases:
            raise ValueError(f"未声明的阶段: {key!r}")
        return self.phases.index(key)

    def period_ticks(self, key: str) -> int | None:
        """周期名 → tick 数；未知周期返回 ``None``。"""
        return dict(self.periods).get(key)


@dataclass(frozen=True, slots=True)
class WorldSpec:
    """世界装配：模块集 + 参数 + 旋钮 + 调度 + 种子。"""

    modules: tuple[ModulePack, ...]
    parameters: Mapping[str, object] = field(default_factory=dict)
    knobs: Mapping[str, object] = field(default_factory=dict)
    schedule: Schedule = Schedule()
    seed: int = 0
    contract: str = "world-arch-v0.1"

    def __post_init__(self) -> None:
        if not self.modules:
            raise ValueError("世界装配至少需要一个模块")
        if type(self.seed) is not int:
            raise ValueError(f"世界种子必须为整数: {self.seed!r}")
