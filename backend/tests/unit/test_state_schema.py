"""世界状态声明（state_slots.json）的校验、摘要与规范编码测试。

对应《世界契约》WC-3 / 附录 D.1；声明数据变更即新世界身份。
"""

import hashlib
import json
from pathlib import Path

import pytest

from ascend.causal import (
    DECLARATION_PATH,
    DeclarationError,
    StateDeclaration,
    StateSlice,
    encode_state,
    load_declaration,
    state_digest,
)

_CELLS = 200 * 200
_CHUNKS = ((0, 0), (1, 0))
_CHANNEL_IDS = (
    "world.terrain.moisture",
    "world.terrain.snow",
    "world.terrain.ice",
)
_CURSOR_ID = "world.terrain.integrated_through"
_FIELD_IDS = (*_CHANNEL_IDS, _CURSOR_ID)


def _raw() -> dict:
    return json.loads(DECLARATION_PATH.read_text(encoding="utf-8"))


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "state_slots.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _slot(data: dict, slot_id: str) -> dict:
    return next(s for s in data["slots"] if s["id"] == slot_id)


def _cursor_bytes(value: int = 0) -> bytes:
    return value.to_bytes(8, "little")


def _state(tick: int = 0, moisture: int = 10, cursor: int = 0) -> StateSlice:
    return StateSlice(
        tick=tick,
        fields={
            "world.terrain.moisture": {
                key: bytes([moisture]) * _CELLS for key in _CHUNKS
            },
            "world.terrain.snow": {
                key: bytes([0]) * _CELLS for key in _CHUNKS
            },
            "world.terrain.ice": {
                key: bytes([0]) * _CELLS for key in _CHUNKS
            },
            _CURSOR_ID: {
                key: _cursor_bytes(cursor) for key in _CHUNKS
            },
        },
    )


class TestDeclarationFile:
    def test_real_declaration_loads(self):
        decl = load_declaration()
        assert isinstance(decl, StateDeclaration)
        assert [slot.id for slot in decl.slots] == [
            "world.clock.tick",
            "world.terrain.moisture",
            "world.terrain.snow",
            "world.terrain.ice",
            "world.terrain.integrated_through",
        ]
        assert decl.space.chunk_size == 200
        assert len(decl.field_slots) == 4

    def test_tick_slot_is_decided(self):
        tick = _slot(_raw(), "world.clock.tick")
        assert tick["permissions"]["intervene"] is False
        assert tick["update"] == {"kind": "driver_frame_advance"}
        assert tick["domain"]["bits"] == 64
        assert tick["domain"]["min"] == 0

    def test_terrain_slots_only_intervene_pending(self):
        for slot_id in _FIELD_IDS:
            slot = _slot(_raw(), slot_id)
            assert slot["update"]["stage"] == "terrain.integrate"
            assert slot["permissions"]["intervene"] is None
            assert set(slot["pending"]) == {"permissions.intervene"}
            assert slot["pending"]["permissions.intervene"]

    def test_slot_scopes(self):
        raw = _raw()
        for slot_id in _CHANNEL_IDS:
            assert _slot(raw, slot_id)["domain"]["scope"] == "tile"
        assert _slot(raw, _CURSOR_ID)["domain"]["scope"] == "chunk"
        assert "scope" not in _slot(raw, "world.clock.tick")["domain"]

    def test_terrain_domains_match_runtime_registry(self):
        from ascend.space.state_defs import STATE_TYPES

        decl = load_declaration()
        channels = [
            slot for slot in decl.field_slots if slot.id in _CHANNEL_IDS
        ]
        assert len(channels) == 3
        for slot in channels:
            key = slot.id.rsplit(".", 1)[-1]
            assert (slot.domain.min, slot.domain.max) == \
                STATE_TYPES[key].bounds

    def test_space_matches_runtime_tile_size(self):
        from ascend.config import TILE_MAP_SIZE

        assert load_declaration().space.chunk_size == TILE_MAP_SIZE

    def test_digest_is_stable_sha256_hex(self):
        digest = load_declaration().digest()
        assert len(digest) == 64
        assert digest == load_declaration().digest()


