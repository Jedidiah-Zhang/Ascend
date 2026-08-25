"""配置内容数据测试 — data/world.json 覆盖 + 类型校验 + 派生/基础常量隔离。

Coverage: ascend.config._apply_content/_coerce_content + load_content("world")。
"""

import pytest

from ascend.data import load_content
import ascend.config as c
from ascend.config import _apply_content, _coerce_content


def _norm(v):
    """JSON 值归一为 Python 内存形态（递归 list→tuple）。"""
    if isinstance(v, list):
        return tuple(_norm(x) for x in v)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    return v


class TestWorldDataFile:
    def test_world_data_exists(self):
        doc = load_content("world")
        assert doc["version"] == 1
        # tile 段（曾含 STEEP_GRADIENT，issue #42 移除后无 tile 内容）不再存在
        assert set(doc) == {"version", "world", "climate", "weather"}

    def test_content_values_applied(self):
        """内容常量有效值 = data/world.json（覆盖生效，list↔tuple 归一比较）。"""
        doc = load_content("world")
        flat = {
            k: v for sec, values in doc.items()
            if sec != "version"
            for k, v in values.items()
        }
        for name, json_val in flat.items():
            assert getattr(c, name) == _norm(json_val), f"{name} 未被覆盖"

    def test_derived_constants_not_overridden(self):
        """派生值保持代码计算（不在数据文件中）。"""
        assert c.MOISTURE_TILE_FREQUENCY == c.NOISE_FREQ_DERIVED / c.TILE_MAP_SIZE
        assert c.SEASON_LENGTH == c.SEASON_LENGTH_DAYS * c.GAME_DAY
        assert c.TICK_DT == 1.0 / c.TICK_RATE

    def test_infra_constants_untouched(self):
        """基础设施常量不受数据文件影响。"""
        assert c.SERVER_PORT == 9081
        assert c.TICK_RATE == 24
        assert c.TILE_MAP_SIZE == 200

    def test_type_preserved(self):
        """类型经转换保持：int/float/tuple/dict。"""
        assert isinstance(c.DIURNAL_PEAK_HOUR, int)
        assert isinstance(c.CONTINENT_WIDTH_KM, float)
        assert isinstance(c.TEMP_TIER_BOUNDARIES, tuple)
        assert isinstance(c.PARAM_BOUNDS, dict)

    def test_fingerprint_names_resolve(self):
        """生成指纹名单内所有名字仍可 getattr 解析。"""
        for name in c.CONTINENT_GEN_CONSTANT_NAMES:
            assert hasattr(c, name), f"指纹常量缺失: {name}"


class TestContentValidation:
    def test_unknown_key_rejected(self):
        with pytest.raises(ValueError, match="未知配置键"):
            _apply_content({"world": {"NOT_A_REAL_KEY": 1.0}})

    def test_type_mismatch_rejected(self):
        with pytest.raises(ValueError, match="需要 int"):
            _coerce_content("X", 1.5, 3)  # float → int
        with pytest.raises(ValueError, match="需要 float"):
            _coerce_content("X", "abc", 1.0)

    def test_tuple_from_list(self):
        assert _coerce_content("X", [1.0, 2.0], (1.0, 2.0)) == (1.0, 2.0)

    def test_int_to_float_coerced(self):
        assert _coerce_content("X", 10, 10.0) == 10.0

    def test_non_section_value_rejected(self):
        with pytest.raises(ValueError, match="必须是对象"):
            _apply_content({"world": [1, 2]})
