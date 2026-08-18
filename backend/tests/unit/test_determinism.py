"""世界生成确定性测试 — 同 seed 黄金 hash。

固定 seed 的完整生成管线（大陆 → chunk 连续场 → tile 网格）输出
sha256 摘要，与固化黄金值比对。承诺定位：**同架构跨机器一致**
（x86 严格 IEEE-754；无 -ffast-math/-march=native 编译标志）。
CI runner 固定架构，跨架构（x86 vs ARM）不追求位级一致。

任何影响生成数值的修改（噪声算法、参数、浮点运算重排）都会
改变黄金值——须先评估影响再同步更新本测试（警告而非阻断开发：
世界生成结果变化本身是可接受的，测试锁的是"意外漂移"）。
"""

import hashlib
import struct

from ascend.space.continent import (
    ContinentGenerator,
    ContinentParams,
    serialize_continent,
)
from ascend.space.generator import WorldGenerator
from ascend.space.tile_gen import TileGenerator
from ascend.space.tile_grid import TileGrid

GOLDEN_SEED = 20260806
# 2026-08-06 首次固化；同日坐标单位统一（tile=格点 1:1）后重新固化一次
# 2026-08-07 缓存格式新增 land_ratio 字段后重新固化一次
# 2026-08-07 大陆轮廓层改为绝对频率（尺寸延伸而非缩放）后重新固化一次
# 2026-08-11 温度语义统一（海域=海面温度、直减率仅陆地、开阔海洋无雨影）后重新固化一次
# 2026-08-15 缓存头部新增生成环境指纹字段（格式版本归 1；世界数值不变）后重新固化一次
# 2026-08-18 黄金 hash 纳入真实 TileGenerator 输出后重新固化一次
GOLDEN_HASH = "c6f78751833b15a10b66b6357c9af3bebb7a56f867fd126b3684e79935b1f8cb"


def _pipeline_digest(seed: int) -> str:
    """固定 seed 的生成管线输出 sha256（大陆场 + 首块 + 真实 tile）。"""
    continent = ContinentGenerator(
        seed=seed,
        params=ContinentParams(width_km=6, height_km=4, sample_resolution=200),
    ).generate()
    gen = WorldGenerator(seed=seed)
    gen._continent = continent  # 注入小尺寸大陆（避免默认大尺寸 5-30s）
    chunk = gen.generate_chunk(0, 0)
    grid = TileGenerator(seed=seed, continent=continent).generate_chunk_for(chunk)

    h = hashlib.sha256()
    h.update(serialize_continent(continent))
    h.update(struct.pack(">ii", chunk.cx, chunk.cy))
    h.update(struct.pack(">ddd", chunk.mean_temp, chunk.annual_rainfall, chunk.altitude))
    h.update(struct.pack(">i", int(chunk.biome)))
    h.update(struct.pack(">i", int(chunk.climate_zone)))
    h.update(grid.to_bytes())
    return h.hexdigest()


def test_worldgen_golden_hash() -> None:
    """固定 seed 管线输出与黄金 hash 一致（同架构跨机确定性）。"""
    digest = _pipeline_digest(GOLDEN_SEED)
    assert digest == GOLDEN_HASH


def test_golden_hash_seed_sensitive() -> None:
    """不同 seed 输出不同摘要（黄金值不是恒量）。"""
    assert _pipeline_digest(GOLDEN_SEED) != _pipeline_digest(GOLDEN_SEED + 1)
