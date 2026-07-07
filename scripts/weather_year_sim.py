"""天气系统年模拟 — 使用真实后端流程 + tick 加速。

用法:
    cd ascend-backend && PYTHONPATH=. ../.venv/bin/python ../scripts/weather_year_sim.py [--seed=42] [--fraction=1.0]
"""

import argparse
import random
import time as _real_time
from collections import defaultdict

from ascend.time import WorldClock, GameCalendar
from ascend.time.constants import GAME_HOUR, GAME_DAY, GAME_YEAR
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
        weather.register_chunk(cx, cy, chunk.annual_baseline, chunk.climate_zone)
    print(f"── 天气引擎接入 {len(loaded)} chunk")

    # ── 4. 订阅天气事件 ─────────────────────────────
    collected: dict[str, list] = defaultdict(list)
    event_types = [
        "temperature_change", "humidity_change", "wind_change",
        "precipitation_start", "precipitation_stop",
        "season_change", "sunrise", "sunset",
    ]
    for ev_type in event_types:
        world_tree.subscribe(
            ev_type,
            lambda e, _t=ev_type: collected[_t].append(e),
        )

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
    counts = {t: len(collected[t]) for t in event_types}
    season_changes = [
        (e.data["season"], e.timestamp // GAME_DAY + 1,
         e.data["time_of_day"] // GAME_HOUR)
        for e in collected["season_change"]
    ]
    precip_starts = len(collected["precipitation_start"])
    precip_stops = len(collected["precipitation_stop"])
    snow_count = sum(
        1 for e in collected["precipitation_start"]
        if e.data.get("precip_type") == "snow"
    )
    sr_times = [e.data["time_of_day"] // GAME_HOUR
                for e in collected["sunrise"]]
    ss_times = [e.data["time_of_day"] // GAME_HOUR
                for e in collected["sunset"]]

    return {
        "seed": seed, "birth": birth, "num_chunks": len(loaded),
        "climate_counts": dict(climate_counts), "counts": counts,
        "season_changes": season_changes,
        "precip_starts": precip_starts, "precip_stops": precip_stops,
        "snow_count": snow_count,
        "sunrise_times": sr_times, "sunset_times": ss_times,
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

    sr = r["sunrise_times"]
    ss = r["sunset_times"]
    print(f"\n── 日出/日落 ──")
    print(f"  日出: {len(sr)} 次  "
          f"最早 {min(sr) if sr else '?'}:00  最晚 {max(sr) if sr else '?'}:00")
    print(f"  日落: {len(ss)} 次  "
          f"最早 {min(ss) if ss else '?'}:00  最晚 {max(ss) if ss else '?'}:00")

    print(f"\n── 降水 ──")
    print(f"  start: {r['precip_starts']}  stop: {r['precip_stops']}  "
          f"(diff={abs(r['precip_starts'] - r['precip_stops'])})")
    print(f"  雪事件: {r['snow_count']}")

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
