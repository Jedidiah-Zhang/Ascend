"""审查修复测试 — 发布布局路径解析 / 语言切换 / ns 校验 / 原子性。

Coverage: data._resolve_content_dir、i18n._resolve_content_dir/get_default、
data.split_ns_id、biome/climate 构建原子性。
"""

from pathlib import Path

import pytest

from ascend.data import _resolve_content_dir, split_ns_id
from ascend.i18n import get_default, _resolve_content_dir as _resolve_lang_dir
from ascend.space.climate import ClimateZone, _build_climate_templates
from ascend.space.biome import BiomeType, _build_biome_templates


class TestContentDirResolution:
    """发布/开发双布局路径解析（模拟 __file__ 布局）。"""

    def test_dev_layout(self, tmp_path):
        """开发：backend/ascend/data.py → 仓库根/data。"""
        here = tmp_path / "backend" / "ascend" / "data.py"
        (tmp_path / "data").mkdir()
        assert _resolve_content_dir("data", here) == tmp_path / "data"

    def test_release_stage_root(self, tmp_path):
        """发布：数据配送到舞台根 STAGE/data（上三级）。"""
        here = tmp_path / "STAGE" / "server" / "ascend" / "data.py"
        (tmp_path / "STAGE" / "data").mkdir(parents=True)
        assert _resolve_content_dir("data", here) == tmp_path / "STAGE" / "data"

    def test_release_server_fallback(self, tmp_path):
        """发布：数据配送到 server/data（上三级缺失 → 回退 server/ 内）。"""
        here = tmp_path / "STAGE" / "server" / "ascend" / "data.py"
        (tmp_path / "STAGE" / "server" / "data").mkdir(parents=True)
        assert _resolve_content_dir("data", here) \
            == tmp_path / "STAGE" / "server" / "data"

    def test_i18n_same_convention(self, tmp_path):
        """i18n 与 data 同源约定：发布配送舞台根 lang。"""
        here = tmp_path / "STAGE" / "server" / "ascend" / "i18n.py"
        (tmp_path / "STAGE").mkdir()
        (tmp_path / "STAGE" / "lang").mkdir()
        assert _resolve_lang_dir("lang", here) == tmp_path / "STAGE" / "lang"


class TestNsValidation:
    def test_split_valid(self):
        assert split_ns_id("ascend:grassland") == ("ascend", "grassland")

    def test_split_bare_rejected(self):
        with pytest.raises(ValueError, match="注册表键非法"):
            split_ns_id("GRASSLAND")

    def test_split_bad_chars_rejected(self):
        with pytest.raises(ValueError, match="注册表键非法"):
            split_ns_id("ascend:Grass-Land")


class TestLanguageSwitchShared:
    def test_labels_follow_shared_set_lang(self):
        """枚举 label 经进程共享实例，set_lang 全局生效。"""
        i = get_default()
        orig = i.lang
        try:
            i.set_lang("en_US")
            assert BiomeType.TUNDRA.label == "Tundra"
            assert ClimateZone.ALPINE.label == "Alpine"
            i.set_lang("zh_CN")
            assert BiomeType.TUNDRA.label == "苔原"
        finally:
            i.set_lang(orig)


class TestBuildAtomicity:
    """解析中途失败不留下半修改的枚举 label_key（先全量校验再统一填充）。"""

    def _climate_two_doc(self) -> dict:
        return {
            "version": 1,
            "climate": {
                "ascend:temperate_forest": {
                    "value": 4, "label_key": "climate.temperate_forest",
                    "humidity_range": [45.0, 80.0], "wind_speed_range": [0.0, 12.0],
                    "seasonality": "four_season", "display_color": "#4a7c3f",
                    "mean_precip_intensity": 5.0, "humidity_sharpness": 0.0,
                },
                "ascend:desert": {
                    "value": 2, "label_key": "climate.desert",
                    "humidity_range": [5.0, 30.0], "wind_speed_range": [2.0, 15.0],
                    "seasonality": "none", "display_color": "#e6c878",
                    "mean_precip_intensity": 2.0, "humidity_sharpness": 0.0,
                },
            },
        }

    def test_climate_partial_failure_leaves_no_label(self):
        """第二档非法 → 整体失败，第一档 label_key 未写入（原子性）。"""
        orig = ClimateZone.TEMPERATE_FOREST.label_key
        ClimateZone.TEMPERATE_FOREST.label_key = ""  # 归零
        try:
            doc = self._climate_two_doc()
            doc["climate"]["ascend:desert"]["seasonality"] = "bogus"
            with pytest.raises(ValueError, match="seasonality"):
                _build_climate_templates(doc)
            assert ClimateZone.TEMPERATE_FOREST.label_key == "", \
                "失败不应残留 label_key"
        finally:
            ClimateZone.TEMPERATE_FOREST.label_key = orig  # 恢复，避免污染其它测试

    def test_biome_partial_failure_leaves_no_label(self):
        """第二群系非法 → 整体失败，第一群系 label_key 未写入。"""
        orig = BiomeType.TEMPERATE_DECIDUOUS_FOREST.label_key
        BiomeType.TEMPERATE_DECIDUOUS_FOREST.label_key = ""
        try:
            doc = {
                "version": 1,
                "biome": {
                    "ascend:temperate_deciduous_forest": {
                        "value": 9, "label_key": "biome.temperate_deciduous_forest",
                        "climate": "ascend:temperate_forest", "ocean": False,
                    },
                    "ascend:temperate_mixed_forest": {
                        "value": 8, "label_key": "x",
                        "climate": "ascend:does_not_exist", "ocean": False,
                    },
                },
                "subdiv": {},
            }
            with pytest.raises(KeyError):
                _build_biome_templates(doc)
            assert BiomeType.TEMPERATE_DECIDUOUS_FOREST.label_key == ""
        finally:
            BiomeType.TEMPERATE_DECIDUOUS_FOREST.label_key = orig
