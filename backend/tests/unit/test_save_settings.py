"""世界设置校验单元测试 — manifest 与当前声明的一致性（fail-closed）。

覆盖 ascend/save/settings.py：声明一致放行、未记录放行（由调用方补写）、
哈希/观测协议版本不一致拒绝、记录残缺（损坏）拒绝。
"""

from __future__ import annotations

import pytest

from ascend.world.assembly import build_game_program
from ascend.save.manifest import Manifest, SaveFormatError
from ascend.save.settings import validate_world_program, validate_world_settings

_SEED = 20260908


class TestWorldSettings:
    """manifest 世界设置校验（声明版本不一致即拒绝加载）。"""

    def _manifest(self, declaration=None) -> Manifest:
        return Manifest(
            name="测试世界", seed=_SEED, world_id="w1",
            mechanism_declaration=declaration,
        )

    def test_matching_declaration_accepted(self):
        settings = build_game_program().declaration_settings()
        validate_world_settings(self._manifest(settings), settings)

    def test_absent_declaration_accepted_for_backfill(self):
        """旧存档未记录声明版本：放行，由调用方随后补写。"""
        validate_world_settings(
            self._manifest(None), build_game_program().declaration_settings(),
        )

    def test_hash_mismatch_rejected(self):
        settings = build_game_program().declaration_settings()
        stored = {**settings, "declaration_hash": "sha256:deadbeef"}
        with pytest.raises(ValueError, match="declaration_hash"):
            validate_world_settings(self._manifest(stored), settings)

    def test_protocol_version_mismatch_rejected(self):
        settings = build_game_program().declaration_settings()
        stored = {**settings, "observation_protocol_version": "sha256:0"}
        with pytest.raises(ValueError, match="observation_protocol_version"):
            validate_world_settings(self._manifest(stored), settings)

    def test_partial_declaration_rejected(self):
        """记录存在但字段不全 = 损坏，不是"未记录"。"""
        settings = build_game_program().declaration_settings()
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
                build_game_program().declaration_settings(),
            )

    def test_incomplete_current_view_rejected(self):
        """调用方传入的声明视图残缺 = 编程错误，显式拒绝而不是静默放行。"""
        with pytest.raises(ValueError, match="声明视图缺少字段"):
            validate_world_settings(self._manifest(None), {"declaration_id": ""})

    def test_declaration_view_has_required_fields(self):
        settings = build_game_program().declaration_settings()
        assert settings["declaration_hash"].startswith("sha256:")
        assert settings["declaration_id"] == "world-arch-v0.1"
        assert settings["observation_protocol_version"].startswith("sha256:")

    def test_manifest_rejects_non_dict_declaration_on_read(self):
        """manifest 读取期就拒绝非对象声明（不等读档校验）。"""
        with pytest.raises(SaveFormatError, match="mechanism_declaration"):
            Manifest.from_dict({
                "name": "x", "seed": 1, "world_id": "w",
                "mechanism_declaration": "not-a-dict",
            })


class TestWorldProgram:
    """manifest 世界程序身份校验（不一致即拒绝加载）。"""

    def _program(self) -> dict:
        return build_game_program().settings()

    def _manifest(self, program=None) -> Manifest:
        return Manifest(
            name="测试世界", seed=_SEED, world_id="w1",
            world_program=program,
        )

    def test_matching_program_accepted(self):
        view = self._program()
        validate_world_program(self._manifest(view), view)

    def test_absent_program_accepted_for_backfill(self):
        """旧存档未记录程序身份：放行，由调用方随后补写。"""
        validate_world_program(self._manifest(None), self._program())

    def test_identity_mismatch_rejected(self):
        view = self._program()
        stored = {**view, "identity": "sha256:" + "00" * 32}
        with pytest.raises(ValueError, match="世界程序身份"):
            validate_world_program(self._manifest(stored), view)

    def test_partial_program_rejected(self):
        """记录存在但缺 identity = 损坏，不是"未记录"。"""
        view = self._program()
        with pytest.raises(ValueError, match="缺少字段"):
            validate_world_program(self._manifest({"schema_version": 1}), view)

    def test_non_mapping_program_rejected(self):
        with pytest.raises(ValueError, match="必须为映射"):
            validate_world_program(
                self._manifest("sha256:0"), self._program(),
            )

    def test_incomplete_current_view_rejected(self):
        with pytest.raises(ValueError, match="世界程序视图缺少字段"):
            validate_world_program(self._manifest(None), {"schema_version": 1})

    def test_program_view_matches_game_program(self):
        program = build_game_program()
        view = program.settings()
        assert view["identity"] == program.identity
        assert view["contract"] == program.contract
        assert view["kernel"] == program.kernel
        assert view["module_digests"] == dict(program.module_digests)

    def test_manifest_rejects_non_dict_program_on_read(self, tmp_path):
        """manifest 读取期就拒绝非对象程序视图（不等读档校验）。"""
        manifest = self._manifest(None)
        data = manifest.dict
        data["world_program"] = "sha256:0"
        import json

        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        with pytest.raises(SaveFormatError, match="world_program"):
            Manifest.read(str(path))

    def test_manifest_roundtrip_program(self, tmp_path):
        """world_program 随 manifest 读写往返。"""
        import json

        view = self._program()
        path = tmp_path / "manifest.json"
        path.write_text(
            json.dumps(self._manifest(view).dict, ensure_ascii=False),
            encoding="utf-8",
        )
        loaded = Manifest.read(str(path))
        assert loaded.world_program == view
