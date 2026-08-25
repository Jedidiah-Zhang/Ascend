"""地形状态定义测试 — 状态注册表 + 地形参数完整性 + 遮蔽规格。

Coverage: state_defs.py 全部公开接口 + terrain.TERRAIN_DEFS 参数完整性。
"""

import pytest

from ascend.space.terrain import TERRAIN_DEFS, TerrainType, get_terrain_def, state_params
from ascend.space.state_defs import (
    STATE_TYPES,
    COVERAGE_SPECS,
    StateConfig,
    StateParams,
    CoverageSpec,
    coverage_for,
    state_keys,
)


class TestStateRegistry:
    """状态注册表结构完整性。"""

    def test_state_types_all_states_present(self):
        """首发三状态齐全。"""
        assert set(STATE_TYPES) == {"moisture", "snow", "ice"}

    def test_state_keys_match_registry(self):
        """state_keys 与注册表键一致（blob 布局契约基准）。"""
        assert state_keys() == tuple(STATE_TYPES)

    def test_registry_entries_valid(self):
        """每注册项：key 自洽、bounds 合法、dtype 有效、阈值升序。"""
        from array import array
        for key, cfg in STATE_TYPES.items():
            assert cfg.key == key
            lo, hi = cfg.bounds
            assert 0 <= lo < hi <= 255, f"{key}: bounds 非法 {cfg.bounds}"
            assert cfg.dtype in ("B", "H"), f"{key}: dtype 非法 {cfg.dtype}"
            array(cfg.dtype)  # 类型码可用
            thresholds = cfg.thresholds
            assert thresholds == tuple(sorted(thresholds)), (
                f"{key}: 阈值非升序 {thresholds}"
            )
            if thresholds:
                assert all(lo < t <= hi for t in thresholds), (
                    f"{key}: 阈值越界 {thresholds} vs bounds {cfg.bounds}"
                )

    def test_overrides_passability_single(self):
        """仅结冰授权覆盖通行性（唯一例外）。"""
        override = [k for k, c in STATE_TYPES.items() if c.overrides_passability]
        assert override == ["ice"]

    def test_precip_triggers_valid(self):
        """precip_trigger 只能是 rain/snow/None。"""
        for key, cfg in STATE_TYPES.items():
            assert cfg.precip_trigger in (None, "rain", "snow"), (
                f"{key}: 非法 precip_trigger {cfg.precip_trigger}"
            )

    def test_thresholds_only_snow(self):
        """首发仅覆雪声明叙事阈值阶梯。"""
        with_thresholds = [
            k for k, c in STATE_TYPES.items() if c.thresholds
        ]
        assert with_thresholds == ["snow"]
        assert STATE_TYPES["snow"].thresholds == (15, 30, 50)


class TestTerrainStateParams:
    """地形注册表 × 状态参数矩阵完整性。"""

    def test_matrix_covers_all_terrains_and_states(self):
        """矩阵完整：每 TerrainType × 每状态必声明（None 或参数）。"""
        for terrain in TerrainType:
            states = get_terrain_def(terrain).states
            for key in state_keys():
                assert key in states, f"{terrain.name} 漏声明 {key}"

    def test_matrix_keys_match_registry(self):
        """注册表行不含未知状态 key。"""
        for name, defn in TERRAIN_DEFS.items():
            assert set(defn.states) == set(state_keys()), (
                f"{name}: 行键与注册表不一致"
            )

    def test_moisture_applicability(self):
        """湿润适用性：土壤类有参；沙地/岩石/水面 None。"""
        assert state_params(TerrainType.GRASSLAND, "moisture") is not None
        assert state_params(TerrainType.FERTILE_SOIL, "moisture") is not None
        assert state_params(TerrainType.MARSH, "moisture") is not None
        assert state_params(TerrainType.SAND, "moisture") is None
        assert state_params(TerrainType.ROCK, "moisture") is None
        assert state_params(TerrainType.WATER, "moisture") is None

    def test_snow_applicability(self):
        """覆雪适用性：陆地全有参（含岩石）；水面有参（冰上承载）。"""
        for terrain in TerrainType:
            assert state_params(terrain, "snow") is not None, (
                f"{terrain.name} 应可覆雪"
            )

    def test_ice_only_water(self):
        """结冰仅水面有参。"""
        assert state_params(TerrainType.WATER, "ice") is not None
        for terrain in TerrainType:
            if terrain != TerrainType.WATER:
                assert state_params(terrain, "ice") is None

    def test_marsh_drain_slower_than_grassland(self):
        """沼泽排水慢于草地（湿地语义的逐型微调）。"""
        marsh = state_params(TerrainType.MARSH, "moisture")
        grass = state_params(TerrainType.GRASSLAND, "moisture")
        assert marsh.drain < grass.drain

    def test_state_params_missing_key_fails_fast(self):
        """未知状态/未声明矩阵项抛 KeyError（fail fast）。"""
        with pytest.raises(KeyError):
            state_params(TerrainType.GRASSLAND, "nope")

    def test_params_frozen(self):
        """StateParams/StateConfig 不可变（frozen dataclass）。"""
        p = state_params(TerrainType.GRASSLAND, "moisture")
        with pytest.raises(Exception):
            p.deposit = 9.9
        cfg = STATE_TYPES["snow"]
        with pytest.raises(Exception):
            cfg.thresholds = (1,)


class TestCoverageSpecs:
    """遮蔽规格查询。"""

    def test_structure_full_cover(self):
        """建筑全遮蔽：雪与湿润沉积均为 0。"""
        spec = coverage_for("STRUCTURE")
        assert spec.snow == 0.0
        assert spec.moisture == 0.0

    def test_canopy_partial_cover(self):
        """树冠部分遮蔽（data 标记）。"""
        spec = coverage_for("CREATURE", {"canopy": True})
        assert spec.snow == 0.3
        assert spec.moisture == 0.8

    def test_open_sky_default(self):
        """露天无遮蔽（默认 1.0）。"""
        assert coverage_for("CREATURE") == CoverageSpec()
        assert coverage_for("PLANT", {"canopy": False}) == CoverageSpec()

    def test_coverage_specs_defined(self):
        """覆盖度表含建筑与树冠两档。"""
        assert set(COVERAGE_SPECS) == {"STRUCTURE", "canopy"}