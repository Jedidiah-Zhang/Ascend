"""存档管理器单元测试 — 存档位生命周期、实时状态、快照与管理操作。

覆盖 ascend/save/manager.py 与 manifest.py。
使用 tmp_path 隔离存档根目录。
"""

import json
import os

import pytest

from ascend.save.manager import SaveManager, SNAPSHOT_SUFFIX
from ascend.save.manifest import Manifest, SaveFormatError, MANIFEST_NAME
from ascend.save.crypto import SaveCryptoError, SaveKeys


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
        """创建世界目录 + manifest + 快照子目录（密钥藏于 manifest）。"""
        wdir = manager.world_dir(world)
        assert os.path.isdir(wdir)
        assert os.path.isfile(manager.manifest_path(world))
        assert os.path.isdir(manager.snapshot_dir(world))
        # 密钥不落盘独立文件，藏于 manifest.secrets_blob
        assert manager.get_manifest(world).secrets_blob is not None
        # 快照是世界的子目录，活文件与快照同一目录
        assert os.path.dirname(manager.snapshot_dir(world)) == wdir

    def test_world_dir_is_one_directory_per_world(self, manager, world):
        """一个存档位 = 一个目录（活文件 + snapshots/ 同根）。"""
        wdir = manager.world_dir(world)
        entries = sorted(os.listdir(wdir))
        assert "manifest.json" in entries
        assert "key.json" not in entries
        assert "snapshots" in entries

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


class TestNameUniqueness:
    """存档名称唯一性（创建/重命名拒绝重名，复制自动副本后缀）。"""

    def test_create_duplicate_name_rejected(self, manager):
        """重名创建拒绝。"""
        manager.create_world("我的世界", seed=1)
        with pytest.raises(ValueError, match="已存在"):
            manager.create_world("我的世界", seed=2)

    def test_create_whitespace_name_rejected(self, manager):
        """纯空白名称拒绝（去空白后为空）。"""
        with pytest.raises(ValueError, match="不能为空"):
            manager.create_world("   ", seed=1)

    def test_rename_to_existing_rejected(self, manager):
        """重命名为已有名称拒绝。"""
        a = manager.create_world("世界A", seed=1).world_id
        manager.create_world("世界B", seed=2)
        with pytest.raises(ValueError, match="已存在"):
            manager.rename_world(a, "世界B")

    def test_rename_keeps_own_name_allowed(self, manager):
        """保持自身名称（未改名）允许。"""
        a = manager.create_world("世界A", seed=1).world_id
        manager.rename_world(a, "世界A")  # 不抛异常

    def test_rename_to_free_name_allowed(self, manager):
        """重命名为空闲名称成功。"""
        a = manager.create_world("世界A", seed=1).world_id
        manager.rename_world(a, "新名称")
        assert manager.get_manifest(a).name == "新名称"

    def test_export_gets_copy_suffix(self, manager):
        """复制档名称自动追加"副本"后缀（原档仍占用原名称）。"""
        a = manager.create_world("我的世界", seed=1).world_id
        new_id = manager.export_world(a)
        assert manager.get_manifest(new_id).name == "我的世界 副本"
        assert manager.get_manifest(a).name == "我的世界"

    def test_export_second_copy_increments_suffix(self, manager):
        """多次复制时副本后缀递增编号。"""
        a = manager.create_world("我的世界", seed=1).world_id
        c1 = manager.export_world(a)
        c2 = manager.export_world(a)
        assert manager.get_manifest(c1).name == "我的世界 副本"
        assert manager.get_manifest(c2).name == "我的世界 副本 2"
        assert len(manager.list_worlds()) == 3


