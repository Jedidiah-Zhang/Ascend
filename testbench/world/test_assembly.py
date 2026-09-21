"""游戏世界装配测试— 声明切片、驱动周期与存档视图。

装配 = 天气引擎求值面（求值子集）+ 地形积分 + 驱动周期；它是存档
身份（manifest 声明视图/程序视图）与调度绑定的事实源。
"""

from __future__ import annotations

from olam.constants import GAME_HOUR, GAME_MINUTE
from olam.assembly import DRIVER_PERIODS, build_game_program
from olam.modules.weather.core import ENGINE_EVAL_OUTPUTS


class TestGameProgram:
    def test_identity_reproducible(self):
        assert build_game_program().identity == build_game_program().identity

    def test_covers_weather_eval_face(self):
        program = build_game_program()
        writers = {
            slot.writer for slot in program.slots.values()
            if slot.writer is not None
        }
        for slot_id in ENGINE_EVAL_OUTPUTS:
            assert slot_id in program.slots
            assert program.slots[slot_id].writer in writers

    def test_covers_terrain_integration(self):
        program = build_game_program()
        assert any(
            mechanism.when.mode == "period"
            and mechanism.when.key == "hour"
            for mechanism in program.mechanisms.values()
        )

    def test_driver_periods_declared(self):
        program = build_game_program()
        assert program.schedule.periods == DRIVER_PERIODS
        assert program.schedule.period_ticks("minute") == GAME_MINUTE
        assert program.schedule.period_ticks("hour") == GAME_HOUR

    def test_declaration_settings_fields(self):
        program = build_game_program()
        view = program.declaration_settings()
        assert set(view) == {
            "declaration_id", "declaration_hash",
            "observation_protocol_version",
        }
        assert all(isinstance(value, str) and value for value in view.values())
        assert view["declaration_hash"] == program.identity
        assert view["declaration_id"] == program.contract

    def test_settings_identity_and_diagnostics(self):
        program = build_game_program()
        view = program.settings()
        assert view["identity"] == program.identity
        assert view["kernel"] == program.kernel
        assert view["module_digests"] == dict(program.module_digests)

    def test_observation_protocol_version_is_stable(self):
        first = build_game_program().observation_protocol_version()
        second = build_game_program().observation_protocol_version()
        assert first == second
        assert first.startswith("sha256:")


class TestPackagedIdentity:
    """打包身份：源码不可读时摘要取自随包记录表（fail-closed）。"""

    def test_source_digest_falls_back_to_recorded_table(self, monkeypatch):
        from olam.meta import validate

        mechanism = next(iter(build_game_program().mechanisms.values()))
        expected = validate.source_digest(mechanism.impl)

        def unavailable(*args, **kwargs):
            raise OSError("packaged")

        monkeypatch.setattr(validate.inspect, "getsource", unavailable)
        assert validate.source_digest(mechanism.impl) == expected

    def test_missing_recorded_digest_fails_closed(self, monkeypatch):
        import pytest

        from olam.meta import validate

        mechanism = next(iter(build_game_program().mechanisms.values()))

        def unavailable(*args, **kwargs):
            raise OSError("packaged")

        monkeypatch.setattr(validate.inspect, "getsource", unavailable)
        monkeypatch.setattr(validate, "_recorded_impl_digests", dict)
        with pytest.raises(ValueError, match="缺少实现摘要"):
            validate.source_digest(mechanism.impl)

    def test_kernel_digest_falls_back_to_recorded_value(self, monkeypatch):
        from olam.meta import validate

        expected = validate.kernel_digest()

        def unavailable(*args, **kwargs):
            raise OSError("packaged")

        monkeypatch.setattr(validate, "file_digest", unavailable)
        assert validate.kernel_digest() == expected
