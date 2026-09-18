"""世界模块包 — 子模块按需导入（不在此处聚合，避免导入环与启动开销）。

- ``primitives``：共享实例原语；``ids``：节点/参数 ID 单一事实源；
- ``pipeline``：世界生成 → 天气链阶段序；
- ``clock`` / ``toy``：P0 玩具模块；
- ``weather`` / ``worldgen`` / ``terrain``：生产切片模块（P1/P2）。

用法：``from ascend.world.modules import weather`` 或直接导入子模块。
"""
