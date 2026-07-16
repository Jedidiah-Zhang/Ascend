"""I18n 单元测试。

覆盖：真实语言文件加载、缺失语言兜底、模板插值、
缺失变量保留、运行时切换语言、可用语言枚举。

用 monkeypatch 替换 LANG_DIR 指向临时目录，测试不依赖真实翻译内容。
"""

import json

import pytest

import ascend.i18n as i18n_module
from ascend.i18n import I18n


@pytest.fixture
def lang_dir(tmp_path, monkeypatch):
    """临时语言目录，写入两份测试翻译表。"""
    (tmp_path / "zh_CN.json").write_text(
        json.dumps({
            "ui.save": "保存",
            "greet": "你好 {name}，今天 {day} 号",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    (tmp_path / "en_US.json").write_text(
        json.dumps({"ui.save": "Save"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(i18n_module, "LANG_DIR", tmp_path)
    return tmp_path


class TestI18nLoad:
    """加载与兜底。"""

    def test_T1_load_existing_lang(self, lang_dir):
        """加载存在的语言文件。"""
        i18n = I18n("zh_CN")
        assert i18n.lang == "zh_CN"
        assert i18n.t("ui.save") == "保存"

    def test_T2_missing_lang_falls_back_to_empty(self, lang_dir):
        """语言文件不存在时翻译表为空，t 返回 key 本身。"""
        i18n = I18n("xx_XX")
        assert i18n.t("ui.save") == "ui.save"

    def test_T3_missing_key_returns_key(self, lang_dir):
        """key 不存在时返回 key 本身。"""
        i18n = I18n("zh_CN")
        assert i18n.t("no.such.key") == "no.such.key"

    def test_T4_real_lang_files_loadable(self):
        """真实 lang/ 目录下 zh_CN 与 en_US 可加载且非空。"""
        for lang in ("zh_CN", "en_US"):
            i18n = I18n(lang)
            assert i18n.t("ui.save") != "ui.save", f"{lang} 翻译表为空"


class TestI18nTemplate:
    """模板插值。"""

    def test_T5_interpolation(self, lang_dir):
        """{var} 占位符被 kwargs 替换。"""
        i18n = I18n("zh_CN")
        assert i18n.t("greet", name="张三", day=5) == "你好 张三，今天 5 号"

    def test_T6_missing_var_preserved(self, lang_dir):
        """未提供的变量保留原始 {var} 占位符。"""
        i18n = I18n("zh_CN")
        assert i18n.t("greet", name="张三") == "你好 张三，今天 {day} 号"

    def test_T7_no_kwargs_no_substitution(self, lang_dir):
        """无 kwargs 时不做任何替换。"""
        i18n = I18n("zh_CN")
        assert i18n.t("greet") == "你好 {name}，今天 {day} 号"


class TestI18nSwitch:
    """语言切换与枚举。"""

    def test_T8_set_lang_reloads(self, lang_dir):
        """set_lang 切换语言并重新加载翻译表。"""
        i18n = I18n("zh_CN")
        i18n.set_lang("en_US")
        assert i18n.lang == "en_US"
        assert i18n.t("ui.save") == "Save"

    def test_T9_available_langs(self, lang_dir):
        """available_langs 返回目录下所有语言代码（有序）。"""
        i18n = I18n("zh_CN")
        assert i18n.available_langs() == ["en_US", "zh_CN"]

    def test_T10_available_langs_missing_dir(self, tmp_path, monkeypatch):
        """语言目录不存在时返回空列表。"""
        monkeypatch.setattr(i18n_module, "LANG_DIR", tmp_path / "nope")
        i18n = I18n("zh_CN")
        assert i18n.available_langs() == []
