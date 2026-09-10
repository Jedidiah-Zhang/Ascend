"""干预执行器终端指令组 — do 指令（研究者原子化执行干预）。

指令（与 net 研究 API 同源：同一干预表、同一校验、同一缺省解析）:

    do list                                          列出当前有效干预
    do value <node> <value> [cx cy] [at T] [dur N]   值干预（缺省单帧）
    do mech <node> <mechanism_id> [cx cy] [at T] [dur N]
                                                     机制干预（缺省长期）
    do param <parameter_id> <value> [at T]           参数干预（环境变化）
    do clear <node|param|feature> <target> [cx cy] [rep value|mechanism]
                                                     清除干预

帧基准为时间模块 tick；缺省下一 tick 生效（``InterventionTable.default_frame``），
chunk 分量缺省坐标用 default_chunk。任何未识别的多余参数都会报错
（fail-closed），不做静默忽略。全部用户可见文案走 i18n。
"""

from __future__ import annotations

from ascend.causal import InterventionRecord, default_duration
from ascend.log import get_logger

from .result import CommandResult

logger = get_logger(__name__)

_SPACE_MAP = {
    "node": "node",
    "param": "parameter",
    "feature": "field_feature",
}
_LIST_GROUPS = (
    ("values", "console.do_label_value"),
    ("mechanisms", "console.do_label_mechanism"),
    ("parameters", "console.do_label_parameter"),
    ("features", "console.do_label_feature"),
)


