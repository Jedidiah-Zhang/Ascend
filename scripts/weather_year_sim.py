"""天气系统年模拟 — 使用真实后端流程 + tick 加速。

用法:
    cd ascend-backend && PYTHONPATH=. ../.venv/bin/python ../scripts/weather_year_sim.py [--seed=42] [--fraction=1.0]
"""

import argparse
import random
import time as _real_time
from collections import defaultdict

from ascend.time import WorldClock, GameCalendar
from ascend.config import GAME_HOUR, GAME_DAY, GAME_YEAR
from ascend.world_tree import world_tree, Event, AffectedParty
from ascend.space import WorldGenerator, TileGenerator
from ascend.weather import WeatherEngine
from ascend.game import GameEngine, INITIAL_CHUNK_RADIUS


def run(seed: int, fraction: float) -> dict:
    """完整后端初始化 + tick 加速模拟。

    Returns:
        统计结果字典。
    """
    if seed == 0:
        seed = random.randint(1, 2**31 - 1)
    print(f"天气系统年模拟  seed={seed}")
    print()

    # ── 1. 世界生成 ──────────────────────────────────
    print("── 生成大陆...", end=" ", flush=True)
    t0 = _real_time.monotonic()
    world_gen = WorldGenerator(seed=seed)
    continent = world_gen.ensure_continent()
    tile_gen = TileGenerator(seed=seed, continent=continent)
    print(f"{_real_time.monotonic() - t0:.1f}s  {continent}")

    # ── 2. 出生点 + 初始 chunk ──────────────────────
    birth = GameEngine._select_birth_point(continent)
    print(f"── 出生点: chunk {birth}")

    from concurrent.futures import ThreadPoolExecutor, as_completed

    r = INITIAL_CHUNK_RADIUS
    coords = [
        (birth[0] + dx, birth[1] + dy)
        for dy in range(-r, r + 1) for dx in range(-r, r + 1)
    ]
    chunks = world_gen.generate_parallel(coords, max_workers=4)

    def _build_tiles(chunk):
        grid = tile_gen.generate_chunk_for(chunk)
        chunk.generate_tiles(grid)
        return chunk

    loaded = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_build_tiles, c): (c.cx, c.cy) for c in chunks}
        for future in as_completed(futures):
            chunk = future.result()
            loaded[(chunk.cx, chunk.cy)] = chunk
    print(f"── 初始 chunk: {len(loaded)} 个")

    climate_counts: dict[str, int] = defaultdict(int)
    for chunk in loaded.values():
        climate_counts[chunk.climate_zone.label] += 1
    print(f"── 气候分布: {dict(climate_counts)}")

    # ── 3. 时钟 + 日历 + 天气引擎 ────────────────────
    clock = WorldClock()
    cal = GameCalendar(clock=clock)
    weather = WeatherEngine(clock, seed=seed, world_tree_arg=world_tree)
    for (cx, cy), chunk in loaded.items():
        weather.register_chunk(cx, cy, chunk.annual_baseline, chunk.climate_zone, chunk.sea_level_temp)
    print(f"── 天气引擎接入 {len(loaded)} chunk")

    # 启用世界树内存裁剪（避免长模拟 OOM）
    world_tree.configure(max_memory_events=100_000)

    # ── 4. 实时统计（O(1) 内存，不囤积事件）───────────
    event_types = [
        "temperature_change", "humidity_change", "wind_change", "sunshine_change",
        "precipitation_start", "precipitation_stop",
        "cold_snap_start", "cold_snap_stop",
        "heat_wave_start", "heat_wave_stop",
        "storm_start", "storm_stop",
        "season_change", "sunrise", "sunset",
    ]
    counts: dict[str, int] = {t: 0 for t in event_types}
    # 温度
    temp_min = float("inf"); temp_max = float("-inf"); temp_sum = 0.0
    # 日照
    sun_min = float("inf"); sun_max = float("-inf"); sun_sum = 0.0
    # 日出日落
    sr_min = 999; sr_max = 0; ss_min = 999; ss_max = 0
    dl_min = float("inf"); dl_max = float("-inf")
    # 降水
    snow_count = 0
    # 季节
    season_changes: list[tuple[int, int, int]] = []
    # 温度按 chunk
    temp_by_chunk: dict[tuple, list[float]] = defaultdict(
        lambda: [float("inf"), float("-inf")]  # [min, max]
    )

    def _on_temp(e):
        counts["temperature_change"] += 1
        v = e.data["temperature"]
        nonlocal temp_min, temp_max, temp_sum
        if v < temp_min: temp_min = v
        if v > temp_max: temp_max = v
        temp_sum += v
        key = e.location[:2]
        tbc = temp_by_chunk[key]
        if v < tbc[0]: tbc[0] = v
        if v > tbc[1]: tbc[1] = v

    def _on_humidity(e):
        counts["humidity_change"] += 1

    def _on_wind(e):
        counts["wind_change"] += 1

    def _on_sunshine(e):
        counts["sunshine_change"] += 1
        v = e.data["sunshine"]
        nonlocal sun_min, sun_max, sun_sum
        if v < sun_min: sun_min = v
        if v > sun_max: sun_max = v
        sun_sum += v

    def _on_precip_start(e):
        counts["precipitation_start"] += 1
        nonlocal snow_count
        if e.data.get("precip_type") == "snow":
            snow_count += 1

    def _on_precip_stop(e):
        counts["precipitation_stop"] += 1

    def _on_season_change(e):
        counts["season_change"] += 1
        season_changes.append((
            e.data["season"],
            e.timestamp // GAME_DAY + 1,
            e.data["time_of_day"] // GAME_HOUR,
        ))

    def _on_sunrise(e):
        counts["sunrise"] += 1
        tod = e.data["time_of_day"] // GAME_HOUR
        dl = e.data["daylight_hours"]
        nonlocal sr_min, sr_max, dl_min, dl_max
        if tod < sr_min: sr_min = tod
        if tod > sr_max: sr_max = tod
        if dl < dl_min: dl_min = dl
        if dl > dl_max: dl_max = dl

    def _on_sunset(e):
        counts["sunset"] += 1
        tod = e.data["time_of_day"] // GAME_HOUR
        nonlocal ss_min, ss_max
        if tod < ss_min: ss_min = tod
        if tod > ss_max: ss_max = tod

    def _on_modifier(e):
        counts[e.event_type] += 1

    # 注册回调
    _callbacks = {
        "temperature_change": _on_temp,
        "humidity_change": _on_humidity,
        "wind_change": _on_wind,
        "sunshine_change": _on_sunshine,
        "precipitation_start": _on_precip_start,
        "precipitation_stop": _on_precip_stop,
        "season_change": _on_season_change,
        "sunrise": _on_sunrise,
        "sunset": _on_sunset,
    }
    for ev_type, cb in _callbacks.items():
        world_tree.subscribe(ev_type, cb)
    for et in ("cold_snap_start", "cold_snap_stop",
               "heat_wave_start", "heat_wave_stop",
               "storm_start", "storm_stop"):
        world_tree.subscribe(et, _on_modifier)

    # ── 5. Tick 加速 ─────────────────────────────────
    target_ticks = int(GAME_YEAR * fraction)
    print(f"\n── 加速模拟 {target_ticks * 100 // GAME_YEAR}% 年 "
          f"({target_ticks:,} ticks)...", flush=True)

    t0 = _real_time.monotonic()
    tick_span = max(target_ticks // 10, 1)

    for t in range(1, target_ticks + 1):
        clock.tick()  # → Calendar._check_boundaries → minute_change → WeatherEngine

        if t % tick_span == 0:
            pct = t * 100 // target_ticks
            elapsed = _real_time.monotonic() - t0
            tps = t / elapsed if elapsed > 0 else 0
            remaining = (target_ticks - t) / tps if tps > 0 else 0
            game_days = t / GAME_DAY
            print(f"  {pct:>3}%  {game_days:.0f}d  {tps:,.0f} t/s  "
                  f"剩余 ~{remaining:.0f}s", flush=True)

    elapsed = _real_time.monotonic() - t0
    tps = target_ticks / elapsed if elapsed > 0 else 0
    print(f"  完成  {tps:,.0f} t/s  {elapsed:.0f}s")

    weather.shutdown()
    cal.shutdown()

    # ── 6. 汇总 ─────────────────────────────────────
    precip_starts = counts["precipitation_start"]
    precip_stops = counts["precipitation_stop"]

    return {
        "seed": seed, "birth": birth, "num_chunks": len(loaded),
        "climate_counts": dict(climate_counts), "counts": counts,
        "season_changes": season_changes,
        "precip_starts": precip_starts, "precip_stops": precip_stops,
        "snow_count": snow_count,
        "temp_min": temp_min, "temp_max": temp_max,
        "temp_sum": temp_sum, "temp_count": counts["temperature_change"],
        "sun_min": sun_min, "sun_max": sun_max,
        "sun_sum": sun_sum, "sun_count": counts["sunshine_change"],
        "sr_min": sr_min, "sr_max": sr_max,
        "ss_min": ss_min, "ss_max": ss_max,
        "dl_min": dl_min, "dl_max": dl_max,
        "temp_by_chunk": {k: tuple(v) for k, v in temp_by_chunk.items()},
        "elapsed": elapsed, "total_ticks": target_ticks,
    }


def print_report(r: dict) -> None:
    print()
    print("=" * 64)
    print(f"  天气系统年模拟报告  seed={r['seed']}")
    print("=" * 64)
    print(f"  出生点: chunk {r['birth']}")
    print(f"  周边 chunk: {r['num_chunks']} 个")
    print(f"  气候分布: {r['climate_counts']}")
    print(f"  模拟 tick: {r['total_ticks']:,}  耗时: {r['elapsed']:.0f}s  "
          f"({r['total_ticks']/max(r['elapsed'],0.1):,.0f} t/s)")

    counts = r["counts"]
    print(f"\n── WorldTree 事件统计 ──")
    total = sum(counts.values())
    for ev_type, n in sorted(counts.items(), key=lambda x: -x[1]):
        bar = "█" * min(40, n * 40 // max(1, max(counts.values())))
        print(f"  {ev_type:<24} {n:>8}  {bar}")
    print(f"  {'(合计)':<24} {total:>8}")

    sc = r["season_changes"]
    from ascend.weather.season import Season
    print(f"\n── 季节切换 ({len(sc)} 次) ──")
    for s, day, hour in sc:
        print(f"    day {day:>4}  {hour:>2}:00  →  {Season(s).name}")

    print(f"\n── 温度 ──")
    tc = r["temp_count"]
    if tc:
        print(f"  事件数: {tc}  min={r['temp_min']:.0f}°C  max={r['temp_max']:.0f}°C  "
              f"均值={r['temp_sum']/tc:.1f}°C")
    print(f"── 每 chunk 温度范围 ──")
    for (cx, cy), (tmin, tmax) in sorted(r["temp_by_chunk"].items()):
        print(f"  chunk({cx},{cy}): {tmin:.0f}~{tmax:.0f}°C")

    print(f"\n── 日出/日落 ──")
    sr_cnt = r['counts'].get('sunrise', 0)
    ss_cnt = r['counts'].get('sunset', 0)
    print(f"  sunrise: {sr_cnt}次  {r['sr_min']}:00~{r['sr_max']}:00")
    print(f"  sunset:  {ss_cnt}次  {r['ss_min']}:00~{r['ss_max']}:00")
    print(f"  daylight_hours: {r['dl_min']:.1f}~{r['dl_max']:.1f}h")

    print(f"\n── 日照参数 (sunshine_change) ──")
    scnt = r["sun_count"]
    if scnt:
        print(f"  事件数: {scnt}  min={r['sun_min']:.2f}h  max={r['sun_max']:.2f}h  "
              f"均值={r['sun_sum']/scnt:.2f}h")
    else:
        print(f"  事件数: 0")

    print(f"\n── 降水 ──")
    print(f"  start: {r['precip_starts']}  stop: {r['precip_stops']}  "
          f"(diff={abs(r['precip_starts'] - r['precip_stops'])})")
    print(f"  雪事件: {r['snow_count']}")

    print(f"\n── 极端天气 ──")
    for et in ("cold_snap", "heat_wave", "storm"):
        starts = counts.get(f"{et}_start", 0)
        stops = counts.get(f"{et}_stop", 0)
        print(f"  {et}: start={starts}  stop={stops}  "
              f"({'✓' if starts == stops else '⚠ diff=' + str(abs(starts - stops))})")

    issues = []
    if abs(r['precip_starts'] - r['precip_stops']) > 2:
        issues.append("降水 start/stop 不配对")
    if len(sc) < 3 and r['total_ticks'] >= GAME_YEAR * 0.95:
        issues.append(f"全年仅 {len(sc)} 次季节切换（预期 3-4）")
    print(f"\n── 健康检查 ──")
    print("  ✓ 全部通过" if not issues else "".join(f"\n  ⚠ {i}" for i in issues))
    print()
    print("=" * 64)


def main():
    parser = argparse.ArgumentParser(description="天气系统年模拟")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--fraction", type=float, default=1.0,
                        help="模拟比例 (1.0=全年)")
    args = parser.parse_args()

    r = run(args.seed, args.fraction)
    print_report(r)


if __name__ == "__main__":
    main()
