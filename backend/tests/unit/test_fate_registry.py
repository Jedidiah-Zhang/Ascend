"""命运命名空间登记（fate_namespaces.json）的校验与摘要测试。

对应《世界契约》WC-5.5 / 附录 D.3：命名空间与用途登记为机器数据，
摘要进入世界身份；名称重复即拒绝。
"""

import json
from pathlib import Path

import pytest

from ascend.causal import (
    FATE_NAMESPACES_PATH,
    DeclarationError,
    FateNamespaceRegistry,
    load_fate_namespaces,
)
from ascend.fate import FateAddress


def _raw() -> dict:
    return json.loads(FATE_NAMESPACES_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "fate_namespaces.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _entry(data: dict, namespace: str, purpose: str) -> dict:
    return next(
        entry for entry in data["namespaces"]
        if entry["namespace"] == namespace and entry["purpose"] == purpose
    )


class TestRegistryFile:
    def test_real_registry_loads(self):
        registry = load_fate_namespaces()
        assert isinstance(registry, FateNamespaceRegistry)
        assert len(registry.entries) >= 8

    def test_namespace_purpose_pairs_unique(self):
        keys = [
            (entry.namespace, entry.purpose)
            for entry in load_fate_namespaces().entries
        ]
        assert len(set(keys)) == len(keys)

    def test_entries_are_address_constructible(self):
        for entry in load_fate_namespaces().entries:
            address = FateAddress(
                entry.namespace, entry.purpose, tuple(entry.address_shape),
            )
            assert address.parts()[0] == entry.namespace

    def test_lookup_and_require(self):
        registry = load_fate_namespaces()
        entry = registry.require("weather", "proxy.temp")
        assert entry.consumer
        assert registry.lookup("weather", "proxy.temp") == entry
        assert registry.lookup("weather", "no.such") is None
        with pytest.raises(KeyError):
            registry.require("weather", "no.such")

    def test_digest_is_stable_sha256_hex(self):
        digest = load_fate_namespaces().digest()
        assert len(digest) == 64
        assert digest == load_fate_namespaces().digest()


class TestRegistryValidation:
    def test_unknown_top_level_field_rejected(self, tmp_path):
        data = _raw()
        data["extra"] = 1
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_unknown_entry_field_rejected(self, tmp_path):
        data = _raw()
        data["namespaces"][0]["extra"] = 1
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_missing_entry_field_rejected(self, tmp_path):
        data = _raw()
        del data["namespaces"][0]["consumer"]
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_schema_version_must_match_loader(self, tmp_path):
        data = _raw()
        data["schema_version"] = 2
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_duplicate_namespace_purpose_rejected(self, tmp_path):
        data = _raw()
        first = data["namespaces"][0]
        data["namespaces"].append(dict(first))
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_invalid_namespace_format_rejected(self, tmp_path):
        data = _raw()
        _entry(data, "world", "birth_point")["namespace"] = "World"
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_invalid_purpose_format_rejected(self, tmp_path):
        data = _raw()
        _entry(data, "world", "birth_point")["purpose"] = "TBD:#48"
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_address_shape_must_be_name_list(self, tmp_path):
        data = _raw()
        _entry(data, "weather", "texture.channel")["address_shape"] = "channel"
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_address_shape_item_must_be_component_name(self, tmp_path):
        data = _raw()
        _entry(data, "weather", "texture.channel")["address_shape"] = ["x", ""]
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_empty_consumer_rejected(self, tmp_path):
        data = _raw()
        data["namespaces"][0]["consumer"] = ""
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_empty_note_rejected(self, tmp_path):
        data = _raw()
        data["namespaces"][0]["note"] = ""
        with pytest.raises(DeclarationError):
            load_fate_namespaces(_write(tmp_path, data))

    def test_missing_file_rejected(self, tmp_path):
        with pytest.raises(DeclarationError):
            load_fate_namespaces(tmp_path / "absent.json")

    def test_corrupt_json_rejected(self, tmp_path):
        path = tmp_path / "fate_namespaces.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(DeclarationError):
            load_fate_namespaces(path)


class TestRegistryDigest:
    def test_digest_ignores_note_wording(self, tmp_path):
        data = _raw()
        data["namespaces"][0]["note"] = "换一种说法"
        assert load_fate_namespaces(_write(tmp_path, data)).digest() == \
            load_fate_namespaces().digest()

    def test_digest_independent_of_entry_order(self, tmp_path):
        data = _raw()
        data["namespaces"] = list(reversed(data["namespaces"]))
        assert load_fate_namespaces(_write(tmp_path, data)).digest() == \
            load_fate_namespaces().digest()

    def test_digest_changes_on_consumer_change(self, tmp_path):
        data = _raw()
        data["namespaces"][0]["consumer"] = "other.py"
        assert load_fate_namespaces(_write(tmp_path, data)).digest() != \
            load_fate_namespaces().digest()

    def test_digest_changes_on_shape_change(self, tmp_path):
        data = _raw()
        entry = _entry(data, "weather", "texture.channel")
        entry["address_shape"] = ["channel", "index", "variant"]
        assert load_fate_namespaces(_write(tmp_path, data)).digest() != \
            load_fate_namespaces().digest()

    def test_digest_changes_on_purpose_change(self, tmp_path):
        data = _raw()
        entry = _entry(data, "weather", "proxy.temp")
        entry["purpose"] = "proxy.temperature"
        assert load_fate_namespaces(_write(tmp_path, data)).digest() != \
            load_fate_namespaces().digest()
