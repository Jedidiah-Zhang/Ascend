"""生成程序声明测试（P5）— 身份、清单对齐、边界绑定、确定性。

- 声明自洽：四个生成程序（大陆/水文/瓦片/统一天气场）字段完整；
- 清单对齐：旧 ``compute_gen_fingerprint`` 的源码/常量清单被完整覆盖
  （新增未归属即红）；
- 身份：指纹稳定、常量/版本变化即变、打包退位策略可复现；
- 边界绑定：``feeds`` 覆盖游戏程序的全部生成侧外部槽位（slice_boundary
  消解的可执行证据）；
- 确定性：大陆/瓦片/天气场重复取样与生成顺序无关（物化无关性）。
"""

from __future__ import annotations

import pytest

from ascend import config
from ascend.space.generator import _GEN_SOURCE_FILES
from ascend.world.assembly import build_game_program
from ascend.world.generation import (
    DRIVER_INPUT_SLOTS,
    GENERATION_PROGRAMS,
    generation_fingerprint,
    generation_identity,
)
from ascend.world.modules.weather.engine_inputs import BASELINE_IDS


def _program(program_id: str):
    return next(
        decl for decl in GENERATION_PROGRAMS if decl.id == program_id
    )


class TestDeclaration:
    def test_programs_declared(self):
        ids = [decl.id for decl in GENERATION_PROGRAMS]
        assert ids == [
            "generation.continent",
            "generation.hydrology",
            "generation.tile",
            "generation.weather_field",
        ]

    def test_sampling_protocol_declared(self):
        for decl in GENERATION_PROGRAMS:
            assert len(decl.sampling) >= 2, decl.id
            assert decl.determinism, decl.id

    def test_inventory_covers_legacy_fingerprint(self):
        """旧生成环境指纹的源码与常量必须全部有归属（漂移门禁）。"""
        declared_files = {
            rel for decl in GENERATION_PROGRAMS for rel in decl.source_files
        }
        legacy_files = {
            f"ascend/space/{name}" for name in _GEN_SOURCE_FILES
        }
        missing_files = legacy_files - declared_files
        assert not missing_files, missing_files
        declared_constants = {
            name for decl in GENERATION_PROGRAMS for name in decl.constants
        }
        missing_constants = set(config.CONTINENT_GEN_CONSTANT_NAMES) - \
            declared_constants
        assert not missing_constants, missing_constants

    def test_source_files_exist(self):
        from ascend.world.generation import BACKEND_ROOT

        for decl in GENERATION_PROGRAMS:
            for rel in decl.source_files:
                assert (BACKEND_ROOT / rel).is_file(), (decl.id, rel)


class TestFingerprint:
    def test_stable_and_distinct(self):
        first = generation_identity()
        second = generation_identity()
        assert first == second
        assert len(set(first.values())) == len(GENERATION_PROGRAMS)

    def test_constant_change_changes_fingerprint(self, monkeypatch):
        decl = _program("generation.continent")
        before = generation_fingerprint(decl)
        monkeypatch.setattr(config, "SEA_LEVEL_ELEV", 0.123)
        assert generation_fingerprint(decl) != before

    def test_version_change_changes_fingerprint(self):
        from dataclasses import replace

        decl = _program("generation.tile")
        bumped = replace(decl, version="2")
        assert generation_fingerprint(bumped) != generation_fingerprint(decl)

    def test_missing_constant_rejected(self):
        from dataclasses import replace

        decl = replace(
            _program("generation.tile"), constants=("NO_SUCH_CONSTANT",),
        )
        with pytest.raises(ValueError, match="常量未声明"):
            generation_fingerprint(decl)


class TestBoundaryBinding:
    def test_feeds_are_declared_slots(self):
        program = build_game_program()
        for decl in GENERATION_PROGRAMS:
            for slot_id in decl.feeds:
                assert slot_id in program.slots, (decl.id, slot_id)

    def test_feeds_cover_generation_side_externals(self):
        """生成侧外部槽位（基线 + 地形输入）全部有声明来源。"""
        program = build_game_program()
        externals = {
            slot.id for slot in program.slots.values()
            if slot.persist == "external"
        }
        generation_side = externals - set(DRIVER_INPUT_SLOTS)
        feeds = {
            slot_id for decl in GENERATION_PROGRAMS for slot_id in decl.feeds
        }
        assert feeds == generation_side
        assert set(BASELINE_IDS) <= generation_side

    def test_driver_slots_are_not_claimed(self):
        feeds = {
            slot_id for decl in GENERATION_PROGRAMS for slot_id in decl.feeds
        }
        assert not (feeds & set(DRIVER_INPUT_SLOTS))


class TestDeterminism:
    def _small_continent(self, seed: int = 42):
        from ascend.space.continent import ContinentGenerator, ContinentParams

        return ContinentGenerator(
            seed=seed,
            params=ContinentParams(
                width_km=6, height_km=4, sample_resolution=200,
            ),
        ).generate()

    def test_continent_sample_repeatable(self):
        continent = self._small_continent()
        first = continent.get_chunk_climate(0, 0)
        second = continent.get_chunk_climate(0, 0)
        assert first == second
        again = self._small_continent().get_chunk_climate(0, 0)
        assert again == first

    def test_tile_generation_order_independent(self):
        from ascend.space.tile_gen import TileGenerator

        continent = self._small_continent()
        alone = TileGenerator(seed=42, continent=continent)
        first = alone.generate_chunk(0, 0)

        ordered = TileGenerator(seed=42, continent=continent)
        ordered.generate_chunk(1, 0)
        ordered.generate_chunk(0, 1)
        second = ordered.generate_chunk(0, 0)

        assert list(first.raw_data()) == list(second.raw_data())
        assert list(first.elevation_raw()) == list(second.elevation_raw())
        assert first == second

    def test_weather_field_sample_order_independent(self):
        from ascend.weather.field import CH_TEMPERATURE, UnifiedWeatherField

        field = UnifiedWeatherField(seed=42)
        first = field.sample(CH_TEMPERATURE, 1234.0, 5678.0, 100)
        # 其他采样（含批量）不得影响既有取值
        field.sample_grid(CH_TEMPERATURE, 0.0, 0.0, 4, 4, 100)
        field.sample(CH_TEMPERATURE, 9999.0, 9999.0, 200)
        second = field.sample(CH_TEMPERATURE, 1234.0, 5678.0, 100)
        assert first == second
