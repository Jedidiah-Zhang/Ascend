"""trace 指令组 — 研究日志开关、查询与重算校验（研究者通道）。

指令（与 net 研究 API 同源：同一研究日志实例、同一重算实现）:

    trace on [capacity]          开启研究日志（缺省容量 4096）
    trace off                    关闭研究日志（已记录内容保留）
    trace status                 当前开关与记录数
    trace list [frame N] [node ID]   按帧/节点筛选记录
    trace show <node> [frame N]  打印单条记录的完整字段
    trace verify                 全部记录重算校验（返回不一致清单）
    trace clear                  清空记录

研究日志与玩法事件分库：本指令组只读 ``WeatherEngine.trace``，
不订阅世界树、不发布事件。全部用户可见文案走 i18n。
"""

from __future__ import annotations

from .result import CommandResult


class TraceCommandsMixin:
    """研究日志指令组（接入 CommandExecutor）。"""

    def _h_trace(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        if self._weather is None:
            return CommandResult(success=False, output=t("console.trace_unavailable"))
        handlers = {
            "on": self._cmd_trace_on,
            "off": self._cmd_trace_off,
            "status": lambda _rest: self._cmd_trace_status(),
            "list": self._cmd_trace_list,
            "show": self._cmd_trace_show,
            "verify": lambda _rest: self._cmd_trace_verify(),
            "clear": lambda _rest: self._cmd_trace_clear(),
        }
        if not args:
            return CommandResult(success=False, output=self._cmd_trace_help())
        handler = handlers.get(args[0].lower())
        if handler is None:
            return CommandResult(success=False, output=self._cmd_trace_help())
        try:
            return handler(args[1:])
        except ValueError as exc:
            return CommandResult(success=False, output=str(exc))

    def _cmd_trace_help(self) -> str:
        t = self._i18n.t
        return "\n".join((
            f"  trace on [capacity]      {t('console.help_trace_on')}",
            f"  trace off                {t('console.help_trace_off')}",
            f"  trace status             {t('console.help_trace_status')}",
            f"  trace list [frame N] [node ID]   {t('console.help_trace_list')}",
            f"  trace show <node> [frame N]      {t('console.help_trace_show')}",
            f"  trace verify             {t('console.help_trace_verify')}",
            f"  trace clear              {t('console.help_trace_clear')}",
        ))

    # ── 子指令 ────────────────────────────────────────────

    def _cmd_trace_on(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        if self._weather.trace is not None:
            return CommandResult(
                success=False, output=t("console.trace_already_on"),
            )
        capacity = 4096
        if args:
            try:
                capacity = int(args[0])
            except ValueError:
                raise ValueError(t("console.trace_bad_capacity", value=args[0]))
            if len(args) > 1:
                raise ValueError(t("console.trace_extra_args"))
        log = self._weather.enable_trace(capacity=capacity)
        return CommandResult(
            success=True,
            output=t("console.trace_enabled", capacity=capacity, count=len(log)),
        )

    def _cmd_trace_off(self, _args: list[str]) -> CommandResult:
        t = self._i18n.t
        log = self._weather.trace
        if log is None:
            return CommandResult(
                success=False, output=t("console.trace_not_on"),
            )
        count = len(log)
        self._weather.disable_trace()
        return CommandResult(
            success=True, output=t("console.trace_disabled", count=count),
        )

    def _cmd_trace_status(self) -> CommandResult:
        t = self._i18n.t
        log = self._weather.trace
        if log is None:
            return CommandResult(
                success=True, output=t("console.trace_status_off"),
            )
        return CommandResult(
            success=True,
            output=t("console.trace_status_on", count=len(log)),
        )

    def _cmd_trace_list(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        log = self._require_trace()
        frame, node_id = self._parse_filters(args)
        records = log.records(frame=frame, node_id=node_id)
        if not records:
            return CommandResult(
                success=True, output=t("console.trace_list_empty"),
            )
        lines = [t("console.trace_list_header", count=len(records))]
        for entry in records:
            lines.append(
                f"  [{entry.frame}] {entry.node_id} "
                f"{entry.mechanism_id or t('console.trace_value_rep')} "
                f"= {entry.output!r} "
                f"{t('console.trace_parents', count=len(entry.parents))}"
            )
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_trace_show(self, args: list[str]) -> CommandResult:
        t = self._i18n.t
        log = self._require_trace()
        if not args:
            raise ValueError(t("console.trace_need_node"))
        node_id = args[0]
        frame, extra_node = self._parse_filters(args[1:])
        if extra_node is not None:
            raise ValueError(t("console.trace_extra_args"))
        records = log.records(frame=frame, node_id=node_id)
        if not records:
            return CommandResult(
                success=False,
                output=t("console.trace_not_found", node=node_id),
            )
        entry = records[-1]
        view = entry.plain()
        lines = [
            f"  {t('console.trace_field_node')}: {view['node_id']}",
            f"  {t('console.trace_field_frame')}: {view['frame']} "
            f"{t('console.trace_field_instance')}: {view['instance']}",
            f"  {t('console.trace_field_microstep')}: {view['microstep']}",
            f"  {t('console.trace_field_mechanism')}: "
            f"{view['mechanism_id'] or t('console.trace_value_rep')}",
            f"  {t('console.trace_field_equation')}: {view['equation_version']}",
            f"  {t('console.trace_field_parents')}: {view['parents']}",
            f"  {t('console.trace_field_parameters')}: {view['parameters']}",
            f"  {t('console.trace_field_random')}: "
            f"{view['random_addresses']}",
            f"  {t('console.trace_field_intervention')}: {view['rep']} "
            f"{view['intervention'] or ''}",
            f"  {t('console.trace_field_output')}: {view['output']!r}",
            f"  {t('console.trace_field_boundary')}: {view['boundary']}",
        ]
        return CommandResult(success=True, output="\n".join(lines))

    def _cmd_trace_verify(self) -> CommandResult:
        t = self._i18n.t
        log = self._require_trace()
        mismatched = log.verify_all()
        if not mismatched:
            return CommandResult(
                success=True,
                output=t("console.trace_verify_ok", count=len(log)),
            )
        lines = [t("console.trace_verify_fail", count=len(mismatched))]
        for entry in mismatched[:10]:
            lines.append(
                f"  [{entry.frame}] {entry.node_id} "
                f"{t('console.trace_recorded')}={entry.output!r} "
                f"{t('console.trace_replayed')}={log.replay(entry)!r}"
            )
        return CommandResult(success=False, output="\n".join(lines))

    def _cmd_trace_clear(self) -> CommandResult:
        t = self._i18n.t
        log = self._require_trace()
        count = log.clear()
        return CommandResult(
            success=True, output=t("console.trace_cleared", count=count),
        )

    # ── 解析辅助 ──────────────────────────────────────────

    def _require_trace(self):
        if self._weather.trace is None:
            raise ValueError(self._i18n.t("console.trace_not_on"))
        return self._weather.trace

    def _parse_filters(self, args: list[str]) -> tuple[int | None, str | None]:
        """解析 ``[frame N] [node ID]``（顺序不限，多余参数报错）。"""
        t = self._i18n.t
        frame: int | None = None
        node_id: str | None = None
        rest = list(args)
        while rest:
            token = rest.pop(0)
            if token == "frame":
                if not rest:
                    raise ValueError(t("console.trace_need_frame"))
                try:
                    frame = int(rest.pop(0))
                except ValueError:
                    raise ValueError(
                        t("console.trace_bad_frame", value=token)
                    ) from None
            elif node_id is None:
                node_id = token
            else:
                raise ValueError(t("console.trace_extra_args"))
        return frame, node_id
