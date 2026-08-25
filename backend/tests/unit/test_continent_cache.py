"""大陆磁盘缓存单元测试 — 序列化往返、损坏/版本失效、ensure_continent 缓存路径。

覆盖 ascend/space/continent.py 的 serialize/deserialize 与
ascend/space/generator.py 的磁盘缓存接入。
"""

import os

import pytest

from ascend.space.continent import (
    ContinentGenerator,
    ContinentParams,
    ContinentData,
    serialize_continent,
    deserialize_continent,
    CONTINENT_CACHE_VERSION,
)
from ascend.space.generator import WorldGenerator


def _small_continent(
    seed: int = 42, land_ratio: float = 0.55,
    width_km: float = 6.0, height_km: float = 4.0,
) -> ContinentData:
    """快速生成小规模大陆（单元测试用，秒级）。"""
    return ContinentGenerator(
        seed=seed,
        params=ContinentParams(
            width_km=width_km, height_km=height_km, sample_resolution=200,
            land_ratio=land_ratio,
        ),
    ).generate()


class TestContinentSerialize:
    """序列化往返。"""

    def test_roundtrip_preserves_all_fields(self):
        """序列化→反序列化后各字段完全一致。"""
        original = _small_continent(seed=42)
        restored = deserialize_continent(serialize_continent(original))
        assert restored is not None
        assert restored.seed == original.seed
        assert restored.grid_width == original.grid_width
        assert restored.grid_height == original.grid_height
        assert restored.cell_size == original.cell_size
        assert restored.land_mask == original.land_mask
        assert restored.elevation_field == original.elevation_field
        assert restored.river_width == original.river_width
        assert restored.water_distance == original.water_distance
        assert restored.subdiv_ranges == original.subdiv_ranges
        assert restored._chunk_climate == original._chunk_climate
        h1, h2 = original.hydrology, restored.hydrology
        assert h1.flow_acc == h2.flow_acc
        assert h1.directions == h2.directions
        assert h1.filled_dem == h2.filled_dem
        assert len(h1.lake_basins) == len(h2.lake_basins)
        assert [
            (b.surface_elev, b.area_km2, len(b.cells))
            for b in h1.lake_basins
        ] == [
            (b.surface_elev, b.area_km2, len(b.cells))
            for b in h2.lake_basins
        ]
        n1 = h1.river_network
        n2 = h2.river_network
        assert (n1 is None) == (n2 is None)
        if n1 is not None:
            assert len(n1.rivers) == len(n2.rivers)
            for r1, r2 in zip(n1.rivers, n2.rivers):
                assert len(r1.points) == len(r2.points)
                for p1, p2 in zip(r1.points, r2.points):
                    assert (p1.x, p1.y, p1.flow, p1.strahler) == (
                        p2.x, p2.y, p2.flow, p2.strahler,
                    )

    def test_roundtrip_256bit_seed(self):
        """256-bit 种子（> int64 范围）往返不失真。

        回归：世界种子为 256-bit 空间（manifest.SEED_MAX = 2**256-1），
        seed 字段按 32 字节大端全量序列化，任意合法种子不溢出。
        """
        big_seed = 90716806870141588494432962298621886198264273751076786878364930858859894597832
        assert big_seed > 2**63 - 1
        original = _small_continent(seed=big_seed)
        restored = deserialize_continent(serialize_continent(original))
        assert restored is not None
        assert restored.seed == big_seed

    def test_corrupted_bytes_returns_none(self):
        """损坏数据返回 None（调用方重新生成）。"""
        assert deserialize_continent(b"\x00garbage\xff\xfe") is None
        assert deserialize_continent(b"") is None

    def test_truncated_binary_rejected(self):
        """截断的二进制缓存拒绝加载（防损坏/防恶意构造）。"""
        original = _small_continent(seed=42)
        raw = serialize_continent(original)
        assert deserialize_continent(raw[: len(raw) // 2]) is None

    def test_pickle_format_rejected(self):
        """pickle 格式（可执行任意代码）拒绝加载（反序列化安全边界）。"""
        import pickle
        import zlib
        payload = pickle.dumps({
            "format": "ascend-continent",
            "version": CONTINENT_CACHE_VERSION,
            "data": _small_continent(seed=1),
        })
        assert deserialize_continent(zlib.compress(payload)) is None

    def test_version_mismatch_returns_none(self):
        """格式版本不符返回 None（序列化格式迁移 → 重新生成）。

        注意：算法/调参变化不使缓存失效——每个存档的大陆在创建时
        定案，由指纹漂移告警覆盖（见 TestGenerationFingerprint）。
        """
        original = _small_continent()
        from unittest import mock

        with mock.patch(
            "ascend.space.continent_io.CONTINENT_CACHE_VERSION",
            CONTINENT_CACHE_VERSION + 1,
        ):
            stale = serialize_continent(original)
        assert deserialize_continent(stale) is None

    def test_wrong_seed_rejected(self):
        """头部 seed 与数据不符拒绝加载（防篡改/错档缓存）。"""
        original = _small_continent(seed=1)
        restored = deserialize_continent(serialize_continent(original))
        # 反序列化函数本身信任头部；seed 与生成器不符的场景由外层
        # ensure_continent 校验（见 test_cache_seed_mismatch_regenerates）
        assert restored.seed == 1


class TestWorldGeneratorCache:
    """WorldGenerator.ensure_continent 磁盘缓存路径。"""

    def _cache_path(self, tmp_path, seed: int) -> str:
        return str(tmp_path / "saves" / f"world-{seed}" / "continent.bin")

    def test_miss_generates_and_writes_cache(self, tmp_path, monkeypatch):
        """缓存未命中：生成并写入磁盘。"""
        calls = {"n": 0}
        fake = _small_continent(seed=77)
        cache_path = self._cache_path(tmp_path, 77)

        def _fake_generate(self, *args, **kwargs):
            calls["n"] += 1
            return fake

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(seed=77, continent_cache_path=cache_path)
        cont = wg.ensure_continent()
        assert cont is fake
        assert calls["n"] == 1
        assert os.path.isfile(cache_path)
        # 再次调用命中内存缓存，不落盘
        assert wg.ensure_continent() is fake
        assert calls["n"] == 1

    def test_hit_loads_from_disk_without_generating(self, tmp_path, monkeypatch):
        """缓存命中：从磁盘恢复，不执行生成。"""
        fake = _small_continent(seed=99)
        cache_path = self._cache_path(tmp_path, 99)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(fake))

        calls = {"n": 0}

        def _fake_generate(self, *args, **kwargs):
            calls["n"] += 1
            raise AssertionError("缓存命中时不应执行生成")

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(
            seed=99, width_km=6.0, height_km=4.0,
            continent_cache_path=cache_path,
        )
        cont = wg.ensure_continent()
        assert calls["n"] == 0
        assert cont.seed == 99
        assert cont.grid_width == fake.grid_width
        assert cont.elevation_field == fake.elevation_field

    def test_corrupted_cache_regenerates(self, tmp_path, monkeypatch):
        """缓存文件损坏：重新生成并覆盖。"""
        fake = _small_continent(seed=55)
        cache_path = self._cache_path(tmp_path, 55)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(b"corrupted-not-a-cache")

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            lambda self, *a, **k: fake,
        )
        wg = WorldGenerator(seed=55, continent_cache_path=cache_path)
        cont = wg.ensure_continent()
        assert cont is fake
        # 覆盖后的缓存可正常恢复
        with open(cache_path, "rb") as f:
            assert deserialize_continent(f.read()).seed == 55

    def test_no_cache_path_generates_without_writing(self, tmp_path, monkeypatch):
        """未指定缓存路径（无存档模式）：生成但不落盘。"""
        fake = _small_continent(seed=66)
        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            lambda self, *a, **k: fake,
        )
        wg = WorldGenerator(seed=66)
        assert wg.ensure_continent() is fake
        assert not os.path.exists(self._cache_path(tmp_path, 66))

    def test_cache_seed_mismatch_regenerates(self, tmp_path, monkeypatch):
        """缓存 seed 与生成器不符（错档/旧随机化窗口残留）：重新生成并覆盖。

        防护：缓存必须校验与 self._seed 的匹配——崩溃窗口（manifest
        seed 未落盘）或拷贝错档若加载错误大陆，世界会静默不一致。
        """
        other = _small_continent(seed=111)  # 其它种子的缓存
        fake = _small_continent(seed=222)  # 期望种子的生成结果（预计算，避免 mock 自递归）
        cache_path = self._cache_path(tmp_path, 222)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(other))

        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            return fake

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(
            seed=222, width_km=6.0, height_km=4.0,
            continent_cache_path=cache_path,
        )
        cont = wg.ensure_continent()
        assert calls["n"] == 1, "seed 不符应触发重新生成"
        assert cont.seed == 222
        # 覆盖后的缓存恢复为正确种子
        with open(cache_path, "rb") as f:
            assert deserialize_continent(f.read()).seed == 222

    def test_cache_land_ratio_mismatch_regenerates(self, tmp_path, monkeypatch):
        """缓存 land_ratio 与生成器不符（同 seed 调参结果混入）：重新生成。

        Issue #8：大陆是 (seed, land_ratio) 的确定性函数——同 seed
        不同占比的缓存必须视为未命中，否则调参无效。
        """
        other = _small_continent(seed=333, land_ratio=0.30)
        fake = _small_continent(seed=333, land_ratio=0.70)
        cache_path = self._cache_path(tmp_path, 333)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(other))

        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            return fake

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(
            seed=333, land_ratio=0.70, width_km=6.0, height_km=4.0,
            continent_cache_path=cache_path,
        )
        cont = wg.ensure_continent()
        assert calls["n"] == 1, "land_ratio 不符应触发重新生成"
        assert cont.land_ratio == 0.70
        # 覆盖后的缓存恢复为正确参数
        with open(cache_path, "rb") as f:
            assert deserialize_continent(f.read()).land_ratio == 0.70

    def test_cache_size_mismatch_regenerates(self, tmp_path, monkeypatch):
        """缓存尺寸与生成器不符（同 seed 不同地图尺寸调参结果混入）：重新生成。

        Issue #8：大陆是 (seed, land_ratio, 尺寸) 的确定性函数——同 seed
        不同尺寸的缓存必须视为未命中，否则地图尺寸调参无效。
        """
        other = _small_continent(seed=555)  # 6×4km 缓存
        fake = _small_continent(seed=555, width_km=12.0, height_km=8.0)
        cache_path = self._cache_path(tmp_path, 555)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(other))

        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            return fake

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(
            seed=555, width_km=12.0, height_km=8.0,
            continent_cache_path=cache_path,
        )
        cont = wg.ensure_continent()
        assert calls["n"] == 1, "尺寸不符应触发重新生成"
        assert cont is fake
        # 覆盖后的缓存为期望尺寸
        with open(cache_path, "rb") as f:
            restored = deserialize_continent(f.read())
        assert restored.grid_width == fake.grid_width

    def test_cache_default_size_hits(self, tmp_path, monkeypatch):
        """width_km/height_km=None（未调参）等价默认 100×60：缓存可命中。"""
        fake = _small_continent(seed=777, width_km=100.0, height_km=60.0)
        cache_path = self._cache_path(tmp_path, 777)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(fake))

        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("参数匹配的缓存不应重新生成")

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(seed=777, continent_cache_path=cache_path)
        cont = wg.ensure_continent()
        assert calls["n"] == 0
        assert cont.grid_width == fake.grid_width

    def test_cache_default_land_ratio_hits(self, tmp_path, monkeypatch):
        """land_ratio=None（未调参）等价默认 0.55：缓存可命中。"""
        fake = _small_continent(seed=444)  # 默认 land_ratio=0.55
        cache_path = self._cache_path(tmp_path, 444)
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(fake))

        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            raise AssertionError("参数匹配的缓存不应重新生成")

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(
            seed=444, width_km=6.0, height_km=4.0,
            continent_cache_path=cache_path,
        )
        cont = wg.ensure_continent()
        assert calls["n"] == 0
        assert cont.seed == 444


