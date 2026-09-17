"""更新点声明（declarations/update_points.json）加载与校验测试。"""

import json
from pathlib import Path

import pytest

from ascend.causal.declaration import DeclarationError
from ascend.causal.update_points import (
    UPDATE_POINTS_PATH,
    UpdatePointTable,
    load_update_points,
)


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "update_points.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _valid() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "v0.1",
        "points": [
            {
                "id": "weather.evaluate",
                "owner": "world.weather",
                "period": "game_minute",
                "order": 10,
                "slots": [],
            },
            {
                "id": "terrain.integrate",
                "owner": "world.terrain.settlement",
                "period": "game_hour",
                "order": 20,
                "slots": ["world.terrain.moisture"],
            },
        ],
    }


class TestLoad:
    def test_default_declaration(self):
        table = load_update_points()
        assert table.schema_version == 1
        assert [p.id for p in table.points] == [
            "weather.evaluate", "terrain.integrate",
        ]
        assert [p.order for p in table.points] == [10, 20]
        assert [p.period for p in table.points] == [
            "game_minute", "game_hour",
        ]
        terrain = table.require("terrain.integrate")
        assert terrain.slots == (
            "world.terrain.moisture",
            "world.terrain.snow",
            "world.terrain.ice",
            "world.terrain.integrated_through",
        )
        assert table.require("weather.evaluate").slots == ()

    def test_sorted_by_order(self, tmp_path):
        data = _valid()
        data["points"].reverse()
        table = load_update_points(_write(tmp_path, data))
        assert [p.order for p in table.points] == [10, 20]

    def test_require_unknown_raises(self):
        table = load_update_points()
        with pytest.raises(KeyError, match="未登记"):
            table.require("unknown.step")

    def test_digest_stable_and_sensitive(self, tmp_path):
        first = load_update_points()
        assert first.digest() == load_update_points().digest()
        data = _valid()
        data["points"][0]["order"] = 5
        changed = load_update_points(_write(tmp_path, data))
        assert changed.digest() != first.digest()

    def test_declaration_path_is_project_file(self):
        assert UPDATE_POINTS_PATH.name == "update_points.json"
        assert UPDATE_POINTS_PATH.exists()


class TestReject:
    @pytest.mark.parametrize("mutate,match", [
        (lambda d: d.update({"unknown": 1}), "未知字段"),
        (lambda d: d.pop("contract_version"), "缺少必填字段"),
        (lambda d: d.update({"schema_version": 2}), "不支持"),
        (lambda d: d["points"][0].update({"period": "game_week"}), "未知周期"),
        (lambda d: d["points"][0].update({"order": 0}), "正整数"),
        (lambda d: d["points"][0].update({"id": "Bad.Id"}), "格式非法"),
        (lambda d: d["points"][0].update({"slots": "x"}), "应为数组"),
        (lambda d: d["points"][0].update({"slots": ["a", "a"]}), "重复槽位"),
        (lambda d: d["points"][1].update({"id": "weather.evaluate"}),
         "重复更新点"),
        (lambda d: d["points"][1].update({"order": 10}), "重复执行顺序"),
        (lambda d: d["points"][0].update({"owner": ""}), "非空字符串"),
        (lambda d: d.update({"points": []}), ""),
    ])
    def test_invalid(self, tmp_path, mutate, match):
        data = _valid()
        mutate(data)
        if match:
            with pytest.raises(DeclarationError, match=match):
                load_update_points(_write(tmp_path, data))
        else:
            table = load_update_points(_write(tmp_path, data))
            assert table.points == ()

    def test_unreadable(self, tmp_path):
        with pytest.raises(DeclarationError, match="不可读"):
            load_update_points(tmp_path / "missing.json")

    def test_not_json(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{", encoding="utf-8")
        with pytest.raises(DeclarationError, match="不是合法 JSON"):
            load_update_points(path)
