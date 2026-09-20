"""世界模块包 — 子模块按需导入（不在此处聚合，避免导入环与启动开销）。

- ``primitives``：共享实例原语；``ids``：节点/参数 ID 单一事实源；
- ``pipeline``：世界生成 → 天气链阶段序；
- ``clock`` / ``toy``：P0 玩具模块；
- ``weather`` / ``worldgen`` / ``terrain``：生产切片模块（P1/P2）；
- ``conservation``：守恒练兵切片（P4-2，流量/守恒不变量/多分辨率）；
- ``harvest``：实体练兵切片（P4-3，实体/事件/资源/Γ）。

用法：``from ascend.world.modules import weather`` 或直接导入子模块。
"""