class TestSeedZero:
    """种子创建时定案（P0 回归：seed=0 密钥身份失配）。"""

    def test_seed_zero_randomized_at_create(self, manager):
        """seed=0（随机占位）在创建时随机化，manifest 出生即一致。"""
        manifest = manager.create_world("随机种子", seed=0)
        assert 1 <= manifest.seed <= 2**31 - 1
        # 密钥混淆层与 manifest 身份一致：state 可正常读写
        manager.write_state(manifest.world_id, {"clock": {"time": 5}})
        assert manager.read_state(manifest.world_id)["clock"]["time"] == 5

    def test_explicit_seed_preserved(self, manager):
        """显式种子原样保留，state 可正常读写。"""
        manifest = manager.create_world("固定种子", seed=12345)
        assert manifest.seed == 12345
        manager.write_state(manifest.world_id, {"clock": {"time": 1}})
        assert manager.read_state(manifest.world_id)["clock"]["time"] == 1

    def test_rekey_migrates_legacy_seed_zero(self, manager):
        """旧版 seed=0 存档（创建时未随机化）：rekey 迁移后可用。"""
        world_id = "legacy-world"
        wdir = manager.world_dir(world_id)
        os.makedirs(wdir)
        os.makedirs(manager.snapshot_dir(world_id))
        manifest = Manifest(name="旧档", seed=0, world_id=world_id)
        manifest.secrets_blob = SaveKeys.generate().protect(world_id, 0)
        manifest.write(manager.manifest_path(world_id))
        # 旧行为复现：seed 变更但未重混淆 → state 加解密失配
        stale = manager.get_manifest(world_id)
        stale.seed = 999
        stale.write(manager.manifest_path(world_id))
        with pytest.raises(SaveCryptoError):
            manager.write_state(world_id, {"clock": {"time": 5}})
        # rekey：用旧 seed 解出、新 seed 重混淆
        manager.rekey(world_id, old_seed=0, new_seed=999)
        assert manager.get_manifest(world_id).seed == 999
        manager.write_state(world_id, {"clock": {"time": 5}})
        assert manager.read_state(world_id)["clock"]["time"] == 5

    def test_rekey_wrong_old_seed_rejected(self, manager):
        """旧 seed 不对时 rekey 拒绝（存档身份不符）。"""
        world_id = manager.create_world("世界", seed=7).world_id
        with pytest.raises(SaveCryptoError):
            manager.rekey(world_id, old_seed=0, new_seed=999)


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
        assert not os.path.exists(os.path.join(manager.world_dir(world), "evil.txt"))

    def test_snapshot_kept_after_extract(self, manager, world):
        """快照文件本身在回滚后保留（可反复回滚）。"""
        filename = manager.create_snapshot(world)
        path = os.path.join(manager.snapshot_dir(world), filename)
        manager.extract_snapshot(path)
        assert os.path.isfile(path)

    def test_same_second_snapshots_do_not_collide(self, manager, world):
        """同一秒内多次快照互不覆盖（文件名唯一化）。"""
        f1 = manager.create_snapshot(world, suffix="manual")
        f2 = manager.create_snapshot(world, suffix="manual")
        assert f1 != f2
        assert len(manager.list_snapshots(world)) == 2

    def test_snapshot_after_checkpoint_keeps_wal_data(self, manager, world, tmp_path):
        """WAL checkpoint 后打包：快照内 chunk/事件数据完整（回归 #P0-1）。

        复现引擎 snapshot_current 的顺序：flush → checkpoint → 打包；
        若缺 checkpoint，WAL 模式拷贝的 .db 会丢失全部数据。
        """
        import sqlite3

        from ascend.space import BiomeType, ClimateZone, WeatherParams
        from ascend.space.chunk import ChunkData
        from ascend.space.chunk_store import ChunkStore
        from ascend.space.tile_grid import TileGrid
        from ascend.world_tree.archive import EventArchive
        from ascend.world_tree.event import Event
        from ascend.world_tree.affected import AffectedParty

        # 引擎运行中：两库均打开并已有数据
        cs = ChunkStore(manager.chunks_db_path(world))
        ar = EventArchive(manager.events_db_path(world))
        try:
            for cx, cy in [(0, 0), (1, 0)]:
                chunk = ChunkData(
                    cx=cx, cy=cy,
                    biome=BiomeType.TEMPERATE_MIXED_FOREST,
                    climate_zone=ClimateZone.TEMPERATE_FOREST,
                    annual_baseline=WeatherParams(15.0, 800.0, 12.0, 100.0, 60.0, 5.0),
                )
                chunk.generate_tiles(TileGrid())
                cs.put(chunk)
                cs.mark_dirty(cx, cy)
            cs.flush_dirty()
            ar.archive([
                Event(
                    timestamp=100 + i, location=(0, 0, None, None),
                    initiator_type="system", initiator_id="t",
                    event_type="test", weight=1,
                    affected=[AffectedParty("t", "subject")],
                )
                for i in range(10)
            ])
            # 快照前 checkpoint（snapshot_current 语义）
            cs.checkpoint()
            ar.checkpoint()
            filename = manager.create_snapshot(world, suffix="manual")
        finally:
            cs.close()
            ar.close()

        # 回滚到全新存档根，验证数据完整
        new_root = tmp_path / "fresh"
        mgr2 = SaveManager(root=str(new_root))
        snapshot_path = os.path.join(manager.snapshot_dir(world), filename)
        restored_id = mgr2.extract_snapshot(snapshot_path)
        assert restored_id == world
        cs2 = ChunkStore(mgr2.chunks_db_path(world))
        ar2 = EventArchive(mgr2.events_db_path(world))
        try:
            assert cs2.contains_tiles(0, 0)
            assert cs2.contains_tiles(1, 0)
            assert ar2.event_count() == 10
        finally:
            cs2.close()
            ar2.close()

    def test_extract_snapshot_world_id_override(self, manager, world, tmp_path):
        """复制存档的快照回滚须以目标 world_id 覆盖（回归 #P0-2）。"""
        manager.write_state(world, {"clock": {"time": 100, "speed": 1.0,
                                              "paused": False}, "player": {},
                                    "archive_max_timestamp": 0})
        filename = manager.create_snapshot(world, suffix="manual")
        new_id = manager.export_world(world)
        copied_snapshot = os.path.join(
            manager.snapshot_dir(new_id), filename
        )
        # 不覆盖 → 仍指向原世界（回滚会动原世界，属危险用法）
        assert manager.extract_snapshot(copied_snapshot) == world
        # 显式覆盖 → 展开为复制档
        assert manager.extract_snapshot(
            copied_snapshot, world_id=new_id,
        ) == new_id
        manifest = manager.get_manifest(new_id)
        assert manifest.world_id == new_id

    def test_export_skips_junk_files(self, manager, world):
        """导出只复制规范文件，排除 -wal/-shm/.tmp 残留。"""
        wdir = manager.world_dir(world)
        for junk in ("chunks.db-wal", "chunks.db-shm", "state.json.enc.tmp"):
            with open(os.path.join(wdir, junk), "w", encoding="utf-8") as f:
                f.write("junk")
        new_id = manager.export_world(world)
        files = os.listdir(manager.world_dir(new_id))
        assert "chunks.db-wal" not in files
        assert "chunks.db-shm" not in files
        assert "state.json.enc.tmp" not in files
        assert "manifest.json" in files
        assert "key.json" not in files


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