class InterventionCommandsMixin:
    """干预执行器 do 指令组（接入 CommandExecutor）。"""

    # ── 入口 ──────────────────────────────────────────────

    def _h_do(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        if not args:
            return CommandResult(success=False, output=self._cmd_do_help())
        if self._intervention_table is None:
            return CommandResult(
                success=False, output=t("console.do_unavailable"),
            )
        handlers = {
            "list": lambda _rest: CommandResult(
                success=True, output=self._cmd_do_list(),
            ),
            "value": self._cmd_do_value,
            "mech": self._cmd_do_mech,
            "param": self._cmd_do_param,
            "clear": self._cmd_do_clear,
        }
        handler = handlers.get(args[0].lower())
        if handler is None:
            return CommandResult(success=False, output=self._cmd_do_help())
        try:
            result = handler(args[1:])
        except ValueError as exc:
            return CommandResult(success=False, output=str(exc))
        if result.success:
            # 干预是研究溯源的关键操作：成功路径留结构化日志（与
            # research_do 同字段），使终端登记的干预也可事后复现。
            logger.info("do %s: %s", " ".join(args), result.output)
        return result

    def _cmd_do_help(self) -> str:
        t = self._i18n.t
        return "\n".join((
            f"  do value <node> <value> [cx cy] [at T] [dur N]"
            f"   {t('console.help_do_value')}",
            f"  do mech <node> <mechanism_id> [cx cy] [at T] [dur N]"
            f"   {t('console.help_do_mech')}",
            f"  do param <parameter_id> <value> [at T]"
            f"           {t('console.help_do_param')}",
            f"  do clear <node|param|feature> <target> [cx cy] [rep value|mechanism]"
            f"   {t('console.help_do_clear')}",
            f"  do list                                          "
            f"{t('console.help_do_list')}",
        ))

    # ── 子指令 ────────────────────────────────────────────

    def _cmd_do_value(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        node, rest = self._pop(args)
        if node is None:
            raise ValueError(t("console.do_need_node"))
        registry = self._intervention_table.registry
        if node not in registry.nodes:
            raise ValueError(t("console.do_unknown_node", target=node))
        node_spec = registry.nodes[node]
        value_text, rest = self._pop(rest)
        if value_text is None:
            raise ValueError(t("console.do_need_value"))
        value = self._coerce(
            value_text, node_spec.value.kind, node_spec.value.choices,
        )
        frame, duration, instance = self._parse_target(
            rest, node, default_duration=default_duration("node", "value"),
        )
        self._intervention_table.commit(InterventionRecord(
            target_space="node", target=node, instance=instance,
            rep="value", value=value,
            frame_t0=frame, duration=duration,
        ))
        return CommandResult(
            success=True,
            output=t(
                "console.do_registered_value",
                target=node, value=value,
                window=self._window_text(frame, duration),
            ),
        )

    def _cmd_do_mech(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        node, rest = self._pop(args)
        if node is None:
            raise ValueError(t("console.do_need_node"))
        mech_id, rest = self._pop(rest)
        if mech_id is None:
            raise ValueError(t("console.do_need_mech"))
        registry = self._intervention_table.registry
        mechanism = registry.mechanisms.get(mech_id)
        if mechanism is None:
            raise ValueError(t("console.do_unknown_mechanism", id=mech_id))
        frame, duration, instance = self._parse_target(
            rest, node, default_duration=default_duration("node", "mechanism"),
        )
        self._intervention_table.commit(InterventionRecord(
            target_space="node", target=node, instance=instance,
            rep="mechanism", mechanism=mechanism,
            frame_t0=frame, duration=duration,
        ))
        return CommandResult(
            success=True,
            output=t(
                "console.do_registered_mechanism",
                target=node, id=mech_id,
                window=self._window_text(frame, duration),
            ),
        )

    def _cmd_do_param(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        param, rest = self._pop(args)
        if param is None:
            raise ValueError(t("console.do_need_param"))
        registry = self._intervention_table.registry
        if param not in registry.parameters:
            raise ValueError(t("console.do_unknown_parameter", target=param))
        param_spec = registry.parameters[param]
        value_text, rest = self._pop(rest)
        if value_text is None:
            raise ValueError(t("console.do_need_value"))
        value = self._coerce(value_text, param_spec.value_type)
        frame, rest = self._take_frame(rest)
        self._reject_extra(rest)
        if frame is None:
            frame = self._intervention_table.default_frame()
        self._intervention_table.commit(InterventionRecord(
            target_space="parameter", target=param,
            rep="value", value=value, frame_t0=frame,
            duration=default_duration("parameter", "value"),
        ))
        return CommandResult(
            success=True,
            output=t(
                "console.do_registered_parameter",
                target=param, value=value, frame=frame,
            ),
        )

    def _cmd_do_clear(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        space, rest = self._pop(args)
        target, rest = self._pop(rest)
        if space not in _SPACE_MAP or target is None:
            raise ValueError(t("console.do_clear_usage"))
        instance: tuple = ()
        if space == "node":
            registry = self._intervention_table.registry
            if target not in registry.nodes:
                raise ValueError(t("console.do_unknown_node", target=target))
            if registry.nodes[target].instance_domain.kind != "global_singleton":
                instance, rest = self._take_instance(rest)
        elif space == "feature":
            instance, rest = self._take_instance(rest)
        rep = None
        if rest and rest[0] == "rep":
            if space != "node":
                raise ValueError(t("console.do_rep_only_node"))
            if len(rest) < 2:
                raise ValueError(t("console.do_clear_usage"))
            rep = rest[1]
            rest = rest[2:]
            if rep not in ("value", "mechanism"):
                raise ValueError(t("console.do_bad_rep", rep=rep))
        self._reject_extra(rest)
        cleared = self._intervention_table.clear(
            _SPACE_MAP[space], target, instance, rep=rep,
        )
        if not cleared:
            return CommandResult(
                success=False,
                output=t("console.do_not_found", space=space, target=target),
            )
        return CommandResult(
            success=True,
            output=t(
                "console.do_cleared", space=space, target=target,
                reps=",".join(cleared),
            ),
        )

    def _cmd_do_list(self) -> str:
        t = self._i18n.t
        snapshot = self._intervention_table.snapshot()
        lines = [t("console.do_list_header")]
        any_active = False
        for group, label_key in _LIST_GROUPS:
            records = snapshot.get(group, [])
            if not records:
                continue
            any_active = True
            for record in records:
                lines.append(
                    f"  [{t(label_key)}] {record['target']} "
                    f"{self._window_text(record['frame_t0'], record['duration'])} "
                    f"seq={record['seq']}"
                )
        if not any_active:
            lines.append(t("console.do_list_empty"))
        return "\n".join(lines)

    # ── 解析辅助（全部 fail-closed）────────────────────────

    @staticmethod
    def _pop(args: list[str]) -> tuple[str | None, list[str]]:
        if not args:
            return None, args
        return args[0], args[1:]

    @staticmethod
    def _window_text(frame_t0: int, duration: int | None) -> str:
        """生效窗口文本：[t0, t0+dur) 或 [t0, ∞)。"""
        if duration is None:
            return f"@[{frame_t0}, \u221e)"
        return f"@[{frame_t0}, {frame_t0 + duration})"

    @staticmethod
    def _coerce(
        text: str, kind: str = "float", choices: tuple = (),
    ) -> object:
        """按目标值域类型解析值。

        integer/float 域转数值；enum 域按 ``choices`` 的元素类型解析
        （整数枚举转 int，文本枚举保持文本）；其余（string 等）保持文本。
        """
        if kind == "integer":
            try:
                return int(text)
            except ValueError:
                raise ValueError(f"值 {text!r} 不属于声明值域 integer") from None
        if kind == "float":
            try:
                return float(text)
            except ValueError:
                raise ValueError(f"值 {text!r} 不属于声明值域 float") from None
        if kind == "enum" and choices and all(
            isinstance(choice, int) and not isinstance(choice, bool)
            for choice in choices
        ):
            try:
                return int(text)
            except ValueError:
                raise ValueError(
                    f"值 {text!r} 不属于声明值域 enum {tuple(choices)}"
                ) from None
        return text

    @staticmethod
    def _take_frame(args: list[str]) -> tuple[int | None, list[str]]:
        """解析 `at <tick>`；缺省返回 None（调用方按下一 tick 处理）。"""
        rest = list(args)
        if rest and rest[0] == "at":
            if len(rest) < 2:
                raise ValueError("at 缺少 tick 参数")
            try:
                return int(rest[1]), rest[2:]
            except ValueError:
                raise ValueError(f"tick 必须为整数: {rest[1]!r}") from None
        return None, rest

    @staticmethod
    def _take_duration(args: list[str]) -> tuple[int | None, list[str]]:
        """解析 `dur <n>`；缺省返回 None（调用方按替换规格缺省处理）。"""
        rest = list(args)
        if rest and rest[0] == "dur":
            if len(rest) < 2:
                raise ValueError("dur 缺少时长参数")
            try:
                return int(rest[1]), rest[2:]
            except ValueError:
                raise ValueError(f"时长必须为整数: {rest[1]!r}") from None
        return None, rest

    def _take_instance(
        self, args: list[str],
    ) -> tuple[tuple, list[str]]:
        """解析 `[cx cy]`；缺省用 default_chunk（chunk 实例域节点）。"""
        rest = list(args)
        if len(rest) >= 2 and all(
            part.lstrip("-").isdigit() for part in rest[:2]
        ):
            return (int(rest[0]), int(rest[1])), rest[2:]
        return self._default_chunk, rest

    @staticmethod
    def _reject_extra(rest: list[str]) -> None:
        if rest:
            raise ValueError(f"do 不接受多余参数: {' '.join(rest)}")

    def _parse_target(
        self, args: list[str], node: str,
        *,
        default_duration: int | None,
    ) -> tuple[int, int | None, tuple]:
        """解析 (生效帧, 时长, 实例) 三要素，多余参数一律报错。"""
        registry = self._intervention_table.registry
        node_spec = registry.nodes[node]
        is_global = node_spec.instance_domain.kind == "global_singleton"
        rest = list(args)
        instance: tuple = ()
        if not is_global:
            instance, rest = self._take_instance(rest)
        frame, rest = self._take_frame(rest)
        duration, rest = self._take_duration(rest)
        self._reject_extra(rest)
        if frame is None:
            frame = self._intervention_table.default_frame()
        if duration is None:
            duration = default_duration
        return frame, duration, instance
