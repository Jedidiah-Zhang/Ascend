"""事件 Schema 注册与校验单元测试。"""

import pytest
from ascend.world_tree import (
    Event, AffectedParty, WorldTree, EventSchema, SchemaRegistry,
)


def make_event(event_type="test", data=None, **kwargs):
    """创建测试用 Event 的快捷方法。"""
    return Event(
        timestamp=0.0,
        location=(0, 0, None, None),
        initiator_type="system",
        initiator_id="test-1",
        affected=[AffectedParty(entity_id="test-1", role="subject")],
        event_type=event_type,
        data=data or {},
        **kwargs,
    )


class TestEventSchema:
    """EventSchema 单元测试。"""

    def test_empty_schema_passes(self):
        """无 required/optional 的 schema 总是通过。"""
        schema = EventSchema("test")
        assert schema.validate({}) == []
        assert schema.validate({"a": 1}) == []

    def test_required_field_present(self):
        """必填字段存在且类型正确时通过。"""
        schema = EventSchema("test", required={"temp": float})
        assert schema.validate({"temp": 3.14}) == []

    def test_required_field_missing(self):
        """缺少必填字段时返回错误。"""
        schema = EventSchema("test", required={"temp": float})
        errors = schema.validate({})
        assert len(errors) == 1
        assert "缺少必填字段" in errors[0]
        assert "temp" in errors[0]

    def test_required_field_wrong_type(self):
        """必填字段类型错误时返回错误。"""
        schema = EventSchema("test", required={"count": int})
        errors = schema.validate({"count": "not an int"})
        assert len(errors) == 1
        assert "类型错误" in errors[0]

    def test_optional_field_correct_type(self):
        """可选字段存在且类型正确时通过。"""
        schema = EventSchema("test", optional={"note": str})
        assert schema.validate({"note": "hello"}) == []

    def test_optional_field_missing(self):
        """可选字段缺失时通过。"""
        schema = EventSchema("test", optional={"note": str})
        assert schema.validate({}) == []

    def test_optional_field_wrong_type(self):
        """可选字段存在但类型错误时返回错误。"""
        schema = EventSchema("test", optional={"count": int})
        errors = schema.validate({"count": "bad"})
        assert len(errors) == 1
        assert "类型错误" in errors[0]

    def test_multiple_errors(self):
        """多个字段问题同时报告。"""
        schema = EventSchema(
            "test",
            required={"a": int, "b": str},
            optional={"c": float},
        )
        errors = schema.validate({"b": 123, "c": "bad"})
        # a 缺失, b 类型错, c 类型错 → 3 个错误
        assert len(errors) == 3

    def test_union_type(self):
        """支持 type | type 联合类型。"""
        schema = EventSchema("test", required={"val": (int, float)})
        assert schema.validate({"val": 1}) == []
        assert schema.validate({"val": 1.5}) == []
        errors = schema.validate({"val": "str"})
        assert len(errors) == 1

    def test_repr(self):
        """repr 包含事件类型和字段计数。"""
        schema = EventSchema("weather", required={"t": float},
                            optional={"h": float})
        r = repr(schema)
        assert "weather" in r
        assert "required=1" in r
        assert "optional=1" in r


