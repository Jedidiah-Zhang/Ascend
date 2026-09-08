"""神迹系统终端指令组 — do 指令（研究者原子化执行干预）。

指令（与 net 研究 API 同源，均登记进同一神迹表）:

    do list                                    列出当前有效神迹
    do value <node> <value> [cx cy] [at T] [dur N]
        值神迹：单帧（默认）或窗口 [T, T+N)；T 默认下一 tick。
    do mech <node> <mechanism_id> [cx cy] [at T] [dur N]
        机制神迹：以已登记机制替换目标节点公式（默认长期）。
    do param <parameter_id> <value> [at T]
        参数神迹：环境变化，长期生效，T 默认下一 tick。
    do clear <node|param|feature> <target> [cx cy]
        清除指定神迹。

帧基准为时间模块 tick；chunk 分量缺省坐标用 default_chunk。
"""

from __future__ import annotations

from ascend.causal import MiracleRecord

from .result import CommandResult

_HELP_DO = """\
  do value <node> <value> [cx cy] [at T] [dur N]   单帧/窗口值神迹（T 默认下一 tick，dur 默认 1）
  do mech <node> <mechanism_id> [cx cy] [at T]     机制神迹（以已登记机制替换公式，默认长期）
  do param <parameter_id> <value> [at T]           参数神迹（环境变化，长期生效）
  do clear <node|param|feature> <target> [cx cy]   清除神迹
  do list                                           列出当前有效神迹"""


