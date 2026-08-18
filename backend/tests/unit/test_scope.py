"""订阅作用域单元测试 — 统一收集/撤销订阅。"""

import pytest

from ascend.world_tree import WorldTree, SubscriptionScope
from ascend.world_tree.event import Event, AffectedParty


def make_event(event_type="test", timestamp=0) -> Event:
    return Event(
        timestamp=timestamp,
        location=(0, 0, None, None),
        initiator_type="system",
        initiator_id="a",
        affected=[AffectedParty(entity_id="a", role="subject")],
        event_type=event_type,
    )


class TestSubscriptionScope:
    def test_capture_and_close_unsubscribes_all(self):
        """capture 登记多个凭证，close() 一次全部撤销。"""
        bus = WorldTree()
        received = []
        scope = SubscriptionScope()
        scope.capture(bus.subscribe("t1", lambda e: received.append(e)))
        scope.capture(bus.subscribe("t2", lambda e: received.append(e)))
        scope.capture(bus.subscribe("*", lambda e: received.append(e)))
        assert bus.subscriber_count == 3

        scope.close()
        assert bus.subscriber_count == 0
        bus.publish(make_event("t1"))
        bus.publish(make_event("t2"))
        bus.publish(make_event("t3"))
        assert received == []

    def test_close_is_idempotent(self):
        """close() 幂等：重复调用不报错、不重复撤销。"""
        bus = WorldTree()
        unsub_calls = []
        scope = SubscriptionScope()

        def fake_subscribe(_t, _cb, location_filter=None):
            def _unsub():
                unsub_calls.append(1)
            return _unsub

        scope.capture(fake_subscribe("t", lambda e: None))
        scope.close()
        scope.close()
        assert len(unsub_calls) == 1

    def test_capture_after_close_unsubscribes_immediately(self):
        """已关闭作用域再登记凭证：立即撤销，不泄漏。"""
        bus = WorldTree()
        scope = SubscriptionScope()
        scope.close()
        scope.capture(bus.subscribe("t", lambda e: None))
        assert bus.subscriber_count == 0

    def test_subscribe_method(self):
        """subscribe 便捷方法：订阅 + 登记一步完成。"""
        bus = WorldTree()
        received = []
        scope = SubscriptionScope()
        scope.subscribe(bus, "t", lambda e: received.append(e))
        bus.publish(make_event("t"))
        assert len(received) == 1
        scope.close()
        assert bus.subscriber_count == 0

    def test_one_failure_does_not_block_others(self):
        """单项退订异常被隔离，其余正常撤销。"""
        bus = WorldTree()
        scope = SubscriptionScope()
        scope.capture(bus.subscribe("t1", lambda e: None))

        def failing_unsub():
            raise RuntimeError("boom")

        scope.capture(failing_unsub)
        scope.capture(bus.subscribe("t2", lambda e: None))
        scope.close()
        # failing_unsub 抛异常被记录，t1/t2 仍撤销
        assert bus.subscriber_count == 0

    def test_close_releases_references(self):
        """close() 后作用域不再持有退订凭证。"""
        bus = WorldTree()
        scope = SubscriptionScope()
        scope.capture(bus.subscribe("t", lambda e: None))
        scope.close()
        assert scope._unsubs == []
