"""存档管理器单元测试 — 存档位生命周期、实时状态、快照与管理操作。

覆盖 ascend/save/manager.py 与 manifest.py。
使用 tmp_path 隔离存档根目录。
"""

import json
import os

import pytest

from ascend.save.manager import SaveManager, SNAPSHOT_SUFFIX
from ascend.save.manifest import Manifest, SaveFormatError, MANIFEST_NAME
from ascend.save.crypto import SaveCryptoError


@pytest.fixture()
def manager(tmp_path) -> SaveManager:
    """隔离的存档管理器。"""
    return SaveManager(root=str(tmp_path / "saves"))


@pytest.fixture()
def world(manager: SaveManager) -> str:
    """已创建的存档位 ID。"""
    manifest = manager.create_world("测试世界", seed=12345)
    return manifest.world_id


class TestCreateWorld:
    """存档位创建。"""

    def test_creates_directory_structure(self, manager, world):
        """创建活目录 + 密钥 + manifest + 快照目录。"""
        wdir = manager.world_dir(world)
        assert os.path.isdir(wdir)
        assert os.path.isfile(manager.manifest_path(world))
        assert os.path.isfile(manager.key_path(world))
        assert os.path.isdir(manager.snapshot_dir(world))

    def test_manifest_fields(self, manager, world):
        """manifest 记录名称/种子/ID。"""
        manifest = manager.get_manifest(world)
        assert manifest.name == "测试世界"
        assert manifest.seed == 12345
        assert manifest.world_id == world

    def test_empty_name_rejected(self, manager):
        """空名称拒绝创建。"""
        with pytest.raises(ValueError):
            manager.create_world("", seed=1)

    def test_duplicate_world_id_rejected(self, manager, world):
        """重复 world_id 拒绝创建。"""
        with pytest.raises(FileExistsError):
            manager.create_world("另一个", seed=2, world_id=world)

    def test_list_worlds_summary(self, manager, world):
        """列表摘要含关键字段与快照计数。"""
        manager.create_world("第二个", seed=7)
        worlds = manager.list_worlds()
        assert len(worlds) == 2
        by_id = {w["world_id"]: w for w in worlds}
        entry = by_id[world]
        assert entry["name"] == "测试世界"
        assert entry["seed"] == 12345
        assert entry["snapshot_count"] == 0

    def test_list_skips_corrupted_manifest(self, manager, world):
        """损坏的 manifest 被跳过，不影响列表。"""
        bad_id = manager.create_world("坏档", seed=1).world_id
        with open(manager.manifest_path(bad_id), "w", encoding="utf-8") as f:
            f.write("{broken")
        worlds = manager.list_worlds()
        ids = [w["world_id"] for w in worlds]
        assert world in ids
        assert bad_id not in ids

    def test_unknown_world_manifest_raises(self, manager):
        """不存在的世界报 SaveFormatError。"""
        with pytest.raises(SaveFormatError):
            manager.get_manifest("nope")

    def test_format_version_mismatch_rejected(self, manager, world):
        """format_version 不兼容时拒绝加载。"""
        path = manager.manifest_path(world)
        data = json.load(open(path, encoding="utf-8"))
        data["format_version"] = 999
        json.dump(data, open(path, "w", encoding="utf-8"))
        with pytest.raises(SaveFormatError):
            manager.get_manifest(world)


class TestStateIO:
    """实时状态读写。"""

    def test_write_read_roundtrip(self, manager, world):
        """state 加密写入后可解密读回。"""
        state = {
            "clock": {"time": 1728000, "speed": 1.0, "paused": False},
            "player": {"entity_id": None, "x": 100.0, "y": 200.0},
            "weather": {"seed": 12345},
            "archive_max_timestamp": 1728000,
        }
        manager.write_state(world, state)
        assert manager.read_state(world) == state

    def test_state_file_scrambled(self, manager, world):
        """state 文件内容不含明文（防直读）。"""
        manager.write_state(world, {"player": {"x": 123.456}})
        raw = open(manager.state_path(world), "rb").read()
        assert b"123.456" not in raw

    def test_tampered_state_rejected(self, manager, world):
        """篡改 state 文件导致解密失败。"""
        manager.write_state(world, {"clock": {"time": 1}})
        path = manager.state_path(world)
        data = bytearray(open(path, "rb").read())
        data[-1] ^= 0xFF
        with open(path, "wb") as f:
            f.write(bytes(data))
        with pytest.raises(SaveCryptoError):
            manager.read_state(world)

    def test_missing_state_raises(self, manager, world):
        """缺失 state 报 SaveFormatError。"""
        with pytest.raises(SaveFormatError):
            manager.read_state(world)

    def test_rename_world(self, manager, world):
        """重命名生效。"""
        manager.rename_world(world, "新名字")
        assert manager.get_manifest(world).name == "新名字"


