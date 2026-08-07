"""创建世界流程的步骤基类（Issue #8）— 纯逻辑 RefCounted，可单测。

步骤契约（由 SetupFlow / world_setup 容器驱动）:
  - step_id(): 步骤唯一 ID（注册/顺序校验）
  - title(): 页面标题
  - setup(params): 进入步骤时调用；params = 之前步骤已合并的
      生成参数（只读视图，供依赖步骤读取，如群落分布读地图参数）
  - get_params(): 本步骤产出，离开时由容器合并进 setup_params
  - validate(): 校验本步骤输入，"" = 通过，否则返回错误文本
  - draw_page(canvas, rect, font): 绘制步骤内容区
  - handle_input(event, rect) -> bool: 步骤内输入，true = 已消费
      （容器随后整体重绘）
  - handle_release(event) -> bool: 拖拽释放（鼠标抬起），true = 已消费
  - on_escape() -> bool: Esc 按键；true = 已消费
  - on_preview_response(payload): map_preview 响应（容器按 request_type
      路由到当前步骤）
  - on_preview_failed(): map_preview 请求失败
  - on_seed_submitted(text): 容器种子输入框提交回调

参数模型（setup_params）:
    {seed: int, gen_params: {land_ratio: float, ...}}

依赖步骤通过 setup() 收到的 params 读取前置产出；顺序由
SetupFlow.build_steps() 的数组顺序固定（地图 → 群落 → 物种），
新步骤按依赖插入对应位置即可，无需改动容器。
"""

class_name SetupStep
extends RefCounted


## 步骤唯一 ID（空 = 未实现，容器跳过并告警）。
func step_id() -> String:
	return ""


## 步骤标题（页面标题显示）。
func title() -> String:
	return ""


## 进入步骤：恢复/读取共享参数。
func setup(_params: Dictionary) -> void:
	pass


## 本步骤产出参数（容器合并进 setup_params）。
func get_params() -> Dictionary:
	return {}


## 校验当前输入；"" = 通过，否则返回错误文本（「下一步」时拦截）。
func validate() -> String:
	return ""


## 绘制步骤内容区（自绘风格，与主菜单/存档页一致）。
func draw_page(_canvas: Control, _rect: Rect2, _font: Font) -> void:
	pass


## 步骤内输入分发（鼠标移动/点击）；返回 true = 已消费。
func handle_input(_event: InputEvent, _rect: Rect2) -> bool:
	return false


## 拖拽释放（容器在鼠标抬起时调用）；返回 true = 已消费。
func handle_release(_event: InputEvent) -> bool:
	return false


## Esc 按键（容器在按下时调用）；返回 true = 已消费。
func on_escape() -> bool:
	return false


## map_preview 响应（容器按 request_type 路由到当前步骤）。
func on_preview_response(_payload: Dictionary) -> void:
	pass


## map_preview 请求失败。
func on_preview_failed() -> void:
	pass


## 容器种子输入框提交回调。
func on_seed_submitted(_text: String) -> void:
	pass
