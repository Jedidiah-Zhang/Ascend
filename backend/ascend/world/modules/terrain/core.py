"""地形核心适配器 — 新核心 field 机制（P2-2b 生产切换）。

旧 ``space/tile_state.state_evolve`` 的语义：给定 chunk 内状态/地形/坡度
数组与逐步降水/温度/步长，原地更新状态数组。本适配器用新核心的
``terrain.integrate``（field 机制，chunk 动态实例）实现同一语义：

- 每一步构造一次无状态求值：物化 chunk、装载当前状态场、注入地形/坡度/
  遮蔽与降水/温度/步长、推进一帧、读回三状态场；
- 多步（日结算采样）按步序贯求值——与旧内核的逐步循环同序同式；
- 内核（参考实现 / C 加速）逐位对拍由模块内核对锁定，适配器只做数据搬运。
"""

from __future__ import annotations

from typing import Mapping, Sequence

from ascend.world.compile import compile_world
from ascend.world.meta.declarations import Schedule, WorldSpec
from ascend.world.runtime import LatticeField, WorldProcess

from . import kernel, module

__all__ = ["TerrainCore"]

_CHUNK = "lattice.chunk"
_COORDS = (0, 0)


class TerrainCore:
    """按 chunk 数组求值地形演化（无状态；状态由调用方装载/取回）。"""

    def __init__(self, program: object | None = None) -> None:
        self._program = program or compile_world(
            WorldSpec(
                modules=(module.MODULE,),
                schedule=Schedule(periods=(("hour", 1),)),
            )
        )

    @property
    def program(self) -> object:
        return self._program

    def evolve(
        self,
        states: Mapping[str, Sequence[int]],
        terrain: Sequence[int],
        slope: Sequence[float],
        *,
        precip: Sequence[Sequence[float]],
        temp: Sequence[float],
        dt: float = 1.0,
        cover: Sequence[float] | None = None,
    ) -> dict[str, LatticeField]:
        """求值后返回新状态场（不修改入参）。"""
        n_steps = len(temp)
        if n_steps < 1:
            return {
                key: _as_field(states[key]) for key in kernel.STATE_KEYS
            }
        fields = {key: _as_field(states[key]) for key in kernel.STATE_KEYS}
        for step in range(n_steps):
            process = WorldProcess(self._program)
            process.materialize(_CHUNK, _COORDS)
            for key in kernel.STATE_KEYS:
                process.seed_at(f"terrain.{key}", _COORDS, fields[key])
            process.step(
                inputs={
                    "terrain.terrain_id": {_COORDS: list(terrain)},
                    "terrain.slope": {
                        _COORDS: [float(value) for value in slope]
                    },
                    "terrain.cover": {
                        _COORDS: (
                            [1.0] * len(terrain)
                            if cover is None
                            else list(cover)
                        )
                    },
                    "weather.precip_moisture": precip[0][step],
                    "weather.precip_snow": precip[1][step],
                    "weather.step_temp": temp[step],
                    "terrain.dt": dt,
                }
            )
            for key in kernel.STATE_KEYS:
                fields[key] = process.committed(
                    f"terrain.{key}"
                ).get(_COORDS)
        return fields

    def evolve_into(
        self,
        states: Mapping[str, Sequence[int]],
        terrain: Sequence[int],
        slope: Sequence[float],
        *,
        precip: Sequence[Sequence[float]],
        temp: Sequence[float],
        dt: float = 1.0,
        cover: Sequence[float] | None = None,
    ) -> None:
        """与旧 ``state_evolve`` 同语义：原地回写 ``states`` 数组。

        生产直调内核绑定（零拷贝，与机制声明同一 C 内核；逐位一致由
        内核对锁定）。``states``/``terrain``/``slope`` 须为可写缓冲
        （``array('B')``/``array('H')``/``array('f')``）。
        """
        kernel.evolve_accelerated_into(
            states, terrain, slope,
            precip=precip, temp=temp, dt=dt, cover=cover,
        )


def _as_field(values: Sequence[int]) -> LatticeField:
    """序列 → 一维场（适配器输入形状：旧内核为扁平数组）。"""
    return LatticeField.from_values(values)
