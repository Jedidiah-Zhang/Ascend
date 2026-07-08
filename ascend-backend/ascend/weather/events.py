"""天气事件 schema 注册 — per-parameter 事件（什么变了发什么）。

7 个事件：
  - temperature_change：温度变化超阈值（含 season 元数据）
  - humidity_change：湿度变化超阈值
  - wind_change：风速变化超阈值
  - sunshine_change：日照时长变化超阈值（随季节+纬度浮动）
  - precipitation_start：降水开始（RainSchedule 事件进入区间），含 precip_type
  - precipitation_stop：降水停止（RainSchedule 事件结束）
  - season_change / sunrise / sunset：全局季节切换 / per-chunk 昼夜切换

导入此模块即向 world_tree 单例注册。WeatherEngine 构造时也会在注入实例注册。
"""

from ascend.world_tree import world_tree


def register_weather_schemas(wt) -> None:
    """在指定 WorldTree 实例上注册天气事件 schema。"""
    wt.register_event_schema(
        "temperature_change",
        required={"temperature": float, "season": int, "time_of_day": int},
        description="温度变化超阈值时发布（解析算，每刻连续）。season 为当前季节索引。",
    )
    wt.register_event_schema(
        "humidity_change",
        required={"humidity": float, "time_of_day": int},
        description="湿度变化超阈值时发布。",
    )
    wt.register_event_schema(
        "wind_change",
        required={"wind_speed": float, "wind_dir_x": float, "wind_dir_y": float,
                  "time_of_day": int},
        description="风速变化超阈值时发布。wind_dir_* 为全局风向单位向量。",
    )
    wt.register_event_schema(
        "sunshine_change",
        required={"sunshine": float, "season": int, "time_of_day": int},
        description="日照时长变化超阈值时发布（解析算，随季节+纬度浮动）。"
                    "sunshine 为当日日照时长（小时/天），season 为当前季节索引。",
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


# 单例注册（生产环境用 world_tree 单例时生效）
register_weather_schemas(world_tree)