class TestSnapshot:
    """手动快照。"""

    def test_create_snapshot_file(self, manager, world):
        """快照文件生成于 snapshots/ 目录并列入列表。"""
        filename = manager.create_snapshot(world, suffix="manual")
        assert filename.endswith(SNAPSHOT_SUFFIX)
        assert "-manual" in filename
        snapshots = manager.list_snapshots(world)
        assert [s["file"] for s in snapshots] == [filename]
        assert snapshots[0]["suffix"] == "manual"

    def test_snapshot_is_encrypted(self, manager, world, tmp_path):
        """快照内容不含明文（加密打包）。"""
        manager.write_state(world, {"player": {"x": 42.0}})
        filename = manager.create_snapshot(world)
        raw = open(
            os.path.join(manager.snapshot_dir(world), filename), "rb"
        ).read()
        assert b"42.0" not in raw

    def test_extract_restores_state(self, manager, world):
        """快照展开后 state 与原一致（回滚核心语义）。"""
        original = {
            "clock": {"time": 100, "speed": 1.0, "paused": False},
            "player": {"entity_id": "abc", "x": 1.5, "y": 2.5},
            "weather": {"seed": 1},
            "archive_max_timestamp": 99,
        }
        manager.write_state(world, original)
        filename = manager.create_snapshot(world)
        snapshot_path = os.path.join(manager.snapshot_dir(world), filename)

        # 展开后读 state 应一致
        restored_id = manager.extract_snapshot(snapshot_path)
        assert restored_id == world
        assert manager.read_state(world) == original

    def test_extract_overwrites_live_dir(self, manager, world):
        """展开覆盖活目录内容（旧内容被替换）。"""
        manager.write_state(world, {"version": "old"})
        filename = manager.create_snapshot(world)
        snapshot_path = os.path.join(manager.snapshot_dir(world), filename)

        # 回滚前把活目录改成"新分支"
        manager.write_state(world, {"version": "new-branch"})
        manager.extract_snapshot(snapshot_path)
        assert manager.read_state(world) == {"version": "old"}

    def test_extract_snapshot_state_preview(self, manager, world):
        """读快照内的 state 用于回滚前预览。"""
        manager.write_state(world, {"clock": {"time": 777}})
        filename = manager.create_snapshot(world)
        snapshot_path = os.path.join(manager.snapshot_dir(world), filename)
        state = manager.read_snapshot_state(snapshot_path)
        assert state["clock"]["time"] == 777

    def test_tampered_snapshot_rejected(self, manager, world):
        """篡改快照文件被拒绝（防篡改）。"""
        filename = manager.create_snapshot(world)
        path = os.path.join(manager.snapshot_dir(world), filename)
        data = bytearray(open(path, "rb").read())
        data[len(data) // 2] ^= 0xFF
        with open(path, "wb") as f:
            f.write(bytes(data))
        with pytest.raises(SaveCryptoError):
            manager.extract_snapshot(path)

    def test_path_traversal_blocked(self, manager, world):
        """zip 内恶意路径（../）不会逃出活目录。"""
        import zipfile
        import io
        from ascend.save.crypto import SaveKeys

        # 构造恶意快照：条目名为 ../evil.txt
        wdir = manager.world_dir(world)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as zf:
            zf.writestr("../evil.txt", "pwned")
            zf.writestr(MANIFEST_NAME, json.dumps({
                "format_version": 1, "name": "x", "seed": 1,
                "world_id": world,
            }))
        keys = SaveKeys.generate()
        key_dict = keys.to_dict()
        header = json.dumps({
            "format": "ascendsave", "version": 1,
            "fernet_key": key_dict["fernet_key"],
            "sign_key": key_dict["sign_key"],
        }).encode() + b"\n"
        malicious = os.path.join(manager.snapshot_dir(world), "evil.ascendsave")
        with open(malicious, "wb") as f:
            f.write(header)
            f.write(keys.encrypt(buffer.getvalue()))

        manager.extract_snapshot(malicious)
        # 没有逃逸：saves 根目录下无 evil.txt
        assert not os.path.exists(os.path.join(manager.root, "evil.txt"))
        assert not os.path.exists(os.path.join(manager.live_dir(), "evil.txt"))

    def test_snapshot_kept_after_extract(self, manager, world):
        """快照文件本身在回滚后保留（可反复回滚）。"""
        filename = manager.create_snapshot(world)
        path = os.path.join(manager.snapshot_dir(world), filename)
        manager.extract_snapshot(path)
        assert os.path.isfile(path)


class TestWorldOps:
    """世界级管理操作。"""

    def test_delete_world(self, manager, world):
        """删除整个世界目录。"""
        manager.delete_world(world)
        assert not os.path.isdir(manager.world_dir(world))
        with pytest.raises(SaveFormatError):
            manager.get_manifest(world)

    def test_delete_unknown_raises(self, manager):
        """删除不存在的世界报错。"""
        with pytest.raises(SaveFormatError):
            manager.delete_world("ghost")

    def test_export_creates_independent_copy(self, manager, world):
        """导出复制为独立新世界，互不影响。"""
        manager.write_state(world, {"clock": {"time": 5}})
        new_id = manager.export_world(world)
        assert new_id != world
        # 副本内容一致
        assert manager.read_state(new_id) == {"clock": {"time": 5}}
        assert manager.get_manifest(new_id).seed == 12345
        # 修改副本不影响原档
        manager.write_state(new_id, {"clock": {"time": 999}})
        assert manager.read_state(world) == {"clock": {"time": 5}}

    def test_export_includes_snapshots(self, manager, world):
        """导出的世界携带原快照。"""
        manager.create_snapshot(world)
        new_id = manager.export_world(world)
        assert len(manager.list_snapshots(new_id)) == 1

    def test_manifest_touch_updates_info(self, manager, world):
        """touch 更新游戏时间与时长（存档选择页数据源）。"""
        path = manager.manifest_path(world)
        manifest = manager.get_manifest(world)
        manifest.touch(path, game_time=500, play_duration_sec=120.5)
        loaded = manager.get_manifest(world)
        assert loaded.game_time == 500
        assert loaded.play_duration_sec == 120.5
