"""创建世界流程 — 步骤注册与参数汇总（Issue #8，纯逻辑 RefCounted）。

设计（Issue #8 要求可插拔 + 固定顺序）:
  - 步骤顺序 = build_steps() 数组顺序（依赖链固定：地图生成 →
    群落分布 → 物种分布），中间步骤按依赖插入即可
  - 步骤基类 SetupStep 提供统一契约，未来步骤实现后加入注册表
  - setup_params 汇总：{seed, gen_params} 最终传给 save_create

本类只负责注册与汇总；页面容器（world_setup.gd）驱动步骤流转。
"""

class_name SetupFlow
extends RefCounted


## 构建步骤链（有序，依赖在前的先执行）。
## 当前步骤：地图生成调参（seed + 大陆占比 + 地形预览）。
## 未来步骤（初始群落分布、物种分布等）在实现后追加到对应位置。
static func build_steps() -> Array:
	var steps: Array = []
	steps.append(MapSetupStep.new())
	return steps


## 步骤 ID 列表（顺序校验 / 日志）。
static func step_ids(steps: Array) -> Array:
	var ids: Array = []
	for step in steps:
		if step is SetupStep:
			ids.append(step.step_id())
	return ids


## 汇总全部步骤参数（按数组顺序逐个合并，后者覆盖前者）。
## gen_params 子字典按键合并（各步骤只产出自己的调参键，互不覆盖）。
## 返回 {seed, gen_params} 字典；空步骤产出不影响结果。
static func merge_params(steps: Array, base: Dictionary = {}) -> Dictionary:
	var merged: Dictionary = base.duplicate(true)
	for step in steps:
		if not step is SetupStep:
			continue
		var params: Dictionary = step.get_params()
		if params.has("gen_params") and params["gen_params"] is Dictionary:
			var inner: Dictionary = merged.get("gen_params", {})
			if not inner is Dictionary:
				inner = {}
			for key in params["gen_params"]:
				inner[key] = params["gen_params"][key]
			merged["gen_params"] = inner
		for key in params:
			if key != "gen_params":
				merged[key] = params[key]
	return merged
