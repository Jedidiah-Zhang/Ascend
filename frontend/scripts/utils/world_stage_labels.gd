"""世界生成阶段 → UI 文案映射（单源，Issue #8）。

后端 ContinentGenerator.STAGE_*（elevation/climate/erosion/water/
width/done）+ "chunks" 出生区阶段的统一翻译表；world_loading.gd
与 main_world.gd 共同引用，新增阶段文案只改此处。
"""

class_name WorldStageLabels
extends RefCounted


## 阶段 → 翻译键（文案见 lang/*.json 的 ui.stage.*）
const LABEL_KEYS: Dictionary = {
	"elevation": "ui.stage.elevation",
	"climate": "ui.stage.climate",
	"erosion": "ui.stage.erosion",
	"water": "ui.stage.water",
	"width": "ui.stage.width",
	"chunks": "ui.stage.chunks",
	"done": "ui.stage.done",
}

## 阶段顺序（与后端广播顺序一致：ContinentGenerator.generate 的
## STAGE_* 逐阶段 + game.py 的 "chunks" 出生区阶段，随后 world_initialized）：
## 进度条按此推进刻度；缓存命中时只广播 done（读档秒开）。
const ORDER: Array = [
	"elevation", "climate", "erosion", "water", "width", "done", "chunks",
]


## 阶段名 → 文案；未知阶段用兜底文案。
static func label_for(stage: String) -> String:
	return TranslationServer.tr(str(LABEL_KEYS.get(stage, "ui.stage.fallback")))


## 阶段在 ORDER 中的索引；未知阶段返回 -1（不推进进度刻度）。
static func index_of(stage: String) -> int:
	return ORDER.find(stage)