class TestSecretsInManifest:
    """密钥藏于 manifest.secrets_blob。"""

    def test_keys_stored_in_manifest_not_file(self, manager, world):
        """写读状态使用 manifest 内密钥，无独立密钥文件。"""
        manager.write_state(world, {"clock": {"time": 7}})
        entries = sorted(os.listdir(manager.world_dir(world)))
        assert "key.json" not in entries
        assert manager.read_state(world) == {"clock": {"time": 7}}

    def test_manifest_secrets_blob_not_plaintext(self, manager, world):
        """manifest 里的密钥是混淆串，不暴露明文密钥。"""
        blob = manager.get_manifest(world).secrets_blob
        assert blob is not None
        assert "fernet_key" not in blob
        assert "sign_key" not in blob

    def test_export_reprotects_secrets(self, manager, world):
        """导出副本用新 world_id 重新混淆密钥，可正常读写。"""
        manager.write_state(world, {"clock": {"time": 5}})
        new_id = manager.export_world(world)
        assert manager.read_state(new_id) == {"clock": {"time": 5}}
        manager.write_state(new_id, {"clock": {"time": 999}})
        assert manager.read_state(new_id) == {"clock": {"time": 999}}
        assert manager.read_state(world) == {"clock": {"time": 5}}

    def test_snapshot_roundtrip_with_manifest_secrets(self, manager, world, tmp_path):
        """快照内密钥从 manifest 解出（无需 key.json）。"""
        manager.write_state(world, {"clock": {"time": 100, "speed": 1.0,
                                              "paused": False}, "player": {},
                                    "archive_max_timestamp": 0})
        filename = manager.create_snapshot(world, suffix="manual")
        snapshot_path = os.path.join(manager.snapshot_dir(world), filename)
        state = manager.read_snapshot_state(snapshot_path)
        assert state["clock"]["time"] == 100
        new_root = tmp_path / "fresh"
        mgr2 = SaveManager(root=str(new_root))
        restored = mgr2.extract_snapshot(snapshot_path)
        assert restored == world
        assert mgr2.read_state(world)["clock"]["time"] == 100
