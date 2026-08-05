"""存档管理器单元测试 — 存档位生命周期、实时状态、快照与管理操作。

覆盖 ascend/save/manager.py 与 manifest.py。
使用 tmp_path 隔离存档根目录。
"""

import json
import os

import pytest

from ascend.save.manager import (
    SaveManager, SNAPSHOT_SUFFIX, AUTO_SNAPSHOT_KEEP, QUIT_SNAPSHOT_KEEP,
)
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


class TestLineage:
    """快照血缘（时间线分叉元数据）。"""

    def test_lineage_records_parent_and_game_time(self, manager, world):
        """创建快照记录血缘：parent=活目录来源，game_time 取自 state。"""
        manager.write_state(world, {"clock": {"time": 500}})
        filename = manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == filename, "活目录来源应更新为最新快照"
        entry = lineage["snapshots"][filename]
        assert entry["parent"] == ""
        assert entry["game_time"] == 500

    def test_consecutive_snapshots_chain(self, manager, world):
        """连续保存自动串链：后一个快照从最近一个派生（非世界初始）。

        回归：旧实现 live_origin 仅在回滚时更新，连续快照的 parent
        全为 ""（都挂在世界初始上），无法体现「从最近手动存档派生」。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        s2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        manager.write_state(world, {"clock": {"time": 300}})
        s3 = manager.create_snapshot(world, suffix="manual", game_time=300)
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == s3
        assert lineage["snapshots"][s1]["parent"] == ""
        assert lineage["snapshots"][s2]["parent"] == s1, "S2 应从 S1 派生"
        assert lineage["snapshots"][s3]["parent"] == s2, "S3 应从 S2 派生"

    def test_lineage_game_time_param_overrides(self, manager, world):
        """引擎路径显式传 game_time（比周期 state 更准）。"""
        manager.write_state(world, {"clock": {"time": 10}})
        filename = manager.create_snapshot(world, game_time=999)
        entry = manager.snapshot_lineage(world)["snapshots"][filename]
        assert entry["game_time"] == 999

    def test_extract_sets_live_origin(self, manager, world):
        """回滚后活目录来源 = 回滚目标快照。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        path = os.path.join(manager.snapshot_dir(world), snap)
        manager.extract_snapshot(path)
        assert manager.snapshot_lineage(world)["live_origin"] == snap

    def test_extract_accepts_bare_filename(self, manager, world):
        """裸文件名（协议 save_load 下发形式）应从目标世界快照目录解析。

        回归：旧实现直接按路径 open，裸文件名 FileNotFoundError，
        前端回滚请求整体失败（引擎回退服务模式）。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        manager.extract_snapshot(snap, world_id=world)
        assert manager.snapshot_lineage(world)["live_origin"] == snap

    def test_fork_parent_is_rollback_target(self, manager, world):
        """回滚后保存的新快照 parent = 回滚目标（分叉语义）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap_a = manager.create_snapshot(world, suffix="manual")
        path = os.path.join(manager.snapshot_dir(world), snap_a)
        manager.extract_snapshot(path)
        # 回滚后继续玩，再保存 → 新快照从 snap_a 派生
        manager.write_state(world, {"clock": {"time": 150}})
        snap_b = manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][snap_b]["parent"] == snap_a
        assert lineage["live_origin"] == snap_b, "保存后活目录来源 = 最新快照"

    def test_lineage_survives_extract(self, manager, world):
        """回滚展开不丢血缘上下文（原快照条目保留）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap_a = manager.create_snapshot(world, suffix="manual")
        manager.write_state(world, {"clock": {"time": 200}})
        manager.create_snapshot(world, suffix="manual")
        path = os.path.join(manager.snapshot_dir(world), snap_a)
        manager.extract_snapshot(path)
        lineage = manager.snapshot_lineage(world)
        assert len(lineage["snapshots"]) == 2, "回滚不应丢原血缘条目"
        assert lineage["live_origin"] == snap_a

    def test_export_copies_lineage(self, manager, world):
        """复制存档携带血缘（副本时间线上下文完整）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        filename = manager.create_snapshot(world, suffix="manual")
        new_id = manager.export_world(world)
        lineage = manager.snapshot_lineage(new_id)
        assert len(lineage["snapshots"]) == 1
        assert lineage["live_origin"] == filename


    def test_lineage_seq_monotonic(self, manager, world):
        """血缘 seq 单调递增（权威排序键，与游戏时间倒退无关）。"""
        manager.write_state(world, {"clock": {"time": 300}})
        s1 = manager.create_snapshot(world, suffix="manual", game_time=300)
        manager.write_state(world, {"clock": {"time": 100}})  # 回滚后时间倒退
        s2 = manager.create_snapshot(world, suffix="manual", game_time=100)
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][s1]["seq"] == 0
        assert lineage["snapshots"][s2]["seq"] == 1, "seq 反映创建顺序而非游戏时间"

    def test_lineage_migrates_legacy_entries(self, manager, world):
        """旧版血缘条目（无 seq）按 (saved_at, game_time) 合成。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        for entry in lineage["snapshots"].values():
            entry.pop("seq", None)
        lineage["snapshots"][s2]["saved_at"] = 1.0  # 改写为最早
        with open(manager.lineage_path(world), "w", encoding="utf-8") as f:
            json.dump(lineage, f, ensure_ascii=False, indent=2)
        migrated = manager.snapshot_lineage(world)
        assert migrated["snapshots"][s2]["seq"] == 0
        assert migrated["snapshots"][s1]["seq"] == 1, "s2 更早所以编号在前"

    def test_lineage_migration_skips_modern_entries(self, manager, world):
        """已含 seq 的条目不被迁移重排。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        manager.write_state(world, {"clock": {"time": 200}})
        s2 = manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        lineage["snapshots"]["@2030-01-01-000000-aaaaaa-manual.ascendsave"] = {
            "parent": "", "game_time": 50, "saved_at": 1.0,
        }
        with open(manager.lineage_path(world), "w", encoding="utf-8") as f:
            json.dump(lineage, f, ensure_ascii=False, indent=2)
        migrated = manager.snapshot_lineage(world)
        assert migrated["snapshots"][s1]["seq"] == 0, "现代条目编号不动"
        assert migrated["snapshots"][s2]["seq"] == 1
        assert migrated["snapshots"]["@2030-01-01-000000-aaaaaa-manual.ascendsave"]["seq"] == 2, \
            "仅旧条目接续编号"


