"""距水距离场 — 层1 宏观场的"到最近水体距离"（issue #42）。

对每个宏观格，计算到最近水体（海/河/湖）的平面距离（米），
存为与 elevation_field 同分辨率同索引的场。材质分布（沙滩/冲积/
湿地）、生态/湿度/动物水源寻路/聚落选址共用此接口。

多源 BFS（4 邻域）：所有水体格为源（距离 0），向陆地逐层扩散，
距离 = 步数 × cell_size。4 邻域沿海岸线走，不斜穿窄陆地（对角
不被当作直达），度量朴素、带宽参数按视觉标定即可。

C 加速：复用 _hydrology.so 的 hydrology_water_distance（同一动态库，
后端本就必需，无新增编译依赖）。纯 Python 参考实现仅存于测试中
作正确性 oracle（C 输出与之一致，见 tests/unit/test_water_distance.py）。
"""

import ctypes
from array import array
from pathlib import Path

from ._cext import load_c_extension

_HERE = Path(__file__).resolve().parent
_HYDRO = load_c_extension(
    str(_HERE / "_hydrology.c"), str(_HERE / "_hydrology.so"), link_flags=["-lm"],
)

_HYDRO.hydrology_water_distance.argtypes = [
    ctypes.POINTER(ctypes.c_uint8),   # water_mask
    ctypes.c_int, ctypes.c_int,       # w, h
    ctypes.c_double,                  # cell_size (m)
    ctypes.POINTER(ctypes.c_double),  # dist_out (m)
]
_HYDRO.hydrology_water_distance.restype = None

__all__ = ["compute_water_distance"]


def compute_water_distance(
    water_mask,  # sequence[bool|int]: True = 水体格（源）
    width: int,
    height: int,
    cell_size: float,
) -> array:
    """多源 BFS 计算每个格到最近水体的距离 (m)。

    Args:
        water_mask: 行优先序列，长度 == width × height，True = 水体
            （源，距离 0）。空图返回全 0。
        width: 网格宽（格）。
        height: 网格高（格）。
        cell_size: 每格对应世界距离 (m)。

    Returns:
        array('d')，行优先，长度 == width × height；水体格 = 0，
        陆格 = 到最近水体的 BFS 步数 × cell_size。

    Raises:
        ValueError: water_mask 长度不符，或无任何水体源（全陆——
            "距水"无定义，调用方须保证至少一个源）。
    """
    n = width * height
    if n == 0:
        return array("d")
    if len(water_mask) != n:
        raise ValueError(
            f"water_mask 长度需为 {n}（{width}×{height}），实际为 {len(water_mask)}"
        )
    # 无水体源（全陆）时"距水"无定义：fail fast 而非返回全 0——
    # 全 0 会与"水体本身=0"混淆，消费方无法区分。大陆生成保证有海。
    if not any(water_mask):
        raise ValueError("water_mask 无水体源（全陆）：距水距离无定义")
    # bool 序列 → uint8 缓冲：array('B') 比逐元素 unpack 快 ~10x，
    # from_buffer 零拷贝传给 C（BFS 本身毫秒级，包装层不再成为瓶颈）。
    mask_buf = (ctypes.c_uint8 * n).from_buffer(array("B", water_mask))
    dist = array("d", [0.0]) * n
    dist_ptr = (ctypes.c_double * n).from_buffer(dist)
    _HYDRO.hydrology_water_distance(
        mask_buf, width, height, float(cell_size), dist_ptr,
    )
    return dist
