"""存档清单契约测试 — 世界 seed 的协议层 hex 序列化/解析（单源）。

覆盖 ascend/save/manifest.py 的 seed 契约：
  - SEED_MAX = 2^256-1（世界空间）
  - seed_to_hex：int → 小写 hex（无 0x 前缀，小 seed 短表示）
  - parse_seed：hex 字符串 / int / null / 占位 / 越界 / 非法输入
"""

import pytest

from ascend.save.manifest import SEED_MAX, parse_seed, seed_to_hex


class TestSeedToHex:
    def test_small_seed_short_form(self):
        assert seed_to_hex(42) == "2a"
        assert seed_to_hex(0) == "0"
        assert seed_to_hex(1) == "1"

    def test_max_seed_full_width(self):
        assert len(seed_to_hex(SEED_MAX)) == 64
        assert seed_to_hex(SEED_MAX) == "f" * 64

    def test_lowercase(self):
        assert seed_to_hex(0xABC123) == "abc123"
        assert seed_to_hex(SEED_MAX).islower()


class TestParseSeed:
    def test_placeholder_forms(self):
        """"" / "0" / 缺省（None）→ 0 = 随机占位。"""
        for raw in ("", "0", None):
            assert parse_seed(raw) == 0

    def test_hex_string(self):
        assert parse_seed("2a") == 42
        assert parse_seed("A3F9") == 0xA3F9  # 大小写均可

    def test_max_seed_round_trip(self):
        big = "f" * 64
        assert parse_seed(big) == SEED_MAX
        assert seed_to_hex(parse_seed(big)) == big

    def test_int_compat(self):
        assert parse_seed(42) == 42
        assert parse_seed(SEED_MAX) == SEED_MAX

    def test_sign_and_prefix_rejected(self):
        """符号与 0x 前缀显式拒绝（严格无符号无前缀 hex）。"""
        for bad in ("+2a", "-2a", "-1", "0x2a", "0X2A"):
            with pytest.raises(ValueError):
                parse_seed(bad)

    def test_invalid_hex_rejected(self):
        for bad in ("zz", "g", "2a!", "1.5"):
            with pytest.raises(ValueError):
                parse_seed(bad)

    def test_whitespace_only_is_placeholder(self):
        """纯空白经 strip 视同占位（与缺省一致）。"""
        assert parse_seed("   ") == 0

    def test_bool_rejected(self):
        """bool 是 int 子类——显式拒绝，避免 True→1 误入。"""
        with pytest.raises(ValueError):
            parse_seed(True)
        with pytest.raises(ValueError):
            parse_seed(False)

    def test_out_of_range_rejected(self):
        with pytest.raises(ValueError):
            parse_seed("1" + "0" * 64)  # 2^256
        with pytest.raises(ValueError):
            parse_seed(-1)

    def test_float_rejected(self):
        with pytest.raises(ValueError):
            parse_seed(1.5)

    def test_round_trip(self):
        for seed in (0, 1, 42, 2**64, SEED_MAX):
            assert parse_seed(seed_to_hex(seed)) == seed