class TestSchemaRegistry:
    """SchemaRegistry 单元测试。"""

    def test_register_and_get(self):
        """注册后可通过 get 获取 schema。"""
        reg = SchemaRegistry()
        reg.register("test", required={"x": int})
        s = reg.get("test")
        assert s is not None
        assert s.event_type == "test"

    def test_get_unregistered(self):
        """查询未注册类型返回 None。"""
        reg = SchemaRegistry()
        assert reg.get("nonexistent") is None

    def test_validate_unregistered_skips(self):
        """未注册事件类型的 validate 返回空（跳过校验）。"""
        reg = SchemaRegistry()
        assert reg.validate("unknown", {}) == []

    def test_validate_registered(self):
        """已注册类型的 validate 进行实际校验。"""
        reg = SchemaRegistry()
        reg.register("test", required={"key": str})
        errors = reg.validate("test", {})
        assert len(errors) == 1

    def test_reregister_overwrites(self):
        """重新注册同类型会覆盖旧 schema。"""
        reg = SchemaRegistry()
        reg.register("test", required={"a": int})
        reg.register("test", required={"b": str})
        s = reg.get("test")
        assert "b" in s.required
        assert "a" not in s.required

    def test_registered_types_sorted(self):
        """registered_types 返回排序列表。"""
        reg = SchemaRegistry()
        reg.register("c")
        reg.register("a")
        reg.register("b")
        assert reg.registered_types == ["a", "b", "c"]

    def test_list_schemas(self):
        """list_schemas 返回结构化摘要。"""
        reg = SchemaRegistry()
        reg.register("move", required={"dx": int, "dy": int},
                    description="移动事件")
        schemas = reg.list_schemas()
        assert len(schemas) == 1
        s = schemas[0]
        assert s["event_type"] == "move"
        assert set(s["required"]) == {"dx", "dy"}
        assert s["description"] == "移动事件"

    def test_repr(self):
        """repr 包含已注册类型数量。"""
        reg = SchemaRegistry()
        reg.register("a")
        reg.register("b")
        assert "types=2" in repr(reg)


class TestWorldTreeSchemaIntegration:
    """WorldTree 集成 schema 校验的测试。"""

    def test_publish_with_schema_passes(self):
        """data 符合 schema 时 publish 成功。"""
        reg = SchemaRegistry()
        reg.register("move", required={"dx": int, "dy": int})
        bus = WorldTree(validate=True, schema_registry=reg)
        ev = make_event("move", data={"dx": 1, "dy": 2})
        bus.publish(ev)  # 不抛异常

    def test_publish_with_schema_missing_field_raises(self):
        """缺少 schema 必填字段时 publish 抛出 ValueError。"""
        reg = SchemaRegistry()
        reg.register("move", required={"dx": int, "dy": int})
        bus = WorldTree(validate=True, schema_registry=reg)
        ev = make_event("move", data={"dx": 1})  # 缺少 dy
        with pytest.raises(ValueError, match="schema 校验失败"):
            bus.publish(ev)

    def test_publish_with_schema_wrong_type_raises(self):
        """schema 字段类型错误时 publish 抛出 ValueError。"""
        reg = SchemaRegistry()
        reg.register("move", required={"dx": int})
        bus = WorldTree(validate=True, schema_registry=reg)
        ev = make_event("move", data={"dx": "bad"})
        with pytest.raises(ValueError, match="schema 校验失败"):
            bus.publish(ev)

    def test_publish_unregistered_type_skips_schema(self):
        """未注册事件类型跳过 schema 校验，正常 publish。"""
        reg = SchemaRegistry()
        bus = WorldTree(validate=True, schema_registry=reg)
        ev = make_event("unknown_type", data={"anything": "goes"})
        bus.publish(ev)  # 不抛异常
        assert bus.event_count == 1

    def test_publish_no_registry_backward_compat(self):
        """不配置 schema_registry 时行为不变。"""
        bus = WorldTree(validate=True)  # 无 registry
        ev = make_event("any", data={"x": "y"})
        bus.publish(ev)  # 不抛异常
        assert bus.event_count == 1

    def test_publish_validate_disabled_skips_schema(self):
        """validate=False 时 schema 校验也被跳过。"""
        reg = SchemaRegistry()
        reg.register("move", required={"dx": int})
        bus = WorldTree(validate=False, schema_registry=reg)
        ev = make_event("move", data={})  # 缺少 dx，但 validation 关闭
        bus.publish(ev)  # 不抛异常

    def test_register_event_schema_convenience(self):
        """register_event_schema 便捷方法自动创建 registry。"""
        bus = WorldTree(validate=True)
        assert bus.schema_registry is None

        bus.register_event_schema("jump", required={"height": float})
        assert bus.schema_registry is not None
        assert bus.schema_registry.get("jump") is not None

        # 校验生效
        ev = make_event("jump", data={"height": 1.5})
        bus.publish(ev)  # 不抛异常

        with pytest.raises(ValueError):
            bus.publish(make_event("jump", data={}))

    def test_schema_registry_property(self):
        """schema_registry property 返回配置的注册表。"""
        reg = SchemaRegistry()
        bus = WorldTree(schema_registry=reg)
        assert bus.schema_registry is reg