class TestDeclarationValidation:
    def test_unknown_top_level_field_rejected(self, tmp_path):
        data = _raw()
        data["extra"] = 1
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_unknown_slot_field_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["extra"] = 1
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_schema_version_must_match_loader(self, tmp_path):
        data = _raw()
        data["schema_version"] = 1
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_duplicate_slot_id_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["id"] = "world.terrain.moisture"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_cross_section_duplicate_id_rejected(self, tmp_path):
        data = _raw()
        data["derived"].append({
            "id": "world.clock.tick",
            "recompute": "非法：与槽位重名",
            "cache_ok": True,
            "audit_ref": "§1",
        })
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_missing_required_slot_field_rejected(self, tmp_path):
        data = _raw()
        del _slot(data, "world.terrain.snow")["owner"]
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_invalid_role_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["role"] = "derived"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_invalid_carrier_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["carrier"] = "tile"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_persist_must_be_true_for_state_slot(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["persist"] = False
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_invalid_bits_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["domain"]["bits"] = 7
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_domain_range_exceeding_bits_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["domain"]["max"] = 256
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_min_greater_than_max_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["domain"]["min"] = 10
        _slot(data, "world.terrain.snow")["domain"]["max"] = 5
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_placeholder_intervene_rejected(self, tmp_path):
        data = _raw()
        slot = _slot(data, "world.terrain.snow")
        slot["pending"].pop("permissions.intervene")
        slot["permissions"]["intervene"] = "candidate"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_pending_field_with_value_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["update"]["stage"] = "TBD:#48"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_missing_applicable_field_without_pending_rejected(self, tmp_path):
        data = _raw()
        del _slot(data, "world.terrain.snow")["update"]["stage"]
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_null_intervene_without_pending_rejected(self, tmp_path):
        data = _raw()
        slot = _slot(data, "world.terrain.snow")
        slot["pending"].pop("permissions.intervene")
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_pending_unknown_path_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["pending"]["update.microstep"] = "x"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_pending_empty_reason_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["pending"][
            "permissions.intervene"
        ] = ""
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_pending_stage_for_driver_slot_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.clock.tick")["pending"] = {
            "update.stage": "驱动槽位无机制阶段",
        }
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_external_input_entering_state_rejected(self, tmp_path):
        data = _raw()
        data["external_inputs"][0]["enters_world_state"] = True
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_removed_entry_without_ref_rejected(self, tmp_path):
        data = _raw()
        entry = data["removed"][0]
        entry.pop("audit_ref", None)
        entry.pop("contract_ref", None)
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_removed_entry_empty_reason_rejected(self, tmp_path):
        data = _raw()
        data["removed"][0]["reason"] = ""
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_derived_cache_ok_must_be_bool(self, tmp_path):
        data = _raw()
        data["derived"][0]["cache_ok"] = "yes"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_empty_slots_rejected(self, tmp_path):
        data = _raw()
        data["slots"] = []
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_invalid_space_kind_rejected(self, tmp_path):
        data = _raw()
        data["space"]["kind"] = "scattered"
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_non_positive_chunk_size_rejected(self, tmp_path):
        data = _raw()
        data["space"]["chunk_size"] = 0
        with pytest.raises(DeclarationError):
            load_declaration(_write(tmp_path, data))

    def test_missing_declaration_file_rejected(self, tmp_path):
        with pytest.raises(DeclarationError):
            load_declaration(tmp_path / "absent.json")

    def test_corrupt_json_rejected(self, tmp_path):
        path = tmp_path / "state_slots.json"
        path.write_text("{ not json", encoding="utf-8")
        with pytest.raises(DeclarationError):
            load_declaration(path)


