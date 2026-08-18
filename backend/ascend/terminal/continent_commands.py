"""continent 指令组 — 大陆缓存诊断与强制重建。

Mixin，依赖宿主 CommandExecutor 提供的:
  self._continent_path / self._gen_fingerprint_fn / self._i18n
"""

import os

from .result import CommandResult


class ContinentCommandsMixin:
    """continent 指令组实现。"""

    def _h_continent(self, args: list[str]) -> CommandResult:
        """处理 continent 指令组：status 诊断 / regen 强制重建。

        Args:
            args: 参数列表。

        Returns:
            执行结果。
        """
        if self._continent_path is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.continent_unavailable"),
            )
        if not args or args[0].lower() == "status":
            return self._h_continent_status()
        if args[0].lower() == "regen":
            return self._h_continent_regen()
        return CommandResult(
            success=False, output=self._i18n.t("console.continent_usage"),
        )

    def _h_continent_status(self) -> CommandResult:
        """continent status：缓存存在性 + 生成环境指纹漂移诊断。

        Returns:
            执行结果。
        """
        from ascend.space.continent import read_continent_header

        path = self._continent_path
        if not os.path.isfile(path):
            return CommandResult(
                success=True,
                output=self._i18n.t("console.continent_no_cache"),
            )
        try:
            with open(path, "rb") as f:
                header = read_continent_header(f.read())
        except OSError as exc:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.continent_read_failed", error=str(exc),
                ),
            )
        if header is None:
            return CommandResult(
                success=False,
                output=self._i18n.t("console.continent_header_invalid"),
            )
        version, stored_fp = header
        current_fp = (
            self._gen_fingerprint_fn() if self._gen_fingerprint_fn else ""
        )
        if stored_fp and current_fp and stored_fp != current_fp:
            drift = self._i18n.t("console.continent_drift")
        else:
            drift = self._i18n.t("console.continent_match")
        lines = [
            self._i18n.t("console.continent_status_header"),
            self._i18n.t("console.continent_status_version", version=version),
            self._i18n.t(
                "console.continent_status_fp",
                fp=(stored_fp[:12] + "…") if stored_fp else "-",
            ),
            drift,
        ]
        return CommandResult(success=True, output="\n".join(lines))

    def _h_continent_regen(self) -> CommandResult:
        """continent regen：删除大陆缓存，下次进入世界时按当前算法重建。

        Returns:
            执行结果。
        """
        path = self._continent_path
        if not os.path.isfile(path):
            return CommandResult(
                success=True,
                output=self._i18n.t("console.continent_regen_missing"),
            )
        try:
            os.remove(path)
        except OSError as exc:
            return CommandResult(
                success=False,
                output=self._i18n.t(
                    "console.continent_regen_failed", error=str(exc),
                ),
            )
        return CommandResult(
            success=True,
            output=self._i18n.t("console.continent_regen_ok"),
        )
