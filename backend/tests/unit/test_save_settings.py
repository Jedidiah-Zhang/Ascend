"""世界设置校验单元测试 — manifest 与当前声明的一致性（fail-closed）。

覆盖 ascend/save/settings.py：声明一致放行、未记录放行（由调用方补写）、
哈希/观测协议版本不一致拒绝、记录残缺（损坏）拒绝。
"""

from __future__ import annotations

import pytest

from ascend.causal.world import ASCEND_MECHANISMS
from ascend.save.manifest import Manifest, SaveFormatError
from ascend.save.settings import validate_world_settings

_SEED = 20260908


class TestWorldSettings:
    """manifest 世界设置校验（声明版本不一致即拒绝加载）。"""

    def _manifest(self, declaration=None) -> Manifest:
        return Manifest(
            name="测试世界", seed=_SEED, world_id="w1",
            mechanism_declaration=declaration,
        )

    def test_matching_declaration_accepted(self):
        settings = ASCEND_MECHANISMS.declaration_settings()
        validate_world_settings(self._manifest(settings), settings)

    def test_absent_declaration_accepted_for_backfill(self):
        """旧存档未记录声明版本：放行，由调用方随后补写。"""
        validate_world_settings(
            self._manifest(None), ASCEND_MECHANISMS.declaration_settings(),
        )

    def test_hash_mismatch_rejected(self):
        settings = ASCEND_MECHANISMS.declaration_settings()
        stored = {**settings, "declaration_hash": "sha256:deadbeef"}
        with pytest.raises(ValueError, match="declaration_hash"):
            validate_world_settings(self._manifest(stored), settings)

    def test_protocol_version_mismatch_rejected(self):
        settings = ASCEND_MECHANISMS.declaration_settings()
        stored = {**settings, "observation_protocol_version": "sha256:0"}
        with pytest.raises(ValueError, match="observation_protocol_version"):
            validate_world_settings(self._manifest(stored), settings)

    def test_partial_declaration_rejected(self):
        """记录存在但字段不全 = 损坏，不是"未记录"。"""
        settings = ASCEND_MECHANISMS.declaration_settings()
        stored = {
            key: value for key, value in settings.items()
            if key != "observation_protocol_version"
        }
        with pytest.raises(ValueError, match="缺少字段"):
            validate_world_settings(self._manifest(stored), settings)

    def test_non_mapping_declaration_rejected(self):
        """manifest 记录被改成字符串等非映射 = 损坏，拒绝。"""
        with pytest.raises(ValueError, match="必须为映射"):
            validate_world_settings(
                self._manifest("sha256:0"),
                ASCEND_MECHANISMS.declaration_settings(),
            )

    def test_incomplete_current_view_rejected(self):
        """调用方传入的声明视图残缺 = 编程错误，显式拒绝而不是静默放行。"""
        with pytest.raises(ValueError, match="声明视图缺少字段"):
            validate_world_settings(self._manifest(None), {"declaration_id": ""})

    def test_declaration_view_has_required_fields(self):
        settings = ASCEND_MECHANISMS.declaration_settings()
        assert settings["declaration_hash"] == \
            ASCEND_MECHANISMS.declaration_hash
        assert settings["declaration_id"] == "ascend.world.scalar_formulas"
        assert settings["observation_protocol_version"].startswith("sha256:")

    def test_manifest_rejects_non_dict_declaration_on_read(self):
        """manifest 读取期就拒绝非对象声明（不等读档校验）。"""
        with pytest.raises(SaveFormatError, match="mechanism_declaration"):
            Manifest.from_dict({
                "name": "x", "seed": 1, "world_id": "w",
                "mechanism_declaration": "not-a-dict",
            })
