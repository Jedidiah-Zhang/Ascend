"""Loom of Fate — 命运织机与命运流。

设计文档: docs/世界框架/随机系统/设计.md

职责边界（三层分离）:
  - Fate Loom 只提供**外生随机性 U**（不可控/外生的随机因素）。
  - 世界机制 F（状态如何演化）与代理策略 π（行动如何选择）是
    消费 U 的调用方，不归本模块管。
  - 织机不决定命运，只生成命运中的"偶然性"。

核心性质:
  - 无状态派生：流取值 = H(world_seed, 身份)，与消费顺序无关。
  - 顺序无关：任何 do 干预改变执行路径，都不会搅动未干预流。
  - 线程安全：FateStream 每次调用新建，无共享可变状态。
  - 存档零持久化：同 seed 可完全重算任意流。
"""

import _random
import random

from .derive import MASK_256, derive

__all__ = ["LoomOfFate", "FateStream", "format_fate_path"]


def format_fate_path(identity: tuple) -> str:
    """身份元组 → 人类可读诊断字符串。

    str 分量以 "/" 连接；末尾 int 分量（约定为 tick）渲染为 "@tick"。
    中间 int 分量（如块坐标）内联为字符串。

    Args:
        identity: 流身份元组（LoomOfFate.stream 产出）。

    Returns:
        如 "npc/42/decision@3912"。诊断用途，不承诺可解析回身份。
    """
    parts = [str(p) for p in identity]
    if identity and isinstance(identity[-1], int):
        return "/".join(parts[:-1]) + "@" + str(identity[-1])
    return "/".join(parts)


class FateStream(random.Random):
    """命运流 — 携带身份的确定性随机流。

    与普通 random.Random 的区别:
      - 身份派生：seed 由 (world_seed, identity) 派生，任何人可重算。
      - 禁重播种：seed() 覆写为 RuntimeError——重播种即破坏可复现性。
      - fate_path：身份的可读字符串（调试/回放/事件溯源用）。
      - fork()：以自身身份继续派生子流（实体级子域）。

    跨平台位级一致约束：黄金 hash 承诺路径只允许 MT-safe 方法
    （random/randint/randrange/uniform 为纯整数/乘法运算）；
    禁 gauss/normalvariate/expovariate 等走 math 超越函数的方法。
    """

    __slots__ = ("identity", "_root", "fate_path")

    def __init__(self, root: int, identity: tuple) -> None:
        """从世界根种子与身份构造流。

        Args:
            root: 世界根种子（掩码 256-bit）。
            identity: 完整身份元组（含 path/entity/purpose/tick）。
        """
        self._root = root & MASK_256
        self.identity = identity
        self.fate_path = format_fate_path(identity)
        # 绕过 random.Random.__init__ 的 self.seed(x) 分派（会命中禁止重播种
        # 覆写），直接以 C 级实现播种——FateStream 的确定性由此保证。
        _random.Random.__init__(self)
        _random.Random.seed(self, derive(self._root, *identity))

    def seed(self, *args, **kwargs) -> None:
        """禁止重播种（可复现性由身份派生保证）。"""
        raise RuntimeError(
            "FateStream 不允许重播种——随机性由 (world_seed, identity) 派生"
        )

    def fork(self, *parts: str | int) -> "FateStream":
        """以自身身份继续派生新流（追加身份分量）。

        Args:
            parts: 追加的身份分量（如子 purpose）。

        Returns:
            新 FateStream，身份 = 自身身份 + parts。
        """
        return FateStream(self._root, self.identity + tuple(parts))


class LoomOfFate:
    """命运织机 — 世界级确定性随机源。

    用法:
        loom = LoomOfFate(world_seed)
        stream = loom.stream(entity_id="42", purpose="decision", tick=1000)
        child = loom.domain("environment").domain("weather")
        seed = loom.derive("world", "birth_point")

    子织机 path 累积：``domain("a").stream("b")`` 与
    ``stream("a", "b")`` 等价（身份拼接一致，有测试锁定）。
    """

    __slots__ = ("_root", "_path")

    def __init__(self, world_seed: int, path: tuple[str, ...] = ()) -> None:
        """初始化织机。

        Args:
            world_seed: 世界种子（manifest.seed，256-bit 空间）。
            path: 子织机路径（由 domain() 累积，通常不直接传）。
        """
        self._root = world_seed & MASK_256
        self._path = path

    def derive(self, *parts: str | int) -> int:
        """在织机作用域内派生种子（含 path 累积）。

        Args:
            parts: 身份分量。

        Returns:
            256-bit 派生种子。
        """
        return derive(self._root, *self._path, *parts)

    def domain(self, *names: str) -> "LoomOfFate":
        """创建子织机（namespace 作用域，path 累积）。

        Args:
            names: 子域名称（如 "environment"、"weather"）。

        Returns:
            新 LoomOfFate 实例，path = 自身 path + names。
        """
        for name in names:
            if not isinstance(name, str):
                raise TypeError(f"domain 名必须为 str，实际 {type(name).__name__}")
        return LoomOfFate(self._root, self._path + tuple(names))

    def stream(
        self,
        *parts: str | int,
        entity_id: str | None = None,
        purpose: str | None = None,
        tick: int | None = None,
        **extra: str | int,
    ) -> FateStream:
        """获取确定性随机流（独立于其他一切流）。

        身份规范（构造顺序固定）:
            path + parts + sorted(extra) 键值对 + entity_id + purpose + tick

        Args:
            parts: 位置身份分量（域路径，如 "feature", "block", 3, -2）。
            entity_id: 实体标识（如 NPC ID）。
            purpose: 用途（如 "decision" / "personality"）。
            tick: 世界时间（tick）。入身份 → 原子 do 天然成立：
                同节点不同 tick 是不同流，单 tick 干预不影响其余。
            extra: 附加键值分量（按 key 排序，防字典序依赖）。

        Returns:
            FateStream：同身份同序列（无视求值序），异身份统计独立。
        """
        comps: list = list(self._path)
        comps.extend(parts)
        for key in sorted(extra):
            comps.append(key)
            comps.append(extra[key])
        if entity_id is not None:
            comps.append(entity_id)
        if purpose is not None:
            comps.append(purpose)
        if tick is not None:
            comps.append(tick)
        return FateStream(self._root, tuple(comps))