class TestSnapshotDelete:
    """快照删除与血缘重接（子树提升）。"""

    def test_delete_reparents_children(self, manager, world):
        """删除中间节点：子节点 parent 提升为被删节点的 parent。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        s3 = manager.create_snapshot(world, suffix="manual")
        manager.delete_snapshot(world, s2)
        lineage = manager.snapshot_lineage(world)
        assert s2 not in lineage["snapshots"], "血缘条目随文件删除"
        assert lineage["snapshots"][s3]["parent"] == s1, "子节点重接到祖父"
        assert os.path.isfile(os.path.join(manager.snapshot_dir(world), s1))
        assert not os.path.isfile(os.path.join(manager.snapshot_dir(world), s2))
        assert os.path.isfile(os.path.join(manager.snapshot_dir(world), s3))

    def test_delete_live_origin_falls_back_to_parent(self, manager, world):
        """删除 live_origin：来源回退到其 parent（"" = 世界初始）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        manager.delete_snapshot(world, s2)
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == s1

    def test_delete_root_live_origin(self, manager, world):
        """删除作为链头的 live_origin：来源回退到世界初始。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        manager.delete_snapshot(world, s1)
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == ""

    def test_delete_unknown_tolerated(self, manager, world):
        """删除不存在的快照不报错（容忍外部删文件后的清理）。"""
        manager.delete_snapshot(world, "@2020-01-01-000000-000000-manual.ascendsave")

    def test_delete_chain_keeps_consistency(self, manager, world):
        """连续删除多个节点后血缘仍满足 parent ∈ 存活集 or ''。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        s3 = manager.create_snapshot(world, suffix="manual")
        s4 = manager.create_snapshot(world, suffix="manual")
        manager.delete_snapshot(world, s1)
        manager.delete_snapshot(world, s3)
        lineage = manager.snapshot_lineage(world)
        for name, entry in lineage["snapshots"].items():
            assert entry["parent"] in lineage["snapshots"] or entry["parent"] == "", \
                f"血缘自洽被破坏: {name} → {entry['parent']}"
        assert lineage["snapshots"][s2]["parent"] == ""
        assert lineage["snapshots"][s4]["parent"] == s2, "s3 删除后 s4 重接到 s2"


