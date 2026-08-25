"""命运织机测试 — 种子派生确定性随机流契约。

承诺（设计文档: docs/世界框架/随机系统/设计.md）:
  - derive 为纯函数：sha256 规范编码，跨平台位级一致，禁用内建 hash()
  - 流独立性是构造性的：同身份同序列（无视交错求值序），异身份独立
  - CRN 纪律（研究 05-E5）：do 干预不得改变未干预上游流的取值
"""

import random

import pytest

from ascend.fate import FateStream, LoomOfFate, derive, format_fate_path

MASK_256 = (1 << 256) - 1


def _draws(stream: random.Random, n: int = 8) -> list[float]:
    return [stream.random() for _ in range(n)]


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
        """domain("a").stream(purpose="b") ≡ stream("a", purpose="b")。"""
        loom = LoomOfFate(42)
        direct = loom.stream("env", purpose="weather", tick=100)
        nested = loom.domain("env").stream(purpose="weather", tick=100)
        assert direct.identity == nested.identity
        assert _draws(direct) == _draws(nested)

    def test_same_identity_same_stream(self):
        loom = LoomOfFate(7)
        s1 = loom.stream(entity_id="42", purpose="decision", tick=1000)
        s2 = loom.stream(entity_id="42", purpose="decision", tick=1000)
        assert _draws(s1) == _draws(s2)

    def test_different_identity_independent(self):
        loom = LoomOfFate(7)
        a = _draws(loom.stream(entity_id="1", purpose="decision", tick=1))
        b = _draws(loom.stream(entity_id="2", purpose="decision", tick=1))
        assert a != b

    def test_order_independent_interleaving(self):
        """CRN 前提：交错求值不影响取值（顺序无关）。"""
        loom = LoomOfFate(20260806)
        # 先取 X 再取无关流
        x1 = _draws(loom.stream(entity_id="x", purpose="decision", tick=9))
        _draws(loom.stream(entity_id="noise_a", purpose="whatever", tick=9))
        _draws(loom.stream(entity_id="noise_b", purpose="whatever", tick=9))
        # 先取无关流再取 X
        _draws(loom.stream(entity_id="noise_a", purpose="whatever", tick=9))
        _draws(loom.stream(entity_id="noise_b", purpose="whatever", tick=9))
        x2 = _draws(loom.stream(entity_id="x", purpose="decision", tick=9))
        assert x1 == x2

    def test_crn_discipline_do_intervention(self):
        """研究 05-E5 判据：do 干预改变执行路径，未干预流取值逐位不变。

        基线：只消费 X 与无关流 A。
        干预（do 语义）：被干预节点 Z 的机制被常数方程替换 → Z 的流
        从不被消费（设计文档 do 契约）；执行路径因大量无关流消费而
        改变——X/A 的取值必须不变。
        """
        loom = LoomOfFate(20260806)
        x_base = _draws(loom.stream(entity_id="x", purpose="decision", tick=5))
        a_base = _draws(loom.stream(entity_id="a", purpose="decision", tick=5))

        # 干预运行：Z 的机制被替换（流弃用，从不消费），
        # 且执行路径插入大量无关流抽取
        for i in range(20):
            _draws(loom.stream(entity_id=f"extra{i}", purpose="noise", tick=5))
        x_do = _draws(loom.stream(entity_id="x", purpose="decision", tick=5))
        a_do = _draws(loom.stream(entity_id="a", purpose="decision", tick=5))
        assert x_do == x_base
        assert a_do == a_base

    def test_stream_kwargs_only_extra_sorted(self):
        loom = LoomOfFate(3)
        s1 = loom.stream("k", extra_b=2, extra_a=1)
        s2 = loom.stream("k", extra_a=1, extra_b=2)
        assert _draws(s1) == _draws(s2)

    def test_fork_derives_from_parent(self):
        loom = LoomOfFate(5)
        parent = loom.stream(entity_id="e", purpose="p", tick=10)
        child = parent.fork("sub")
        assert child.identity == ("e", "p", 10, "sub")
        expect = random.Random(derive(5, "e", "p", 10, "sub"))
        assert _draws(child) == _draws(expect)


class TestFateStream:
    def test_seed_override_rejected(self):
        stream = LoomOfFate(1).stream(entity_id="e", purpose="p")
        with pytest.raises(RuntimeError):
            stream.seed(123)

    def test_is_random_subclass(self):
        stream = LoomOfFate(1).stream(entity_id="e", purpose="p")
        assert isinstance(stream, random.Random)
        assert 0.0 <= stream.random() < 1.0
        assert stream.randint(0, 10) in range(11)

    def test_fate_path_with_tick(self):
        stream = LoomOfFate(1).stream(
            entity_id="42", purpose="decision", tick=3912
        )
        assert stream.fate_path == "42/decision@3912"

    def test_fate_path_without_tick(self):
        stream = LoomOfFate(1).stream(entity_id="42", purpose="personality")
        assert stream.fate_path == "42/personality"

    def test_fate_path_multiple_int_parts(self):
        stream = LoomOfFate(1).stream(
            "feature", "block", 3, -2, "segment", 5, tick=100
        )
        assert stream.fate_path == "feature/block/3/-2/segment/5@100"

    def test_fate_path_domain_nested(self):
        stream = LoomOfFate(1).domain("weather").stream(
            entity_id="region", purpose="precip", tick=7
        )
        assert stream.fate_path == "weather/region/precip@7"

    def test_identity_attribute(self):
        stream = LoomOfFate(9).stream("a", entity_id="b", tick=3)
        assert stream.identity == ("a", "b", 3)


class TestFormatFatePath:
    def test_empty(self):
        assert format_fate_path(()) == ""

    def test_single_tick(self):
        assert format_fate_path((100,)) == "@100"

    def test_str_only(self):
        assert format_fate_path(("npc", "42", "decision")) == "npc/42/decision"


class TestSeedingRandomFrom256Bit:
    def test_random_deterministic_from_derive(self):
        """256-bit seed → random.Random 播种跨实例确定（MT init_by_array）。"""
        seed = derive(42, "npc", "7", "decision")
        r1 = random.Random(seed)
        r2 = random.Random(seed)
        assert _draws(r1) == _draws(r2)
        assert 0 <= seed <= MASK_256


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