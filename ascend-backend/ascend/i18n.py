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

# 语言文件目录（项目根 /lang）
LANG_DIR = Path(__file__).parent.parent.parent / "lang"


class I18n:
    """国际化文本管理器。

    从 JSON 文件加载翻译，支持模板插值和运行时切换语言。

    Attributes:
        lang: 当前语言代码。
    """

    def __init__(self, lang: str = "zh_CN") -> None:
        """初始化并加载指定语言的翻译表。

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
        if kwargs:
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
