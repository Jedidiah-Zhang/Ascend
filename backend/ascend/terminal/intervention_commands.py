"""干预执行器终端指令组 — do 指令（研究者原子化执行干预）。

指令（与 net 研究 API 同源：同一时间线、同一校验、同一缺省解析）:

    do list                                          列出当前有效干预
    do value <node> <value> [cx cy] [at T] [dur N]   值干预（缺省单帧）
    do param <parameter_id> <value> [at T]           参数干预（环境变化）
    do clear <node|param|feature> <target> [cx cy]   撤销（后续帧不再生效）

帧基准为时间模块 tick；缺省下一 tick 生效（``InterventionTimeline.default_frame``），
chunk 分量缺省坐标用 default_chunk。任何未识别的多余参数都会报错
（fail-closed），不做静默忽略。全部用户可见文案走 i18n。
"""

from __future__ import annotations

from ascend.log import get_logger
from ascend.world.research.timeline import (
    PlannedIntervention,
    default_duration,
)

from .result import CommandResult

logger = get_logger(__name__)

_SPACE_MAP = {
    "node": "node",
    "param": "parameter",
    "feature": "field_feature",
}
_LIST_LABELS = {
    "node": "console.do_label_value",
    "parameter": "console.do_label_parameter",
    "field_feature": "console.do_label_feature",
}


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
            f"  do param <parameter_id> <value> [at T]"
            f"           {t('console.help_do_param')}",
            f"  do clear <node|param|feature> <target> [cx cy]"
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
        program = self._intervention_table.program
        if node not in program.slots:
            raise ValueError(t("console.do_unknown_node", target=node))
        slot = program.slots[node]
        value_text, rest = self._pop(rest)
        if value_text is None:
            raise ValueError(t("console.do_need_value"))
        value = self._coerce(
            value_text, slot.domain.kind, slot.domain.choices,
        )
        frame, stop, instance = self._parse_target(
            rest, node, default_duration=default_duration("node"),
        )
        self._intervention_table.plan(PlannedIntervention(
            target_space="node", target=node, instance=instance,
            value=value, start_frame=frame, stop_frame=stop,
            source="terminal",
        ))
        return CommandResult(
            success=True,
            output=t(
                "console.do_registered_value",
                target=node, value=value,
                window=self._window_text(frame, stop),
            ),
        )

    def _cmd_do_param(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        param, rest = self._pop(args)
        if param is None:
            raise ValueError(t("console.do_need_param"))
        declaration = self._intervention_table.parameter_decl(param)
        if declaration is None:
            raise ValueError(t("console.do_unknown_parameter", target=param))
        value_text, rest = self._pop(rest)
        if value_text is None:
            raise ValueError(t("console.do_need_value"))
        value = self._coerce(
            value_text, declaration.kind, declaration.choices,
        )
        frame, rest = self._take_frame(rest)
        self._reject_extra(rest)
        if frame is None:
            frame = self._intervention_table.default_frame()
        self._intervention_table.plan(PlannedIntervention(
            target_space="parameter", target=param,
            value=value, start_frame=frame, stop_frame=None,
            source="terminal",
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
            program = self._intervention_table.program
            if target not in program.slots:
                raise ValueError(t("console.do_unknown_node", target=target))
            slot = program.slots[target]
            if program.instances[slot.on].kind != "global":
                instance, rest = self._take_instance(rest)
        elif space == "feature":
            instance, rest = self._take_instance(rest)
        self._reject_extra(rest)
        stopped = self._intervention_table.revoke(
            _SPACE_MAP[space], target, instance,
        )
        if not stopped:
            return CommandResult(
                success=False,
                output=t("console.do_not_found", space=space, target=target),
            )
        return CommandResult(
            success=True,
            output=t(
                "console.do_revoked", space=space, target=target,
                count=stopped,
            ),
        )

    def _cmd_do_list(self) -> str:
        t = self._i18n.t
        frame = self._intervention_table.current_frame()
        active = self._intervention_table.snapshot(frame)
        lines = [t("console.do_list_header")]
        for entry in active:
            lines.append(
                f"  [{t(_LIST_LABELS[entry['target_space']])}] "
                f"{entry['target']} "
                f"{self._window_text(entry['start_frame'], entry['stop_frame'])} "
                f"seq={entry['seq']}"
            )
        if not active:
            lines.append(t("console.do_list_empty"))
        return "\n".join(lines)

    # ── 解析辅助（全部 fail-closed）────────────────────────

    @staticmethod
    def _pop(args: list[str]) -> tuple[str | None, list[str]]:
        if not args:
            return None, args
        return args[0], args[1:]

    @staticmethod
    def _window_text(start_frame: int, stop_frame: int | None) -> str:
        """计划窗口文本：[start, stop) 或 [start, ∞)。"""
        if stop_frame is None:
            return f"@[{start_frame}, \u221e)"
        return f"@[{start_frame}, {stop_frame})"

    @staticmethod
    def _coerce(
        text: str, kind: str = "float", choices: tuple = (),
    ) -> object:
        """按目标值域类型解析值。

        int/float 域转数值；enum 域按 ``choices`` 的元素类型解析
        （整数枚举转 int，文本枚举保持文本）；bool 域按字面量解析；
        其余（any 等）保持文本。
        """
        if kind == "int":
            try:
                return int(text)
            except ValueError:
                raise ValueError(f"值 {text!r} 不属于声明值域 int") from None
        if kind == "float":
            try:
                return float(text)
            except ValueError:
                raise ValueError(f"值 {text!r} 不属于声明值域 float") from None
        if kind == "bool":
            if text.lower() in ("1", "true", "yes", "on"):
                return True
            if text.lower() in ("0", "false", "no", "off"):
                return False
            raise ValueError(f"值 {text!r} 不属于声明值域 bool")
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
        """解析 `dur <n>`；缺省返回 None（调用方按缺省时长处理）。"""
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
        """解析 (生效帧, 失效帧, 实例) 三要素，多余参数一律报错。"""
        program = self._intervention_table.program
        slot = program.slots[node]
        is_global = program.instances[slot.on].kind == "global"
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
        stop = None if duration is None else frame + duration
        return frame, stop, instance
