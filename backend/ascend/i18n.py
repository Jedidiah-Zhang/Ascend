"""国际化文本加载器。

用法:
    from ascend.i18n import I18n
    i18n = I18n()
    print(i18n.t("ui.save"))  # -> "保存" (当前语言)
    print(i18n.t("item.count", n=5))  # -> 模板替换
"""

import json
import re
from pathlib import Path


def _resolve_content_dir(dirname: str, here: Path | None = None) -> Path:
    """按模块位置解析内容目录（与 data.py 同源约定，双布局回退）。

    开发：`backend/ascend/i18n.py` 上三级 → 仓库根 → `根/lang`。
    发布（Nuitka standalone）：`__file__` 含包前缀，上三级 → 舞台根
    `STAGE`；语言文件配送到 `STAGE/lang`（主）或 `STAGE/server/lang`（回退）。

    Args:
        dirname: 内容目录名（"lang"/"data"）。
        here: 模块文件路径（测试注入模拟布局用；None = 本模块 __file__）。
    """
    here = (here or Path(__file__)).resolve()
    primary = here.parent.parent.parent / dirname
    if primary.is_dir():
        return primary
    fallback = here.parent.parent / dirname
    return fallback if fallback.is_dir() else primary


# 语言文件目录（开发=仓库根/lang；发布=舞台根或 server/ 内 lang）
LANG_DIR = _resolve_content_dir("lang")

# 进程共享实例：game.py 与 biome/climate 的 label 解析共用它，
# set_lang 切换全局生效（I18n() 默认实例亦指向它，见 get_default）。
_DEFAULT: "I18n | None" = None


def get_default() -> "I18n":
    """进程级共享 I18n 实例（惰性创建；游戏与枚举 label 共用）。"""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = I18n()
    return _DEFAULT


class I18n:
    """国际化文本管理器。

    从 JSON 文件加载翻译，支持模板插值和运行时切换语言。

    Attributes:
        lang: 当前语言代码。
    """

    def __init__(self, lang: str = "zh_CN") -> None:
        """初始化并加载指定语言的翻译表。

        注：直接构造会产生独立实例（不参与全局语言切换）；需要
        跟随游戏 `lang` 指令切换的共享实例，请用 `get_default()`。

        Args:
            lang: 语言代码，对应 lang/ 下的 JSON 文件名（不含扩展名）。
        """
        self._translations: dict[str, str] = {}
        self.lang = lang
        self._load(lang)

    def _load(self, lang: str) -> None:
        """从 lang/<lang>.json 加载翻译表。

        Args:
            lang: 语言代码。
        """
        path = LANG_DIR / f"{lang}.json"
        if path.exists():
            with open(path, encoding="utf-8") as f:
                self._translations = json.load(f)
        else:
            self._translations = {}

    def t(self, key: str, **kwargs: object) -> str:
        """获取翻译文本，支持模板插值。

        若 key 不存在则返回 key 本身作为兜底。

        Args:
            key: 翻译键，用 . 作为命名空间分隔符，如 "ui.save"。
            **kwargs: 模板变量，替换文本中的 {var} 占位符。

        Returns:
            翻译后的字符串。
        """
        text = self._translations.get(key, key)
        # 无占位符短路：文本不含 { 时跳过正则替换（热路径省 re.sub 开销）
        if kwargs and "{" in text:
            def _replace(match: re.Match) -> str:
                k = match.group(1)
                return str(kwargs.get(k, match.group(0)))
            text = re.sub(r'\{(\w+)\}', _replace, text)
        return text

    def set_lang(self, lang: str) -> None:
        """切换当前语言并重新加载翻译表。

        Args:
            lang: 新的语言代码。
        """
        self.lang = lang
        self._load(lang)

    def available_langs(self) -> list[str]:
        """返回 lang/ 目录下所有可用的语言代码。

        Returns:
            语言代码列表（无 .json 后缀）。
        """
        if not LANG_DIR.exists():
            return []
        return sorted(
            f.stem for f in LANG_DIR.glob("*.json")
        )

    def __repr__(self) -> str:
        return f"I18n(lang={self.lang!r}, keys={len(self._translations)})"
