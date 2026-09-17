"""世界程序装配测试：更新点绑定、执行顺序与 fail-closed 拒绝。"""

import pytest

from ascend.causal.program import compile_default_program
from ascend.config import GAME_HOUR
from ascend.runtime import FrameScheduler, apply_update_points


class TestApplyUpdatePoints:
    def test_registers_in_declared_order(self):
        program = compile_default_program()
        scheduler = FrameScheduler()
        log: list[tuple[str, int]] = []
        names = apply_update_points(program, scheduler, {
            "weather.evaluate": lambda now: log.append(
                ("weather.evaluate", now),
            ),
            "terrain.integrate": lambda now: log.append(
                ("terrain.integrate", now),
            ),
        })
        assert names == ("weather.evaluate", "terrain.integrate")
        scheduler.advance(GAME_HOUR)
        assert log == [
            ("weather.evaluate", GAME_HOUR),
            ("terrain.integrate", GAME_HOUR),
        ]

    def test_missing_binding_rejected(self):
        program = compile_default_program()
        with pytest.raises(ValueError, match="缺少"):
            apply_update_points(program, FrameScheduler(), {
                "weather.evaluate": lambda now: None,
            })

    def test_extra_binding_rejected(self):
        program = compile_default_program()
        with pytest.raises(ValueError, match="多余"):
            apply_update_points(program, FrameScheduler(), {
                "weather.evaluate": lambda now: None,
                "terrain.integrate": lambda now: None,
                "extra.point": lambda now: None,
            })
