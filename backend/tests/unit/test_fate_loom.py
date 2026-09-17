"""命运随机测试 — 种子派生与地址随机的确定性契约。

承诺（《世界契约》WC-5；设计文档: docs/世界框架/随机系统/设计.md）:
  - derive 为纯函数：sha256 规范编码，跨平台位级一致，禁用内建 hash()
  - 地址 → 值：不存在流状态与消费顺序；任一抽取顺序、跳过、新增，
    都不改变其他地址的取值（WC-5.1/5.2）
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from ascend.fate import (
    FATE_ALGORITHM,
    FateAddress,
    LoomOfFate,
    address_seed,
    address_value,
    derive,
)

MASK_256 = (1 << 256) - 1
_BACKEND_ROOT = Path(__file__).resolve().parents[2]


class TestDerive:
    def test_deterministic(self):
        assert derive(42, "npc", 7, "decision") == derive(42, "npc", 7, "decision")
        assert derive(0) == derive(0)
        assert derive(2**256 - 1, "a") == derive(2**256 - 1, "a")

    def test_range_256bit(self):
        for args in [(0,), (42, "npc"), (1, "a", "b", 3), (2**256 - 1, "x")]:
            assert 0 <= derive(*args) <= MASK_256

    def test_sensitive_to_each_part(self):
        base = derive(123, "npc", "42", "decision", 1000)
        variants = [
            derive(124, "npc", "42", "decision", 1000),   # parent
            derive(123, "npc", "43", "decision", 1000),   # entity
            derive(123, "npc", "42", "reproduction", 1000),  # purpose
            derive(123, "npc", "42", "decision", 1001),   # tick
            derive(123, "npc", "42", "decision", 1000, "extra"),  # part 数
        ]
        for v in variants:
            assert v != base
        # 雪崩：单字符差异应翻转大量比特（均匀扩散，非邻近值）
        a = derive(7, "weather", "feature")
        b = derive(7, "weather", "featurf")
        assert bin(a ^ b).count("1") > 100  # 256 位中 >100 位翻转

    def test_encoding_unambiguous(self):
        # 类型标签 + 长度前缀：拼接歧义必须隔离
        assert derive(1, "a", 1) != derive(1, "a1")
        assert derive(1, "a", 1) != derive(1, "a", 2)
        assert derive(1, "a", 1) != derive(1, "ab", -1)
        assert derive(1, 1, "a") != derive(1, "1a")
        assert derive(1, "a", "b") != derive(1, "ab")

    def test_int_negative_and_large(self):
        assert derive(9, -5) == derive(9, -5)
        assert derive(9, -5) != derive(9, 5)
        assert derive(9, 2**200) == derive(9, 2**200)
        assert derive(9, 2**200) != derive(9, 2**200 + 1)

    def test_stream_separation_by_identity(self):
        # 不同身份 → 无碰撞（256-bit 生日界 2^128，工程尺度可断言不相等）
        ids = [
            (0, "npc", "1", "decision", 1),
            (0, "npc", "2", "decision", 1),
            (0, "npc", "1", "reproduction", 1),
            (0, "npc", "1", "decision", 2),
        ]
        vals = {derive(*i) for i in ids}
        assert len(vals) == len(ids)


class TestLoomOfFate:
    def test_derive_scoped_to_world(self):
        loom = LoomOfFate(20260806)
        assert loom.derive("world", "birth_point") == derive(
            20260806, "world", "birth_point"
        )
        assert LoomOfFate(1).derive("x") != LoomOfFate(2).derive("x")

    def test_domain_path_composition(self):
        loom = LoomOfFate(42)
        assert loom.domain("env").derive("weather") == \
            loom.derive("env", "weather")
        assert loom.domain("a").domain("b").derive("c") == \
            loom.derive("a", "b", "c")


class TestFateAddress:
    def test_parts_canonical_order(self):
        address = FateAddress(
            "weather", "texture.channel", ("temperature", 3), time=7,
            draw_index=2,
        )
        assert address.parts() == (
            "weather", "temperature", 3, "texture.channel", 7, 2,
        )

    def test_defaults_are_deterministic(self):
        assert FateAddress("world", "birth_point").parts() == (
            "world", "birth_point", 0, 0,
        )

    @pytest.mark.parametrize(
        "namespace,purpose",
        [("", "x"), ("Xxx", "x"), ("a b", "x"), ("-a", "x"),
         ("a", ""), ("a", "Tbd:#48"), ("a", "x y")],
    )
    def test_invalid_namespace_or_purpose_rejected(self, namespace, purpose):
        with pytest.raises(ValueError):
            FateAddress(namespace, purpose)

    @pytest.mark.parametrize(
        "instance",
        [("ok", None), (True,), (1.5,), (b"x",)],
    )
    def test_invalid_instance_part_rejected(self, instance):
        with pytest.raises(ValueError):
            FateAddress("a", "b", instance)

    @pytest.mark.parametrize("time,draw_index", [(-1, 0), (0, -1), (True, 0)])
    def test_invalid_time_or_index_rejected(self, time, draw_index):
        with pytest.raises(ValueError):
            FateAddress("a", "b", time=time, draw_index=draw_index)


class TestAddressValue:
    def test_deterministic(self):
        address = FateAddress("weather", "proxy.temp")
        assert address_value(7, address, minimum=0, maximum=99) == \
            address_value(7, address, minimum=0, maximum=99)
        assert address_seed(7, address) == address_seed(7, address)

    def test_seed_is_derive_of_parts(self):
        address = FateAddress("weather", "proxy.temp", ("rain",), 5, 1)
        assert address_seed(123, address) == derive(123, *address.parts())

    def test_order_independent_interleaving(self):
        """CRN 前提：交错求值不影响取值（顺序无关）。"""
        address = FateAddress("weather", "texture.wind")
        first = address_value(9, address, minimum=0, maximum=255)
        for i in range(50):
            address_value(
                9,
                FateAddress("weather", "noise", (i,)),
                minimum=0,
                maximum=255,
            )
        assert address_value(9, address, minimum=0, maximum=255) == first

    def test_skipped_indices_do_not_recycle(self):
        """缺抽不回收：跳过抽取序号不影响后续地址取值。"""
        probe = FateAddress("weather", "proxy.rain", draw_index=10)
        direct = address_value(11, probe, minimum=0, maximum=10**9)
        for i in range(10):
            address_value(
                11,
                FateAddress("weather", "proxy.rain", draw_index=i),
                minimum=0,
                maximum=10**9,
            )
        after = address_value(11, probe, minimum=0, maximum=10**9)
        assert after == direct

    def test_range_bounds_inclusive(self):
        assert address_value(
            1,
            FateAddress("world", "birth_point"),
            minimum=3,
            maximum=3,
        ) == 3
        for index in range(20):
            value = address_value(
                1,
                FateAddress("world", "birth_point", draw_index=index),
                minimum=0,
                maximum=255,
            )
            assert 0 <= value <= 255
        assert -5 <= address_value(
            1,
            FateAddress("world", "birth_point"),
            minimum=-5,
            maximum=5,
        ) <= 5

    def test_instance_time_index_sensitive(self):
        base = FateAddress("weather", "texture.channel", ("temp", 0), 5, 0)
        variants = [
            FateAddress("weather", "texture.channel", ("temp", 1), 5, 0),
            FateAddress("weather", "texture.channel", ("temp", 0), 6, 0),
            FateAddress("weather", "texture.channel", ("temp", 0), 5, 1),
            FateAddress("weather", "other", ("temp", 0), 5, 0),
            FateAddress("world", "texture.channel", ("temp", 0), 5, 0),
        ]
        values = {
            address_value(3, address, minimum=0, maximum=MASK_256)
            for address in [base, *variants]
        }
        assert len(values) == 1 + len(variants)

    def test_distribution_is_flat_enough(self):
        """取模映射：100 桶 × 10000 次抽取，各桶应落在 4σ 邻域。"""
        buckets = [0] * 100
        for index in range(10000):
            value = address_value(
                20260806,
                FateAddress("weather", "proxy.temp", draw_index=index),
                minimum=0,
                maximum=99,
            )
            buckets[value] += 1
        assert all(60 <= count <= 140 for count in buckets)

    @pytest.mark.parametrize(
        "minimum,maximum",
        [(0, -1), (5, 4), (True, 3), (0, True)],
    )
    def test_invalid_range_rejected(self, minimum, maximum):
        address = FateAddress("world", "birth_point")
        with pytest.raises(ValueError):
            address_value(1, address, minimum=minimum, maximum=maximum)

    def test_golden_values(self):
        """黄金值：改动算法/编码/分量序必然改值（世界身份变更）。"""
        assert FATE_ALGORITHM == "sha256/derive-1/mod"
        address = FateAddress("weather", "proxy.temp", ("rain",), 5, 2)
        assert address.parts() == ("weather", "rain", "proxy.temp", 5, 2)
        assert address_seed(20260806, address) == (
            68261482312194683340399700917897345018326434771545780184127317168849104368709
        )
        assert address_value(
            20260806, address, minimum=0, maximum=99,
        ) == 9
        assert address_value(
            1,
            FateAddress("world", "birth_point"),
            minimum=0,
            maximum=2**64 - 1,
        ) == 6362245666454638048


class TestProcessIsolation:
    """跨进程一致：派生算法不依赖 PYTHONHASHSEED 等进程状态。"""

    def _run(self, hashseed: str) -> int:
        code = (
            "from ascend.fate import FateAddress, address_value;"
            "a=FateAddress('weather','proxy.temp',('rain',),5,2);"
            "print(address_value(20260806,a,minimum=0,maximum=2**64-1))"
        )
        env = dict(os.environ)
        env["PYTHONHASHSEED"] = hashseed
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            check=True,
            env=env,
            cwd=_BACKEND_ROOT,
        )
        return int(result.stdout.strip())

    def test_matches_subprocess_with_other_hash_seeds(self):
        expected = address_value(
            20260806,
            FateAddress("weather", "proxy.temp", ("rain",), 5, 2),
            minimum=0,
            maximum=2**64 - 1,
        )
        assert self._run("0") == expected
        assert self._run("4242") == expected


class TestWeatherMigrationEquivalence:
    """天气派生迁移契约：API 不变，同 seed 确定性、异 seed 差异。"""

    def _samples(self, seed: int, t: int = 100000) -> tuple:
        from ascend.weather.field import (
            CH_PRECIPITATION, CH_TEMPERATURE, UnifiedWeatherField,
        )
        field = UnifiedWeatherField(seed=seed)
        return (
            field.sample(CH_TEMPERATURE, 1234.5, -678.9, t),
            field.sample(CH_PRECIPITATION, 1234.5, -678.9, t),
        )

    def test_same_seed_deterministic(self):
        assert self._samples(42) == self._samples(42)

    def test_different_seed_differs(self):
        a = self._samples(42)
        b = self._samples(43)
        assert a != b
