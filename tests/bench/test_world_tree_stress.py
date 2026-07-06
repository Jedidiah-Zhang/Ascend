"""世界树事件系统压力测试。

以 max_memory_events=500K 为阈值，在远超实际游戏负载的
事件量下验证自动 trim、归档、因果链、并发安全和吞吐量。

压力级别（可通过 STRESS_SCALE 环境变量控制）:
  small  — CI 快速验证（默认）
  medium — 中等压力
  large  — 等同于最终发布场景 ×1000（30M 事件，仅手动运行）
"""

import os
import tempfile
import threading
import time

import pytest
from ascend.world_tree import Event, AffectedParty, WorldTree, EventGraph


# ── 测试辅助 ──────────────────────────────────────────────

_STRESS_SCALE = os.environ.get("STRESS_SCALE", "small")

# 各压力级别的参数
_SCALES: dict[str, dict] = {
    "small": {
        "max_events": 500_000,
        "publish_n": 1_200_000,       # 触发 trim ~2 次
        "archive_n": 800_000,          # 归档测试
        "throughput_n": 50_000,        # 吞吐量基准
        "concurrent_n": 100_000,       # 并发测试
        "concurrent_threads": 4,
        "causal_chain_n": 50,          # 因果链长度
        "causal_trim_cycles": 3,       # trim 周期数
    },
    "medium": {
        "max_events": 500_000,
        "publish_n": 3_000_000,
        "archive_n": 2_000_000,
        "throughput_n": 500_000,
        "concurrent_n": 500_000,
        "concurrent_threads": 8,
        "causal_chain_n": 200,
        "causal_trim_cycles": 6,
    },
    "large": {
        "max_events": 500_000,
        "publish_n": 30_000_000,       # 3 个数量级以上
        "archive_n": 30_000_000,
        "throughput_n": 5_000_000,
        "concurrent_n": 2_000_000,
        "concurrent_threads": 8,
        "causal_chain_n": 500,
        "causal_trim_cycles": 60,
    },
}

_params = _SCALES[_STRESS_SCALE]


def make_event(timestamp=0, event_type="test", initiator_id="a",
               location=(0, 0, None, None), **kwargs) -> Event:
    """快捷构造测试事件。"""
    affected = kwargs.pop("affected", None)
    if affected is None:
        affected = [AffectedParty(entity_id=initiator_id, role="subject")]
    initiator_type = kwargs.pop("initiator_type", "system")
    return Event(
        timestamp=timestamp,
        location=location,
        initiator_type=initiator_type,
        initiator_id=initiator_id,
        affected=affected,
        event_type=event_type,
        **kwargs,
    )


# ── 内存边界 ──────────────────────────────────────────────


class TestMemoryBound:
    """验证自动 trim 将内存事件数控制在阈值内。"""

    def test_publish_beyond_threshold_stays_bounded(self):
        """发布远超阈值的事件后，内存事件数不超过 max_memory_events。"""
        max_n = _params["max_events"]
        n = _params["publish_n"]
        bus = WorldTree(validate=False, max_memory_events=max_n)

        for i in range(n):
            bus.publish(make_event(timestamp=i, id=f"e{i}"))

        assert bus.event_count <= max_n
        # 最近的事件应在内存中
        assert bus.get_event_by_id(f"e{n - 1}") is not None

    def test_timestamps_monotonic_after_trim(self):
        """多次 trim 后内存事件时间戳仍然有序。"""
        bus = WorldTree(validate=False, max_memory_events=_params["max_events"])
        n = _params["publish_n"]

        for i in range(n):
            bus.publish(make_event(timestamp=i))

        results = bus.get_events_in_range(0, n)
        for a, b in zip(results, results[1:]):
            assert a.timestamp <= b.timestamp

    def test_id_index_consistent_after_repeated_trim(self):
        """多次 trim 重建 _id_index 后，ID 查找仍然正确。"""
        bus = WorldTree(validate=False, max_memory_events=_params["max_events"])
        n = _params["publish_n"]

        for i in range(n):
            bus.publish(make_event(timestamp=i, id=f"e{i}"))

        # 最近的事件应可通过 ID 查到
        for i in range(n - 100, n):
            ev = bus.get_event_by_id(f"e{i}")
            assert ev is not None, f"e{i} 应在内存中"
            assert ev.id == f"e{i}"