class MiracleCommandsMixin:
    """神迹系统 do 指令组（接入 CommandExecutor）。"""

    # ── 入口 ──────────────────────────────────────────────

    def _h_do(self, args: list[str]) -> CommandResult:
        if not args:
            return CommandResult(success=False, output=_HELP_DO)
        if self._miracle_table is None:
            return CommandResult(
                success=False,
                output="神迹表未挂载（神迹系统不可用）",
            )
        sub = args[0].lower()
        rest = args[1:]
        try:
            if sub == "list":
                return CommandResult(success=True, output=self._cmd_do_list())
            if sub == "value":
                return self._cmd_do_value(rest)
            if sub == "mech":
                return self._cmd_do_mech(rest)
            if sub == "param":
                return self._cmd_do_param(rest)
            if sub == "clear":
                return self._cmd_do_clear(rest)
        except (ValueError, KeyError) as exc:
            return CommandResult(success=False, output=str(exc))
        return CommandResult(success=False, output=_HELP_DO)

    # ── 子指令 ────────────────────────────────────────────

    def _cmd_do_value(self, args: list[str]) -> CommandResult:
        node, rest = self._pop(args)
        if node is None:
            raise ValueError("do value 需要目标节点")
        registry = self._miracle_table.registry
        if node not in registry.nodes:
            raise ValueError(f"目标分量未声明: {node}")
        node_spec = registry.nodes[node]
        value_text, rest = self._pop(rest)
        if value_text is None:
            raise ValueError("do value 需要替换值")
        value = self._coerce(value_text, node_spec.value.kind)
        frame, duration, instance = self._parse_target(rest, node)
        self._miracle_table.commit(MiracleRecord(
            target_space="node", target=node, instance=instance,
            rep="value", value=value,
            frame_t0=frame, duration=duration,
            applied_at=self._clock.time,
        ))
        return CommandResult(success=True, output=f"神迹已登记: node={node} value={value}")

    def _cmd_do_mech(self, args: list[str]) -> CommandResult:
        node, rest = self._pop(args)
        if node is None:
            raise ValueError("do mech 需要目标节点")
        mech_id, rest = self._pop(rest)
        if mech_id is None:
            raise ValueError("do mech 需要已登记机制 id")
        registry = self._miracle_table.registry
        mechanism = registry.mechanisms.get(mech_id)
        if mechanism is None:
            raise ValueError(f"机制未登记: {mech_id}")
        frame, duration, instance = self._parse_target(
            rest, node, default_duration=None,
        )
        self._miracle_table.commit(MiracleRecord(
            target_space="node", target=node, instance=instance,
            rep="mechanism", mechanism=mechanism,
            frame_t0=frame, duration=duration,
            applied_at=self._clock.time,
        ))
        return CommandResult(success=True, output=f"神迹已登记: node={node} 机制={mech_id}")

    def _cmd_do_param(self, args: list[str]) -> CommandResult:
        param, rest = self._pop(args)
        if param is None:
            raise ValueError("do param 需要参数 id")
        registry = self._miracle_table.registry
        if param not in registry.parameters:
            raise ValueError(f"目标参数未声明: {param}")
        param_spec = registry.parameters[param]
        value_text, rest = self._pop(rest)
        if value_text is None:
            raise ValueError("do param 需要替换值")
        value = self._coerce(value_text, param_spec.value_type)
        frame, rest = self._frame_arg(rest)
        if frame is None:
            frame = self._clock.time + 1
        if rest:
            raise ValueError(f"do param 不接受多余参数: {' '.join(rest)}")
        self._miracle_table.commit(MiracleRecord(
            target_space="parameter", target=param,
            rep="value", value=value, frame_t0=frame,
            applied_at=self._clock.time,
        ))
        return CommandResult(success=True, output=f"环境变化已登记: parameter={param}={value}")

    def _cmd_do_clear(self, args: list[str]) -> CommandResult:
        space, rest = self._pop(args)
        target, rest = self._pop(rest)
        if space not in ("node", "param", "feature") or target is None:
            raise ValueError("do clear <node|param|feature> <target> [cx cy]")
        instance = ()
        if space in ("node", "feature"):
            registry = self._miracle_table.registry
            is_global = (
                space == "param"
                or (
                    space == "node"
                    and registry.nodes[target].instance_domain.kind
                    == "global_singleton"
                )
            )
            if not is_global:
                instance = self._default_chunk
            if rest:
                instance = (int(rest[0]), int(rest[1]))
        space_map = {"node": "node", "param": "parameter", "feature": "field_feature"}
        cleared = self._miracle_table.clear(space_map[space], target, instance)
        if not cleared:
            return CommandResult(success=False, output=f"未找到神迹: {space} {target}")
        return CommandResult(success=True, output=f"神迹已清除: {space} {target}")

    def _cmd_do_list(self) -> str:
        snapshot = self._miracle_table.snapshot()
        lines = ["当前有效神迹:"]
        any_active = False
        for group, label in (
            ("values", "值神迹"), ("mechanisms", "机制神迹"),
            ("parameters", "环境变化"), ("features", "特征核控制"),
        ):
            records = snapshot.get(group, [])
            if not records:
                continue
            any_active = True
            for record in records:
                lines.append(
                    f"  [{label}] {record['target']} "
                    f"实例={record['instance']} @t{record['frame_t0']} "
                    f"seq={record['seq']}"
                )
        if not any_active:
            lines.append("  （无）")
        return "\n".join(lines)

    # ── 解析辅助 ──────────────────────────────────────────

    @staticmethod
    def _pop(args: list[str]) -> tuple[str | None, list[str]]:
        if not args:
            return None, args
        return args[0], args[1:]

    @staticmethod
    def _coerce(text: str, kind: str = "float") -> object:
        """按目标值域类型解析值（integer 域保持 int，enum/string 保持文本）。"""
        if kind in ("integer",):
            try:
                return int(text)
            except ValueError:
                raise ValueError(f"值 {text!r} 不属于声明值域 integer") from None
        if kind in ("float",):
            try:
                return float(text)
            except ValueError:
                raise ValueError(f"值 {text!r} 不属于声明值域 float") from None
        return text

    @staticmethod
    def _frame_arg(args: list[str]) -> tuple[int | None, list[str]]:
        """解析 `at <tick>`；缺省返回 None（调用方按下一 tick 处理）。"""
        rest = list(args)
        if len(rest) >= 2 and rest[0] == "at":
            return int(rest[1]), rest[2:]
        return None, rest

    def _parse_target(
        self, args: list[str], node: str,
        *,
        default_duration: int | None = 1,
    ) -> tuple[int, int | None, tuple]:
        """解析 (生效帧, 时长, 实例) 三要素。

        chunk 分量缺省坐标用 default_chunk；帧缺省下一 tick；
        值神迹缺省 duration=1（节点神迹），机制神迹缺省长期。
        """
        registry = self._miracle_table.registry
        node_spec = registry.nodes[node]
        is_global = node_spec.instance_domain.kind == "global_singleton"
        instance: tuple = ()
        rest = list(args)
        if not is_global:
            coords = None
            if len(rest) >= 2 and rest[0].lstrip("-").isdigit() \
                    and rest[1].lstrip("-").isdigit():
                coords = (int(rest[0]), int(rest[1]))
                rest = rest[2:]
            instance = coords if coords is not None else self._default_chunk
        frame, rest = self._frame_arg(rest)
        if frame is None:
            frame = self._clock.time + 1  # 默认下一 tick 生效
        duration: int | None = None
        if len(rest) >= 2 and rest[0] == "dur":
            duration = int(rest[1])
        if duration is None:
            duration = default_duration
        return frame, duration, instance