"""波次执行器 — 按世界程序的波次计划求值机制实例（执行层基础件）。

- 串行模式按波次声明顺序逐任务求值；
- 并行模式下同波任务并发执行（ThreadPoolExecutor）——同一波内不存在
  同帧依赖（编译期静态校验保证），任务间无共享写入，结果必须与串行
  逐位一致（``tests/unit/test_wave_executor.py`` 锁定）；
- 边界节点（无机制写者的输入节点）取值由调用方 ``provide`` 提供，
  未提供即拒绝（fail-closed）；
- 当前仅支持单偏移、lag=0 的父依赖（wired 子集）；其余显式拒绝。

本模块不写世界状态、不发布记录；它是机制图的求解器。生产路径接入
见 docs/研究理论/世界基座/13-世界程序编译.md 分期。
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Sequence


def execute_waves(
    program,
    registry,
    *,
    frame: int,
    evaluate: Callable[..., object],
    provide: Callable[[str, tuple], object],
    instances: Sequence[tuple] = ((),),
    parallel: bool = False,
    workers: int | None = None,
    wired_only: bool = True,
) -> dict[tuple[str, tuple], object]:
    """按波次计划求值机制实例，返回 ``{(output, instance): value}``。

    Args:
        program: ``WorldProgram``（波次/内核/wired 标记）。
        registry: 机制注册表（``nodes`` / ``mechanisms``）。
        frame: 求值帧（tick）。
        evaluate: 求值入口 ``(output, parent_values, *, frame, instance)``
            （生产为 ``WeatherEngine.evaluate_node``，含干预覆盖/研究记录）。
        provide: 边界节点取值 ``(node_id, instance) -> value``。
        instances: 非全局节点的实例列表（如 [(cx, cy), ...]）。
        parallel: 同波并发求值（结果须与串行逐位一致）；调用方须保证
            ``evaluate`` 与 ``provide`` 并发安全。
        workers: 并行线程数（None = 同波任务数）。
        wired_only: 只求值 wired 节点（当前生产子集）。

    Returns:
        ``{(output, instance): value}``；instance 为全局节点时为 ``()``。

    Raises:
        NotImplementedError: 父依赖含多空间偏移或 lag≠0（尚未支持）。
        KeyError: 边界值未提供，或父值缺失（求值顺序/声明错误）。
    """
    values: dict[tuple[str, tuple], object] = {}
    executed: set[str] = set()
    pool = (
        ThreadPoolExecutor(max_workers=workers) if parallel else None
    )
    try:
        for wave in program.waves:
            tasks: list[tuple[object, str, tuple]] = []
            for output in wave.outputs:
                kernel = program.kernels[output]
                if wired_only and not kernel.wired:
                    continue
                executed.add(output)
                mechanism = registry.mechanisms[kernel.mechanism_id]
                node = registry.nodes[output]
                if node.instance_domain.axes:
                    for instance in instances:
                        tasks.append((mechanism, output, tuple(instance)))
                else:
                    tasks.append((mechanism, output, ()))
            if not tasks:
                continue
            if pool is not None:
                # 先全部提交再收集（否则逐个 result 会串行化同波任务）
                futures = [
                    pool.submit(
                        _run_task, program, registry, frame, evaluate,
                        provide, values, executed, task,
                    )
                    for task in tasks
                ]
                results = [future.result() for future in futures]
            else:
                results = [
                    _run_task(
                        program, registry, frame, evaluate, provide,
                        values, executed, task,
                    )
                    for task in tasks
                ]
            for (output, instance), value in results:
                values[(output, instance)] = value
    finally:
        if pool is not None:
            pool.shutdown()
    return values


def _run_task(
    program,
    registry,
    frame: int,
    evaluate: Callable[..., object],
    provide: Callable[[str, tuple], object],
    values: dict[tuple[str, tuple], object],
    executed: set[str],
    task: tuple[object, str, tuple],
) -> tuple[tuple[str, tuple], object]:
    """求值单个任务：解析父值 → 调用求值入口。

    父值来源：本波次计划内已求值节点（``executed``）取自值表；
    其余（未 wired 的声明节点/边界节点）由调用方 ``provide`` 提供
    （未提供即 KeyError，fail-closed）。
    """
    mechanism, output, instance = task
    parent_values: dict[str, object] = {}
    for parent in mechanism.parents:
        if parent.lag != 0 or len(parent.spatial_offsets) > 1:
            raise NotImplementedError(
                f"父依赖暂不支持（lag={parent.lag}, "
                f"偏移数={len(parent.spatial_offsets)}）: "
                f"{mechanism.mechanism_id} ← {parent.parent}"
            )
        parent_node = registry.nodes[parent.parent]
        parent_instance = () if not parent_node.instance_domain.axes else instance
        if parent.parent in executed:
            key = (parent.parent, parent_instance)
            if key not in values:
                raise KeyError(
                    f"父值缺失（求值顺序错误）: "
                    f"{parent.parent} {parent_instance}"
                )
            parent_values[parent.parent] = values[key]
        else:
            parent_values[parent.parent] = provide(
                parent.parent, parent_instance,
            )
    value = evaluate(
        output, parent_values, frame=frame, instance=instance,
    )
    return (output, instance), value
