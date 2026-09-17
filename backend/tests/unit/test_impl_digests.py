"""实现内容摘要表测试（issue #49：身份=实现内容，构建期嵌入）。

覆盖：
- 加载器 fail-closed（未知字段/缺键/版本/重复/摘要格式/坏 JSON）；
- 提交表 == 生产注册表实时重算（漂移门禁）；
- 源码模式表陈旧即拒绝；打包模式缺条目即拒绝（见 test_causal_registry）。
"""

from __future__ import annotations

import json

import pytest

from ascend.causal.declaration import DeclarationError
from ascend.causal.impl_digests import (
    IMPL_DIGESTS_PATH,
    SCHEMA_VERSION,
    ImplementationDigestTable,
    load_impl_digests,
)
from ascend.causal.world import ASCEND_MECHANISMS

_DIGEST_ZERO = "sha256:" + "0" * 64


def _valid_payload() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "contract_version": "v0.1",
        "digests": [
            {
                "mechanism_id": "toy.mech.v1",
                "output": "toy.out",
                "equation_version": _DIGEST_ZERO,
            },
        ],
    }


def _write(tmp_path, payload: dict):
    path = tmp_path / "impl_digests.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


class TestLoader:
    def test_load_valid(self, tmp_path):
        table = load_impl_digests(_write(tmp_path, _valid_payload()))
        assert table.schema_version == SCHEMA_VERSION
        entry = table.require("toy.mech.v1")
        assert entry.output == "toy.out"
        assert entry.equation_version == _DIGEST_ZERO
        assert table.get("no.such") is None

    def test_default_path_inside_declarations(self):
        assert IMPL_DIGESTS_PATH.parent.name == "declarations"
        assert IMPL_DIGESTS_PATH.name == "impl_digests.json"

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(DeclarationError, match="不可读"):
            load_impl_digests(tmp_path / "missing.json")

    def test_bad_json_rejected(self, tmp_path):
        path = tmp_path / "impl_digests.json"
        path.write_text("{oops", encoding="utf-8")
        with pytest.raises(DeclarationError, match="不是合法 JSON"):
            load_impl_digests(path)

    def test_require_missing_mechanism_rejected(self):
        table = ImplementationDigestTable(
            schema_version=1, contract_version="v0.1", entries=(),
        )
        with pytest.raises(DeclarationError, match="缺少机制"):
            table.require("toy.mech.v1")

    @pytest.mark.parametrize("mutate, match", [
        (lambda p: p.update({"extra": 1}), "未知字段"),
        (lambda p: p.pop("contract_version"), "缺少必填字段"),
        (lambda p: p.update({"schema_version": 99}), "不支持"),
        (lambda p: p.update({"schema_version": True}), "应为整数"),
        (lambda p: p.update({"digests": {}}), "应为数组"),
        (lambda p: p["digests"].append(dict(p["digests"][0])), "重复机制"),
        (lambda p: p["digests"][0].pop("output"), "缺少必填字段"),
        (lambda p: p["digests"][0].update({"mechanism_id": "Bad-ID"}),
         "格式非法"),
        (lambda p: p["digests"][0].update({"output": ""}), "非空字符串"),
        (lambda p: p["digests"][0].update({"equation_version": "sha256:bad"}),
         "sha256"),
    ])
    def test_rejects(self, tmp_path, mutate, match):
        payload = _valid_payload()
        mutate(payload)
        with pytest.raises(DeclarationError, match=match):
            load_impl_digests(_write(tmp_path, payload))


class TestRegistryDrift:
    """提交表与生产注册表的双向漂移门禁（CI --check 的测试内对照）。"""

    def test_table_covers_all_mechanisms_and_matches(self):
        table = load_impl_digests()
        live = {
            spec.mechanism_id: (
                spec.output, ASCEND_MECHANISMS.equation_version(spec.output),
            )
            for spec in ASCEND_MECHANISMS.mechanisms.values()
        }
        committed = {
            entry.mechanism_id: (entry.output, entry.equation_version)
            for entry in table.entries
        }
        assert committed == live, (
            "实现摘要表与注册表实时重算不一致；"
            "重新生成 research/equations/export_impl_digests.py"
        )

    def test_source_mode_stale_table_rejected(self, monkeypatch):
        """源码模式：表存在但与实时值不符 → 注册表构造拒绝（fail-closed）。"""
        import ascend.causal.registry as registry_module
        from ascend.causal.impl_digests import ImplementationDigest
        from ascend.causal.world import build_registry

        table = load_impl_digests()
        stale = ImplementationDigestTable(
            schema_version=table.schema_version,
            contract_version=table.contract_version,
            entries=tuple(
                ImplementationDigest(
                    mechanism_id=entry.mechanism_id,
                    output=entry.output,
                    equation_version=_DIGEST_ZERO,
                )
                for entry in table.entries
            ),
        )
        monkeypatch.setattr(
            registry_module, "get_impl_digests", lambda: stale,
        )
        with pytest.raises(ValueError, match="实现内容摘要表与源码不一致"):
            build_registry()