class TestDeclarationDigest:
    def test_digest_ignores_document_refs(self, tmp_path):
        data = _raw()
        data["refs"]["audit"] = "docs/其他审计.md"
        data["refs"]["contract"] = "docs/其他契约.md"
        for entry in data["slots"]:
            entry["audit_ref"] = "§9"
        for section in ("derived", "external_inputs", "records"):
            for entry in data[section]:
                if "audit_ref" in entry:
                    entry["audit_ref"] = "§9"
        data["removed"][0]["audit_ref"] = "§9"
        assert load_declaration(_write(tmp_path, data)).digest() == \
            load_declaration().digest()

    def test_digest_ignores_pending_reason_wording(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["pending"][
            "permissions.intervene"
        ] = "换一种说法"
        assert load_declaration(_write(tmp_path, data)).digest() == \
            load_declaration().digest()

    def test_digest_independent_of_entry_order(self, tmp_path):
        data = _raw()
        data["slots"] = list(reversed(data["slots"]))
        data["derived"] = list(reversed(data["derived"]))
        assert load_declaration(_write(tmp_path, data)).digest() == \
            load_declaration().digest()

    def test_digest_independent_of_json_key_order(self, tmp_path):
        data = json.loads(json.dumps(_raw(), sort_keys=True))
        assert load_declaration(_write(tmp_path, data)).digest() == \
            load_declaration().digest()

    def test_digest_changes_on_semantic_change(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["permissions"]["observe"] = False
        assert load_declaration(_write(tmp_path, data)).digest() != \
            load_declaration().digest()

    def test_digest_changes_on_pending_path_change(self, tmp_path):
        data = _raw()
        slot = _slot(data, "world.terrain.snow")
        del slot["pending"]
        slot["permissions"]["intervene"] = True
        assert load_declaration(_write(tmp_path, data)).digest() != \
            load_declaration().digest()


class TestCanonicalEncoding:
    def test_encoding_is_deterministic(self):
        decl = load_declaration()
        assert encode_state(decl, _state()) == encode_state(decl, _state())

    def test_encoding_ignores_chunk_insertion_order(self):
        decl = load_declaration()
        state = _state()
        reversed_fields = {
            slot_id: dict(reversed(list(chunks.items())))
            for slot_id, chunks in state.fields.items()
        }
        assert encode_state(
            decl,
            StateSlice(tick=state.tick, fields=reversed_fields),
        ) == encode_state(decl, state)

    def test_encoding_ignores_slot_declaration_order(self, tmp_path):
        data = _raw()
        data["slots"] = list(reversed(data["slots"]))
        reordered = load_declaration(_write(tmp_path, data))
        assert encode_state(reordered, _state()) == \
            encode_state(load_declaration(), _state())

    def test_encoding_distinguishes_value_change(self):
        decl = load_declaration()
        changed = _state()
        chunks = {
            key: (bytes([1]) + buf[1:] if key == (0, 0) else buf)
            for key, buf in changed.fields["world.terrain.moisture"].items()
        }
        fields = dict(changed.fields)
        fields["world.terrain.moisture"] = chunks
        assert encode_state(decl, changed) != \
            encode_state(decl, StateSlice(tick=0, fields=fields))

    def test_state_digest_matches_sha256_of_encoding(self):
        decl = load_declaration()
        state = _state()
        assert state_digest(decl, state) == \
            hashlib.sha256(encode_state(decl, state)).hexdigest()

    def test_encoding_accepts_empty_field_set(self):
        decl = load_declaration()
        empty = StateSlice(
            tick=0,
            fields={slot_id: {} for slot_id in _FIELD_IDS},
        )
        assert encode_state(decl, empty)

    def test_encoding_rejects_missing_channel(self):
        decl = load_declaration()
        state = _state()
        fields = dict(state.fields)
        del fields["world.terrain.ice"]
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_encoding_rejects_extra_channel(self):
        decl = load_declaration()
        state = _state()
        fields = dict(state.fields)
        fields["world.terrain.bogus"] = {(0, 0): bytes(_CELLS)}
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_encoding_rejects_channel_chunk_mismatch(self):
        decl = load_declaration()
        state = _state()
        fields = dict(state.fields)
        fields["world.terrain.ice"] = {
            (0, 0): bytes(_CELLS),
            (5, 5): bytes(_CELLS),
        }
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_encoding_rejects_wrong_buffer_length(self):
        decl = load_declaration()
        state = _state()
        fields = dict(state.fields)
        chunks = dict(fields["world.terrain.snow"])
        chunks[(0, 0)] = bytes(_CELLS - 1)
        fields["world.terrain.snow"] = chunks
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_encoding_rejects_out_of_domain_cell(self):
        decl = load_declaration()
        with pytest.raises(DeclarationError):
            encode_state(decl, _state(moisture=101))

    def test_encoding_rejects_tick_below_min(self):
        decl = load_declaration()
        with pytest.raises(DeclarationError):
            encode_state(decl, _state(tick=-1))

    def test_encoding_rejects_bool_tick(self):
        decl = load_declaration()
        with pytest.raises(DeclarationError):
            encode_state(decl, _state(tick=False))

    def test_encoding_rejects_bad_chunk_coord(self):
        decl = load_declaration()
        state = _state()
        fields = dict(state.fields)
        fields["world.terrain.ice"] = {
            (0, 0): bytes(_CELLS),
            (-1, 0): bytes(_CELLS),
        }
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_encoding_rejects_second_global_slot(self, tmp_path):
        data = _raw()
        data["slots"].append({
            "id": "world.clock.epoch",
            "carrier": "global",
            "domain": {
                "type": "int",
                "bits": 64,
                "min": 0,
                "max": 18446744073709551615,
                "unit": "tick",
                "missing": "none",
            },
            "role": "state",
            "update": {"kind": "driver_frame_advance"},
            "permissions": {
                "intervene": False,
                "observe": True,
                "record": True,
            },
            "owner": "world.clock",
            "persist": True,
            "audit_ref": "§1",
        })
        with pytest.raises(DeclarationError):
            encode_state(load_declaration(_write(tmp_path, data)), _state())

    def test_encoding_distinguishes_scalar_cursor(self):
        decl = load_declaration()
        assert encode_state(decl, _state(cursor=0)) != \
            encode_state(decl, _state(cursor=1))

    def test_encoding_rejects_wrong_scalar_width(self):
        decl = load_declaration()
        state = _state()
        fields = dict(state.fields)
        chunks = dict(fields[_CURSOR_ID])
        chunks[(0, 0)] = _cursor_bytes(0)[:7]
        fields[_CURSOR_ID] = chunks
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_encoding_rejects_scalar_out_of_domain(self, tmp_path):
        data = _raw()
        _slot(data, _CURSOR_ID)["domain"]["max"] = 100
        _slot(data, _CURSOR_ID)["domain"]["bits"] = 8
        decl = load_declaration(_write(tmp_path, data))
        state = _state()
        fields = dict(state.fields)
        chunks = dict(fields[_CURSOR_ID])
        chunks[(0, 0)] = bytes([101])
        fields[_CURSOR_ID] = chunks
        with pytest.raises(DeclarationError):
            encode_state(decl, StateSlice(tick=state.tick, fields=fields))

    def test_field_scope_required(self, tmp_path):
        data = _raw()
        del _slot(data, "world.terrain.snow")["domain"]["scope"]
        with pytest.raises(DeclarationError, match="scope"):
            load_declaration(_write(tmp_path, data))

    def test_scope_rejected_on_global_slot(self, tmp_path):
        data = _raw()
        _slot(data, "world.clock.tick")["domain"]["scope"] = "tile"
        with pytest.raises(DeclarationError, match="scope"):
            load_declaration(_write(tmp_path, data))

    def test_unknown_scope_rejected(self, tmp_path):
        data = _raw()
        _slot(data, "world.terrain.snow")["domain"]["scope"] = "region"
        with pytest.raises(DeclarationError, match="scope"):
            load_declaration(_write(tmp_path, data))