class TestSnapshotPrune:
    """保留策略（auto 环形 / quit 保留最近 / manual 永久）。"""

    def test_prune_auto_ring_keeps_newest(self, manager, world):
        """auto 快照超量时环形淘汰最旧的（live_origin 额外保护 → N+1）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        created = []
        for i in range(AUTO_SNAPSHOT_KEEP + 5):
            created.append(manager.create_snapshot(world, suffix="auto"))
        remaining = manager.list_snapshots(world)
        files = [s["file"] for s in remaining]
        assert len(files) == AUTO_SNAPSHOT_KEEP + 1, \
            "live_origin（最新自动快照）额外保护"
        assert set(files) == set(created[-(AUTO_SNAPSHOT_KEEP + 1):]), \
            "保留最近 N+1 个（淘汰最旧的）"
        lineage = manager.snapshot_lineage(world)
        for name, entry in lineage["snapshots"].items():
            assert entry["parent"] in lineage["snapshots"] or entry["parent"] == ""

    def test_prune_keeps_manual_forever(self, manager, world):
        """manual 快照不受保留策略影响（用户回退点永久保留）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        for _ in range(AUTO_SNAPSHOT_KEEP + 5):
            manager.create_snapshot(world, suffix="auto")
        manager.create_snapshot(world, suffix="manual")
        remaining = manager.list_snapshots(world)
        assert len([s for s in remaining if s["suffix"] == "manual"]) == 1

    def test_prune_quit_keeps_recent(self, manager, world):
        """quit 快照保留最近 K+1 个（live_origin 保护）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        created = []
        for _ in range(QUIT_SNAPSHOT_KEEP + 3):
            created.append(manager.create_snapshot(world, suffix="quit"))
        remaining = manager.list_snapshots(world)
        files = [s["file"] for s in remaining]
        assert len(files) == QUIT_SNAPSHOT_KEEP + 1
        assert set(files) == set(created[-(QUIT_SNAPSHOT_KEEP + 1):])

    def test_prune_preserves_live_origin(self, manager, world):
        """live_origin 指向的快照永不淘汰（当前时间点的来源）。

        回滚到较旧的自动快照后，即使该点已落出保留窗口，
        仍被额外保护（它定义了时间线的「当前时间点」）。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        created = []
        for _ in range(8):
            created.append(manager.create_snapshot(world, suffix="auto"))
        origin = created[2]
        manager.set_live_origin(world, origin)  # 模拟回滚到较旧自动点
        manager.prune_snapshots(world, keep_auto=3)
        remaining = manager.list_snapshots(world)
        files = [s["file"] for s in remaining]
        assert origin in files, "活目录来源快照不能被淘汰"
        assert len(files) == 4, "keep 窗口内最新 3 个 + 受保护的来源"

    def test_prune_cleans_orphan_lineage(self, manager, world):
        """血缘条目存在但文件缺失的孤儿被清理，子节点重接。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        os.remove(os.path.join(manager.snapshot_dir(world), s1))
        manager.prune_snapshots(world)
        lineage = manager.snapshot_lineage(world)
        assert s1 not in lineage["snapshots"], "孤儿血缘条目被清理"
        assert lineage["snapshots"][s2]["parent"] == "", "子节点重接到祖父"

    def test_prune_returns_deleted_count(self, manager, world):
        """返回淘汰数量（无淘汰时为零）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual")
        assert manager.prune_snapshots(world) == 0

    def test_prune_manual_triggered_on_create(self, manager, world):
        """创建快照自动触发保留策略（一次创建即淘汰超量 auto）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        for _ in range(AUTO_SNAPSHOT_KEEP + 3):
            manager.create_snapshot(world, suffix="auto")
        manager.create_snapshot(world, suffix="manual")
        remaining = manager.list_snapshots(world)
        autos = [s for s in remaining if s["suffix"] == "auto"]
        assert len(autos) == AUTO_SNAPSHOT_KEEP, "auto 上限保持生效"

    def test_prune_does_not_touch_other_suffixes(self, manager, world):
        """手动指定 keep 上限时仅影响对应来源。"""
        manager.write_state(world, {"clock": {"time": 100}})
        for _ in range(5):
            manager.create_snapshot(world, suffix="auto")
        for _ in range(5):
            manager.create_snapshot(world, suffix="quit")
        manager.prune_snapshots(world, keep_auto=2, keep_quit=1)
        remaining = manager.list_snapshots(world)
        autos = [s for s in remaining if s["suffix"] == "auto"]
        quits = [s for s in remaining if s["suffix"] == "quit"]
        assert len(autos) == 2
        assert len(quits) == 2, "live_origin（最后一个 quit）额外保护"


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
