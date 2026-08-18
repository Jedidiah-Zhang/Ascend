"""生命周期栈单元测试 — 装配/拆卸逆操作登记与逆序回滚。"""

import pytest

from ascend.lifecycle import LifecycleStack


class TestLifecycleStack:
    def test_teardown_runs_in_reverse_order(self):
        """teardown 按装配逆序（LIFO）执行。"""
        stack = LifecycleStack()
        order = []

        def make_step(name):
            return lambda: order.append(name)

        stack.push(make_step("a"))
        stack.push(make_step("b"))
        stack.push(make_step("c"))
        stack.teardown()
        assert order == ["c", "b", "a"]

    def test_teardown_is_idempotent(self):
        """teardown 幂等：重复调用不重复执行。"""
        stack = LifecycleStack()
        calls = []
        stack.push(lambda: calls.append(1))
        stack.teardown()
        stack.teardown()
        assert calls == [1]

    def test_one_failure_does_not_block_others(self):
        """单项逆操作异常被隔离，其余仍执行。"""
        stack = LifecycleStack()
        calls = []

        def failing():
            raise RuntimeError("boom")

        stack.push(failing)
        stack.push(lambda: calls.append("b"))
        stack.push(lambda: calls.append("a"))
        stack.teardown()
        # 逆序：a → b → failing（失败被记录，不阻断）
        assert calls == ["a", "b"]

    def test_push_after_teardown_allowed(self):
        """teardown 后可继续 push（stop 后重新 start 场景）。"""
        stack = LifecycleStack()
        calls = []
        stack.push(lambda: calls.append("x"))
        stack.teardown()
        stack.push(lambda: calls.append("y"))
        stack.teardown()
        assert calls == ["x", "y"]

    def test_empty_teardown_is_noop(self):
        """空栈 teardown 不报错。"""
        LifecycleStack().teardown()

    def test_release_references_after_teardown(self):
        """teardown 后清空登记，不再持有逆操作引用。"""
        stack = LifecycleStack()
        stack.push(lambda: None)
        stack.teardown()
        assert stack._teardowns == []
