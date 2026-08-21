"""地形数据外置测试 — data/terrain.json 加载 + 契约校验 + 解析。

Coverage: ascend.data.load_content + terrain._build_terrain_defs/_parse_*。
"""

import json

import pytest

from ascend.data import DATA_DIR, load_content
from ascend.space.terrain import (
    TERRAIN_DEFS,
    TerrainType,
    get_terrain_def,
    terrain_by_id,
    _build_terrain_defs,
)
from ascend.space.state_defs import STATE_TYPES


def _mini_doc(terrain: dict) -> dict:
    """构造最小合法文档（单地形 + 完整状态矩阵 + fallback）。"""
    return {
        "version": 1,
        "terrain": {
            "ascend:grassland": {
                "value": 0,
                "label_key": "terrain.grassland",
                "states": {k: None for k in STATE_TYPES},
                "fallback": True,
            },
            **terrain,
        },
    }


class TestDataFile:
    """data/terrain.json 与加载器。"""

    def test_data_file_exists(self):
        """默认内容文件存在且顶层为对象。"""
        assert (DATA_DIR / "terrain.json").exists()
        doc = load_content("terrain")
        assert isinstance(doc, dict)
        assert doc["version"] == 1

    def test_all_nine_terrains_loaded(self):
        """首发 9 种地形齐全（命名空间 id 键）。"""
        assert set(TERRAIN_DEFS) == {
            "ascend:grassland", "ascend:sand", "ascend:fertile_soil",
            "ascend:rock", "ascend:steep_slope", "ascend:mountain_peak",
            "ascend:shallow_water", "ascend:deep_water", "ascend:marsh",
        }

    def test_enum_matches_registry_order(self):
        """枚举值 = 注册表 value 升序（持久化契约）。"""
        for terrain in TerrainType:
            assert terrain.value == get_terrain_def(terrain).value

    def test_enum_member_local_name(self):
        """枚举成员名 = 命名空间 id 的 local 大写（代码书写名）。"""
        assert TerrainType.GRASSLAND == terrain_by_id("ascend:grassland")
        assert TerrainType.MARSH == terrain_by_id("ascend:marsh")

    def test_persistence_values_contiguous(self):
        """契约：value 0..n-1 连续、唯一（新地形只能追加）。"""
        values = sorted(d.value for d in TERRAIN_DEFS.values())
        assert values == list(range(len(TERRAIN_DEFS)))

    def test_single_fallback(self):
        """兜底地形全表唯一（ascend:sand）。"""
        fallbacks = [ns for ns, d in TERRAIN_DEFS.items() if d.fallback]
        assert fallbacks == ["ascend:sand"]

    def test_inf_cost_parsed(self):
        """JSON 用 "inf" 表示不可通行，解析为无穷。"""
        assert TERRAIN_DEFS["ascend:mountain_peak"].movement_cost == float("inf")
        assert TERRAIN_DEFS["ascend:deep_water"].movement_cost == float("inf")
        assert TERRAIN_DEFS["ascend:grassland"].movement_cost == 1.0

    def test_missing_content_file(self):
        """缺失内容文件抛 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            load_content("does_not_exist")

    def test_bad_json_rejected(self, tmp_path, monkeypatch):
        """非法 JSON 抛 ValueError。"""
        p = tmp_path / "bad.json"
        p.write_text("{not json", encoding="utf-8")
        monkeypatch.setattr("ascend.data.DATA_DIR", tmp_path)
        with pytest.raises(ValueError):
            load_content("bad")

    def test_non_object_top_level_rejected(self, tmp_path, monkeypatch):
        """顶层非对象抛 ValueError。"""
        p = tmp_path / "list.json"
        p.write_text("[1, 2]", encoding="utf-8")
        monkeypatch.setattr("ascend.data.DATA_DIR", tmp_path)
        with pytest.raises(ValueError):
            load_content("list")


class TestContractValidation:
    """注册表构建的契约校验。"""

    def test_duplicate_value_rejected(self):
        doc = _mini_doc({
            "ascend:sand": {"value": 0, "label_key": "terrain.sand",
                            "states": {k: None for k in STATE_TYPES}},
        })
        with pytest.raises(ValueError, match="ascend:grassland") as exc:
            _build_terrain_defs(doc)
        # 报错点名冲突的两个地形
        assert "ascend:sand" in str(exc.value)

    def test_non_contiguous_value_rejected(self):
        doc = _mini_doc({
            "ascend:sand": {"value": 2, "label_key": "terrain.sand",
                            "states": {k: None for k in STATE_TYPES}},
        })
        with pytest.raises(ValueError, match="不连续"):
            _build_terrain_defs(doc)

    def test_duplicate_local_name_rejected(self):
        """两个命名空间共用同一 local 名 → 枚举成员冲突，拒绝。"""
        doc = _mini_doc({
            "mymod:grassland": {"value": 1, "label_key": "x",
                                "states": {k: None for k in STATE_TYPES}},
        })
        with pytest.raises(ValueError, match="local 名重复"):
            _build_terrain_defs(doc)

    def test_bad_namespace_key_rejected(self):
        """非 <ns>:<local> 格式的注册表键被拒。"""
        doc = _mini_doc({
            "GRASSLAND": {"value": 1, "label_key": "x",
                          "states": {k: None for k in STATE_TYPES}},
        })
        with pytest.raises(ValueError, match="注册表键非法"):
            _build_terrain_defs(doc)

    def test_missing_state_rejected(self):
        doc = _mini_doc({})
        doc["terrain"]["ascend:grassland"]["states"] = {"moisture": None}
        with pytest.raises(ValueError, match="漏声明"):
            _build_terrain_defs(doc)

    def test_unknown_state_rejected(self):
        doc = _mini_doc({})
        doc["terrain"]["ascend:grassland"]["states"] = {
            **{k: None for k in STATE_TYPES}, "lava": None,
        }
        with pytest.raises(ValueError, match="未知状态"):
            _build_terrain_defs(doc)

    def test_no_fallback_rejected(self):
        doc = _mini_doc({})
        doc["terrain"]["ascend:grassland"]["fallback"] = False
        with pytest.raises(ValueError, match="fallback"):
            _build_terrain_defs(doc)

    def test_multiple_fallback_rejected(self):
        doc = _mini_doc({
            "ascend:sand": {"value": 1, "label_key": "terrain.sand",
                            "fallback": True,
                            "states": {k: None for k in STATE_TYPES}},
        })
        with pytest.raises(ValueError, match="唯一"):
            _build_terrain_defs(doc)

    def test_missing_required_field_rejected(self):
        doc = _mini_doc({})
        del doc["terrain"]["ascend:grassland"]["value"]
        with pytest.raises(ValueError, match="必需字段"):
            _build_terrain_defs(doc)

    def test_missing_registry_rejected(self):
        with pytest.raises(ValueError, match="terrain 注册表"):
            _build_terrain_defs({"version": 1})

    def test_unknown_state_in_parse(self):
        """state_params 对未知键 fail fast（沿用矩阵兜底）。"""
        from ascend.space.terrain import state_params
        with pytest.raises(KeyError):
            state_params(TerrainType.GRASSLAND, "lava")