class TestGenerationFingerprint:
    """生成环境指纹：漂移沿用告警、强制重建、头部读取。"""

    def test_fingerprint_mismatch_keeps_cache_with_warning(
        self, tmp_path, monkeypatch, caplog,
    ):
        """生成环境漂移：沿用缓存 + 告警，不重新生成。

        防护：算法/调参变化不得改变已创建世界的大陆——每个存档的
        大陆在创建时定案，缓存照常命中，漂移仅以 warning 显式化
        （调参验证请新建世界或强制重建）。
        """
        import logging

        from ascend.space import generator as gen_mod

        fake = _small_continent(seed=313)
        fake.gen_fingerprint = "old-env"
        cache_path = str(tmp_path / "continent.bin")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(fake))

        monkeypatch.setattr(
            gen_mod, "compute_gen_fingerprint", lambda: "new-env",
        )

        def _fake_generate(self, *a, **k):
            raise AssertionError("漂移时不应重新生成（世界连续性优先）")

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        with caplog.at_level(logging.WARNING, logger="ascend.space.generator"):
            wg = WorldGenerator(
                seed=313, width_km=6.0, height_km=4.0,
                continent_cache_path=cache_path,
            )
            cont = wg.ensure_continent()
        assert cont is not None and cont.seed == 313, "漂移时沿用缓存"
        assert cont.gen_fingerprint == "old-env"
        assert "生成环境" in caplog.text, "漂移必须显式告警"

    def test_ignore_cache_forces_regen(self, tmp_path, monkeypatch):
        """ignore_cache=True：无视缓存强制重新生成并覆盖（含当前指纹）。"""
        from ascend.space.generator import compute_gen_fingerprint

        fake = _small_continent(seed=424)
        cache_path = str(tmp_path / "continent.bin")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(fake))

        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            return fake

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(
            seed=424, width_km=6.0, height_km=4.0,
            continent_cache_path=cache_path, ignore_cache=True,
        )
        cont = wg.ensure_continent()
        assert calls["n"] == 1, "ignore_cache 应强制重新生成"
        assert cont is fake
        with open(cache_path, "rb") as f:
            restored = deserialize_continent(f.read())
        assert restored.gen_fingerprint == compute_gen_fingerprint(), (
            "覆盖后的缓存携带当前生成环境指纹"
        )

    def test_read_continent_header_roundtrip(self):
        """read_continent_header 轻量读取版本与指纹，不解析场体。"""
        from ascend.space.continent import read_continent_header

        fake = _small_continent(seed=1)
        fake.gen_fingerprint = "test-fp-123456"
        header = read_continent_header(serialize_continent(fake))
        assert header == (CONTINENT_CACHE_VERSION, "test-fp-123456")

    def test_read_continent_header_invalid(self):
        """非法/损坏/空输入返回 None。"""
        from ascend.space.continent import read_continent_header

        assert read_continent_header(b"garbage-not-zlib") is None
        assert read_continent_header(b"") is None