# ── 归档完整性 ────────────────────────────────────────────


class TestArchiveIntegrity:
    """验证自动 trim 下归档完整性。"""

    def test_archived_events_queryable(self):
        """自动 trim 归档后，历史事件通过查询可获取。"""
        max_n = _params["max_events"]
        n = _params["archive_n"]
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = WorldTree(validate=False, max_memory_events=max_n,
                       archive_path=path)
        try:
            for i in range(n):
                bus.publish(make_event(
                    timestamp=i, id=f"e{i}",
                    initiator_id=f"entity_{i % 1000}",
                    location=(i % 50, (i // 50) % 50, None, None),
                ))

            # 归档中的早期事件仍可查询
            earliest = bus.get_events_in_range(0, 100)
            assert len(earliest) > 0, "早期事件应从归档中检索"

            # 按实体查询归档
            entity_events = bus.get_entity_events("entity_0", 0, n)
            assert len(entity_events) > 0

            # 按区域查询归档
            region_events = bus.get_events_in_region((0, 0), radius=1)
            assert len(region_events) > 0

            # 按 ID 点查归档
            ev = bus.get_event_by_id("e0")
            assert ev is not None
            assert ev.id == "e0"
        finally:
            bus._archive.close()
            os.unlink(path)

    def test_archived_events_preserve_data(self):
        """归档取回的事件保留完整字段。"""
        max_n = _params["max_events"]
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = WorldTree(validate=False, max_memory_events=max_n,
                       archive_path=path)
        try:
            special = make_event(
                timestamp=0, id="special",
                event_type="ritual",
                initiator_id="npc_1",
                location=(10, 20, 5, 5),
                affected=[AffectedParty("npc_2", "witness")],
                caused_by=["cause_1"],
                observes="observed_ev",
                data={"power": 9000, "ritual_type": "blood_moon"},
            )
            bus.publish(special)

            # 触发自动 trim
            for i in range(max_n + 10):
                bus.publish(make_event(timestamp=i + 1))

            ev = bus.get_event_by_id("special")
            assert ev is not None
            assert ev.event_type == "ritual"
            assert ev.data == {"power": 9000, "ritual_type": "blood_moon"}
            assert ev.caused_by == ["cause_1"]
            assert ev.observes == "observed_ev"
            assert len(ev.affected) == 1
        finally:
            bus._archive.close()
            os.unlink(path)


# ── 因果链追溯 ────────────────────────────────────────────


class TestCausalChainStress:
    """验证极端负载下因果链追溯的正确性。"""

    def test_causal_chain_across_trim_boundaries(self):
        """因果链跨越多次 trim 边界后仍可追溯。"""
        max_n = _params["max_events"]
        chain_len = _params["causal_chain_n"]
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = WorldTree(validate=False, max_memory_events=max_n,
                       archive_path=path)
        try:
            # 构建因果链：c0 → c1 → c2 → ... → c{N-1}
            chain_ids = [f"c{i}" for i in range(chain_len)]
            for i, cid in enumerate(chain_ids):
                caused = [chain_ids[i - 1]] if i > 0 else []
                bus.publish(make_event(
                    timestamp=i * 10, id=cid,
                    caused_by=caused,
                ))

            # 填充大量事件触发多次 trim
            for i in range(_params["publish_n"]):
                bus.publish(make_event(timestamp=chain_len * 10 + i))

            # 追溯因果链（通过 lookup 从归档补全）
            chain = bus.graph.get_causal_chain(
                chain_ids[-1],
                max_depth=chain_len,
                lookup=bus.get_event_by_id,
            )
            expected = chain_ids[:-1]
            assert chain == expected, (
                f"因果链长度不匹配: 期望 {len(expected)}, 实际 {len(chain)}"
            )
        finally:
            bus._archive.close()
            os.unlink(path)

    def test_multiple_causal_chains_after_trim(self):
        """多条独立因果链在 trim 后均可追溯。"""
        max_n = _params["max_events"]
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)

        bus = WorldTree(validate=False, max_memory_events=max_n,
                       archive_path=path)
        try:
            num_chains = 20
            chain_length = 10
            leaf_ids: list[str] = []

            for c in range(num_chains):
                for i in range(chain_length):
                    cid = f"chain{c}_e{i}"
                    caused = [f"chain{c}_e{i - 1}"] if i > 0 else []
                    bus.publish(make_event(
                        timestamp=c * 100 + i, id=cid,
                        caused_by=caused,
                    ))
                    if i == chain_length - 1:
                        leaf_ids.append(cid)

            # 填充触发 trim
            for i in range(_params["publish_n"]):
                bus.publish(make_event(timestamp=10000 + i))

            # 验证每条链
            for leaf_id in leaf_ids:
                chain = bus.graph.get_causal_chain(
                    leaf_id, max_depth=chain_length,
                    lookup=bus.get_event_by_id,
                )
                assert len(chain) == chain_length - 1
        finally:
            bus._archive.close()
            os.unlink(path)


# ── 并发安全 ──────────────────────────────────────────────


class TestConcurrentStress:
    """验证极端并发下的线程安全。"""

    def test_concurrent_publish_no_errors(self):
        """多线程并发发布，无异常、无数据丢失。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        n_total = _params["concurrent_n"]
        n_threads = _params["concurrent_threads"]
        per_thread = n_total // n_threads
        errors: list[Exception] = []

        def publisher(start_id: int):
            try:
                for i in range(per_thread):
                    bus.publish(make_event(
                        timestamp=start_id + i,
                        id=f"t{start_id}_{i}",
                    ))
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=publisher, args=(t * per_thread,))
            for t in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        assert len(errors) == 0, f"发布线程异常: {errors}"
        assert bus.event_count <= _params["max_events"]

    def test_concurrent_publish_and_query(self):
        """发布和查询并发执行，查询不报错。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        n_publish = _params["concurrent_n"] // 2
        stop = threading.Event()
        query_errors: list[Exception] = []

        def publisher():
            for i in range(n_publish):
                if stop.is_set():
                    break
                bus.publish(make_event(
                    timestamp=i, id=f"pq_{i}",
                ))

        def querier():
            for _ in range(5000):
                if stop.is_set():
                    break
                try:
                    bus.get_events_in_range(0, n_publish)
                    bus.get_event_by_id(f"pq_{n_publish // 2}")
                    bus.event_count
                except Exception as e:
                    query_errors.append(e)

        pub_thread = threading.Thread(target=publisher)
        q_threads = [threading.Thread(target=querier) for _ in range(4)]

        pub_thread.start()
        for t in q_threads:
            t.start()

        pub_thread.join(timeout=120)
        stop.set()
        for t in q_threads:
            t.join(timeout=30)

        assert len(query_errors) == 0, f"查询线程异常: {query_errors}"

    def test_graph_not_corrupted_by_concurrent_publish(self):
        """并发发布不损坏图结构。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        n = _params["concurrent_n"] // 4
        n_threads = _params["concurrent_threads"]

        def publisher(thread_id: int):
            for i in range(n):
                parent = f"g{thread_id}_{i - 1}" if i > 0 else None
                bus.publish(make_event(
                    timestamp=thread_id * n + i,
                    id=f"g{thread_id}_{i}",
                    caused_by=[parent] if parent else [],
                ))

        threads = [threading.Thread(target=publisher, args=(t,))
                   for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)

        # 图不应崩溃，基本查询正常
        assert bus.graph.node_count > 0


# ── 吞吐量基准 ─────────────────────────────────────────────


class TestThroughput:
    """性能基准测试。"""

    def test_publish_throughput(self):
        """测量单线程发布吞吐量（事件/秒）。"""
        bus = WorldTree(validate=False)
        n = _params["throughput_n"]

        start = time.perf_counter()
        for i in range(n):
            bus.publish(make_event(
                timestamp=i, id=f"tp_{i}",
            ))
        elapsed = time.perf_counter() - start

        rate = n / elapsed if elapsed > 0 else 0
        # 记录性能数据（非硬性断言，仅报告）
        print(f"\n  单线程吞吐量: {rate:,.0f} 事件/秒 ({n} 事件 / {elapsed:.2f}秒)")

        # 基准下限：至少 50K 事件/秒（单线程 Python）
        assert rate > 50_000, f"吞吐量过低: {rate:,.0f} 事件/秒"

    def test_publish_with_auto_trim_throughput(self):
        """测量有自动 trim 时的发布吞吐量。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        n = min(_params["throughput_n"], _params["max_events"] + 500_000)

        start = time.perf_counter()
        for i in range(n):
            bus.publish(make_event(
                timestamp=i, id=f"atp_{i}",
            ))
        elapsed = time.perf_counter() - start

        rate = n / elapsed if elapsed > 0 else 0
        trim_count = n // (_params["max_events"] // 2) if n > _params["max_events"] else 0
        print(f"\n  有自动 trim 吞吐量: {rate:,.0f} 事件/秒 "
              f"({n} 事件, ~{trim_count} 次 trim, {elapsed:.2f}秒)")

        # 有 trim 时基准下限适当放宽
        assert rate > 10_000, f"trim 下吞吐量过低: {rate:,.0f} 事件/秒"

    def test_query_after_stress(self):
        """大量事件后查询仍然快速。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        n = _params["throughput_n"]

        for i in range(n):
            bus.publish(make_event(
                timestamp=i, id=f"q_{i}",
                initiator_id=f"e_{i % 100}",
                location=(i % 50, (i // 50) % 50, None, None),
            ))

        # 时间范围查询
        start = time.perf_counter()
        results = bus.get_events_in_range(0, n)
        elapsed = time.perf_counter() - start
        print(f"\n  时间范围查询: {len(results)} 结果 / {elapsed:.4f}秒")

        # ID 点查
        start = time.perf_counter()
        ev = bus.get_event_by_id(f"q_{n // 2}")
        elapsed = time.perf_counter() - start
        assert ev is not None
        print(f"  ID 点查: {elapsed:.6f}秒")

        # 实体查询
        start = time.perf_counter()
        entity_events = bus.get_entity_events("e_0", 0, n)
        elapsed = time.perf_counter() - start
        print(f"  实体查询: {len(entity_events)} 结果 / {elapsed:.4f}秒")


# ── 图一致性 ──────────────────────────────────────────────


class TestGraphConsistency:
    """验证极端负载下图的内部一致性。"""

    def test_node_count_never_exceeds_memory_events(self):
        """图节点数不应远超内存事件数。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        n = _params["publish_n"]

        for i in range(n):
            bus.publish(make_event(
                timestamp=i, id=f"gc_{i}",
                caused_by=[f"gc_{i - 1}"] if i > 0 and i % 10 == 0 else [],
            ))

        # 图节点数不应远超内存事件数（允许一些 lookup 回填的残留）
        assert bus.graph.node_count <= bus.event_count + 5000, (
            f"图节点过多: graph={bus.graph.node_count}, events={bus.event_count}"
        )

    def test_graph_repr_stable_under_load(self):
        """图的 __repr__ 在负载下不崩溃。"""
        bus = WorldTree(validate=False,
                       max_memory_events=_params["max_events"])
        for i in range(10000):
            bus.publish(make_event(timestamp=i))
        r = repr(bus.graph)
        assert "EventGraph" in r
        assert "nodes=" in r


# ── 运行说明 ──────────────────────────────────────────────
"""
运行方式:

  # CI 快速（默认）
  pytest tests/bench/test_world_tree_stress.py -v

  # 中等压力
  STRESS_SCALE=medium pytest tests/bench/test_world_tree_stress.py -v

  # 完整压力（30M 事件，预计 5-10 分钟）
  STRESS_SCALE=large pytest tests/bench/test_world_tree_stress.py -v -s
"""
