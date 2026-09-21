"""世界模块包 — 子模块按需导入（不在此处聚合）。

- ``primitives``：共享实例原语；``ids``：节点/参数 ID 单一事实源；
- ``pipeline``：世界生成 → 天气链阶段序；
- ``clock`` / ``toy``：验收玩具模块；
- ``weather`` / ``worldgen`` / ``terrain``：生产切片模块；
- ``conservation``：守恒演练模块（流量/守恒不变量/多分辨率）；
- ``harvest``：实体演练模块（实体/事件/资源/Γ）。

用法：``from olam.modules import weather`` 或直接导入子模块。
"""
