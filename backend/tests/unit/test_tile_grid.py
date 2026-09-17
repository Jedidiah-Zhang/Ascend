"""TileGrid 状态数组整组替换（帧边界提交路径）测试。"""

from array import array

import pytest

from ascend.space.state_defs import STATE_TYPES, state_keys
from ascend.space.tile_grid import TileGrid


def _valid_states(**overrides) -> dict[str, array]:
    states = {}
    for key, cfg in STATE_TYPES.items():
        states[key] = array(cfg.dtype, [5]) * 40000
    states.update(overrides)
    return states


class TestReplaceStates:
    def test_replaces_all_state_arrays(self):
        grid = TileGrid()
        states = _valid_states()
        grid.replace_states(states)
        for key in state_keys():
            assert grid.state_raw(key) is states[key]
            assert max(grid.state_raw(key)) == 5

    def test_missing_state_rejected(self):
        grid = TileGrid()
        states = _valid_states()
        missing = state_keys()[0]
        del states[missing]
        with pytest.raises(ValueError, match="array"):
            grid.replace_states(states)

    def test_wrong_length_rejected(self):
        grid = TileGrid()
        key = state_keys()[0]
        states = _valid_states(**{key: array("B", [1, 2, 3])})
        with pytest.raises(ValueError, match="长度"):
            grid.replace_states(states)

    def test_wrong_typecode_rejected(self):
        grid = TileGrid()
        key = state_keys()[0]
        states = _valid_states(**{key: array("H", [1]) * 40000})
        with pytest.raises(ValueError, match="类型"):
            grid.replace_states(states)