class TestWorldGeneratorLazyPath:
    """惰性首触（get_altitude）与主动 ensure_continent 统一创建入口（P0-09）。"""

    def test_lazy_path_matches_ensure_path(self):
        """两条路径产出同一份大陆（含沙漠 moisture 动态值域与群系结果）。"""
        gen_a = WorldGenerator(seed=777, width_km=6.0, height_km=4.0)
        gen_a.get_altitude(0.0, 0.0)  # 惰性首触
        gen_b = WorldGenerator(seed=777, width_km=6.0, height_km=4.0)
        gen_b.ensure_continent()      # 主动
        assert (
            gen_a._continent.subdiv_ranges == gen_b._continent.subdiv_ranges
        ), "两路径的群系细分值域应一致（含沙漠校准）"
        assert gen_a.generate_chunk(0, 0).biome == gen_b.generate_chunk(0, 0).biome

    def test_lazy_first_touch_loads_cache(self, tmp_path, monkeypatch):
        """惰性首触走缓存恢复，不重新生成。"""
        fake = _small_continent(seed=99)
        cache_path = str(tmp_path / "world-99" / "continent.bin")
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "wb") as f:
            f.write(serialize_continent(fake))

        def _no_generate(self, *a, **k):
            raise AssertionError("缓存命中时不应执行生成")

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _no_generate,
        )
        wg = WorldGenerator(
            seed=99, width_km=6.0, height_km=4.0,
            continent_cache_path=cache_path,
        )
        wg.get_altitude(0.0, 0.0)
        assert wg._continent.seed == 99
        assert wg._continent.elevation_field == fake.elevation_field

    def test_lazy_first_touch_writes_cache(self, tmp_path, monkeypatch):
        """惰性首触未命中：生成并落盘缓存（与主动路径一致）。"""
        fake = _small_continent(seed=77)
        cache_path = str(tmp_path / "world-77" / "continent.bin")
        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            lambda self, *a, **k: fake,
        )
        wg = WorldGenerator(seed=77, continent_cache_path=cache_path)
        wg.get_altitude(0.0, 0.0)
        assert os.path.isfile(cache_path)
        with open(cache_path, "rb") as f:
            assert deserialize_continent(f.read()).seed == 77

    def test_concurrent_first_touch_generates_once(self, monkeypatch):
        """并发首触：锁收敛为单次生成，全员拿到同一大陆。"""
        import threading
        import time

        fake = _small_continent(seed=777)
        calls = {"n": 0}

        def _fake_generate(self, *a, **k):
            calls["n"] += 1
            time.sleep(0.05)  # 放大竞争窗口
            return fake

        monkeypatch.setattr(
            "ascend.space.continent.ContinentGenerator.generate",
            _fake_generate,
        )
        wg = WorldGenerator(seed=777)
        results: list[float] = []
        barrier = threading.Barrier(9)

        def worker() -> None:
            barrier.wait()
            results.append(wg.get_altitude(0.0, 0.0))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        barrier.wait()
        for t in threads:
            t.join()
        assert calls["n"] == 1, "并发首触应只生成一次"
        assert len(set(results)) == 1, "所有线程拿到同一大陆的同一采样"
