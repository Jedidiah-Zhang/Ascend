"""天气事件 schema 注册 — 感知层事件 + 离散事件。

事件类型：
  - temperature_change / humidity_change / wind_change / sunshine_change：
       感知类别变化时发布（如 "cold"→"cool"、"dry"→"comfortable"），
       附带 numeric 值和 perception 标签。
  - precipitation_start / precipitation_stop：降雨事件切换
  - cold_snap_start / cold_snap_stop：寒潮事件切换
  - heat_wave_start / heat_wave_stop：热浪事件切换
  - storm_start / storm_stop：暴风雨事件切换
  - season_change / sunrise / sunset：全局季节 / per-chunk 昼夜

导入此模块即向 world_tree 单例注册。WeatherEngine 构造时也会在注入实例注册。
"""

from ascend.world_tree import world_tree
from .weather_modifier import WEATHER_MODIFIERS


def register_weather_schemas(wt) -> None:
    """在指定 WorldTree 实例上注册天气事件 schema。"""
    wt.register_event_schema(
        "temperature_change",
        required={"temperature": float, "perception": str, "season": int,
                  "time_of_day": int},
        description="温度感知类别变化时发布。perception 为感知标签（cold/cool/comfortable 等）。",
    )
    wt.register_event_schema(
        "humidity_change",
        required={"humidity": float, "perception": str, "time_of_day": int},
        description="湿度感知类别变化时发布。perception 为感知标签（dry/comfortable/humid 等）。",
    )
    wt.register_event_schema(
        "wind_change",
        required={"wind_speed": float, "perception": str,
                  "wind_dir_x": float, "wind_dir_y": float, "time_of_day": int},
        description="风速感知类别变化时发布。perception 为感知标签（calm/breezy/windy 等）。",
    )
    wt.register_event_schema(
        "sunshine_change",
        required={"sunshine": float, "perception": str, "season": int,
                  "time_of_day": int},
        description="日照时长感知类别变化时发布。perception 为时长分级标签"
                    "（very_short/short/moderate/long/very_long/extreme）。",
    )
    wt.register_event_schema(
        "precipitation_start",
        required={"precip_type": str, "intensity": float, "time_of_day": int},
        description="降水开始时发布。precip_type: rain|snow，由当前温度判定。",
    )
    wt.register_event_schema(
        "precipitation_stop",
        required={"time_of_day": int},
        description="降水停止时发布（RainSchedule 事件结束）。",
    )
    wt.register_event_schema(
        "season_change",
        required={"season": int, "time_of_day": int},
        description="季节切换时发布（全局事件，location=(0,0)）。season 0=春 1=夏 2=秋 3=冬。",
    )
    wt.register_event_schema(
        "sunrise",
        required={"time_of_day": int, "daylight_hours": float},
        description="日出时发布（per-chunk，用 chunk 纬度算昼夜切换）。"
                    "daylight_hours 为当日天文日照时长（小时/天），供下游种植/生理系统使用。",
    )
    wt.register_event_schema(
        "sunset",
        required={"time_of_day": int, "daylight_hours": float},
        description="日落时发布（per-chunk，用 chunk 纬度算昼夜切换）。"
                    "daylight_hours 为当日天文日照时长（小时/天）。",
    )
    # 天气修改器事件 schema（从 WEATHER_MODIFIERS 注册表自动生成）
    for config in WEATHER_MODIFIERS.values():
        wt.register_event_schema(
            f"{config.type_name}_start",
            required=config.start_schema,
            description=f"{config.type_name} 开始时发布。",
        )
        wt.register_event_schema(
            f"{config.type_name}_stop",
            required={"time_of_day": int},
            description=f"{config.type_name} 停止时发布。",
        )


# 单例注册（生产环境用 world_tree 单例时生效）
register_weather_schemas(world_tree)
