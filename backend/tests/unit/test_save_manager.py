"""存档管理器单元测试 — 存档位生命周期、实时状态、快照与管理操作。

覆盖 ascend/save/manager.py 与 manifest.py。
使用 tmp_path 隔离存档根目录。
"""

import io
import json
import os
import shutil
import sqlite3
import struct
import zipfile

import pytest

from ascend.save.manager import (
    SaveManager, SNAPSHOT_SUFFIX, AUTO_SNAPSHOT_KEEP, QUIT_SNAPSHOT_KEEP,
    STATE_FILE, CHUNKS_DB, ENTITIES_FILE, EVENTS_DB,
)
from ascend.save.manifest import Manifest, SaveFormatError, MANIFEST_NAME
from ascend.save.crypto import SaveCryptoError, SaveKeys


def _pages_payload(page_size: int, file_size: int, pages: dict) -> bytes:
    """构造 SQLite 页图 payload（与 _PAGES_SUFFIX 格式一致）。"""
    out = struct.pack("<IQI", page_size, file_size, len(pages))
    for index, blob in pages.items():
        out += struct.pack("<II", index, len(blob)) + blob
    return out


def _build_delta_snapshot(
    path: str, base_file: str | None, wdir: str,
    entries: list[str], pages: dict[str, bytes], *,
    version: int = 2, world_id: str = "test-world", seed: int = 1,
) -> None:
    """构造 v2 增量快照文件（测试用：文件级条目 + 页图条目）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in entries:
            zf.write(os.path.join(wdir, name), name)
        for db_name, payload in pages.items():
            zf.writestr(db_name + ".pages", payload)
    session_key = SaveKeys.generate()
    header = json.dumps({
        "format": "ascendsave",
        "version": version,
        "base": base_file,
        "world_id": world_id,
        "seed": seed,
        "secrets_blob": session_key.protect(world_id, seed),
    }, ensure_ascii=False).encode("utf-8") + b"\n"
    with open(path, "wb") as f:
        f.write(header)
        f.write(session_key.encrypt(buffer.getvalue()))


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
        """不存在的世界（合法 ID 格式）报 SaveFormatError。"""
        with pytest.raises(SaveFormatError):
            manager.get_manifest("0" * 32)


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


class TestGenParams:
    """创建世界调参产出（Issue #8）随档定案。"""

    def test_create_persists_gen_params(self, manager):
        """gen_params 写入 manifest 并可经文件往返恢复。"""
        manifest = manager.create_world(
            "调参世界", seed=42, gen_params={"land_ratio": 0.35},
        )
        assert manifest.gen_params == {"land_ratio": 0.35}
        reloaded = Manifest.read(manager.manifest_path(manifest.world_id))
        assert reloaded.gen_params == {"land_ratio": 0.35}

    def test_create_without_gen_params(self, manager):
        """无调参时 gen_params 为 None（旧档兼容）。"""
        manifest = manager.create_world("默认世界", seed=1)
        assert manifest.gen_params is None
        reloaded = Manifest.read(manager.manifest_path(manifest.world_id))
        assert reloaded.gen_params is None

    def test_legacy_manifest_without_gen_params(self, manager):
        """旧版 manifest（无 gen_params 字段）仍可读（向前兼容）。"""
        manifest = manager.create_world("旧档", seed=1)
        path = manager.manifest_path(manifest.world_id)
        data = json.loads(open(path, encoding="utf-8").read())
        del data["gen_params"]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        reloaded = Manifest.read(path)
        assert reloaded.gen_params is None

    def test_invalid_land_ratio_rejected(self, manager):
        """land_ratio 越界/非法值拒绝创建（SaveFormatError）。"""
        for ratio in (0.0, 1.5, -0.1, "abc"):
            with pytest.raises(SaveFormatError):
                manager.create_world(
                    "非法调参", seed=1, gen_params={"land_ratio": ratio},
                )

    def test_size_params_persisted(self, manager):
        """地图尺寸随档定案并可往返恢复。"""
        manifest = manager.create_world(
            "尺寸世界", seed=42,
            gen_params={"width_km": 150.0, "height_km": 90.0},
        )
        assert manifest.gen_params == {"width_km": 150.0, "height_km": 90.0}
        reloaded = Manifest.read(manager.manifest_path(manifest.world_id))
        assert reloaded.gen_params == {"width_km": 150.0, "height_km": 90.0}

    def test_invalid_size_rejected(self, manager):
        """地图尺寸越界/非法值拒绝创建（SaveFormatError）。"""
        for gen in (
            {"width_km": 0.0}, {"width_km": 500.0},
            {"height_km": -5.0}, {"height_km": "x"},
        ):
            with pytest.raises(SaveFormatError):
                manager.create_world("非法尺寸", seed=1, gen_params=gen)

    def test_partial_size_params_allowed(self, manager):
        """只调一项尺寸也合法（缺省项用默认）。"""
        manifest = manager.create_world(
            "半调参", seed=1, gen_params={"width_km": 60.0},
        )
        assert manifest.gen_params == {"width_km": 60.0}

    def test_unknown_gen_param_keys_kept(self, manager):
        """未知键保留（向前兼容未来步骤的调参）。"""
        manifest = manager.create_world(
            "未来调参", seed=1,
            gen_params={"land_ratio": 0.5, "colony_seed": 7},
        )
        assert manifest.gen_params == {"land_ratio": 0.5, "colony_seed": 7}


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

    def test_snapshot_kind_parses_suffix(self, manager):
        """来源标识解析：suffix 在唯一段之后，路径/裸名均可。"""
        assert manager.snapshot_kind("@2026-01-01-120000-abc123-manual.ascendsave") == "manual"
        assert manager.snapshot_kind("@2026-01-01-120000-abc123-auto.ascendsave") == "auto"
        assert manager.snapshot_kind("@2026-01-01-120000-abc123-quit.ascendsave") == "quit"
        assert manager.snapshot_kind("snapshots/@2026-01-01-120000-x-auto.ascendsave") == "auto"

    def test_refresh_snapshot_freezes_live_state(self, manager, world):
        """刷新快照：内容 = 当前活状态，血缘位置不变（原地冻结）。"""
        original = {
            "clock": {"time": 100, "speed": 1.0, "paused": False},
            "player": {"entity_id": "e1", "x": 1.0, "y": 2.0},
            "archive_max_timestamp": 0,
        }
        manager.write_state(world, original)
        filename = manager.create_snapshot(world, suffix="auto", game_time=100)
        updated = dict(original)
        updated["clock"] = {**original["clock"], "time": 150}
        manager.write_state(world, updated)
        manager.refresh_snapshot(world, filename, game_time=150)

        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][filename]["game_time"] == 150, \
            "刷新应更新血缘 game_time"
        assert lineage["snapshots"][filename]["parent"] == "", \
            "刷新不改变血缘位置"
        assert lineage["live_origin"] == filename, "刷新不改变活目录来源"
        # 展开刷新后的快照：内容 = 离开时刻活状态
        snapshot_path = os.path.join(manager.snapshot_dir(world), filename)
        manager.extract_snapshot(snapshot_path)
        assert manager.read_state(world) == updated, \
            "刷新后快照内容应为当前活状态"

    def test_refresh_snapshot_unknown_or_missing_rejected(self, manager, world):
        """刷新不存在的快照被拒绝（血缘外或文件缺失）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        with pytest.raises(SaveFormatError, match="血缘"):
            manager.refresh_snapshot(
                world, "@2020-01-01-000000-000000-auto.ascendsave",
            )

    def test_refresh_rejects_manual_snapshot(self, manager, world):
        """刷新仅限 auto 记录：manual（不可变保存节点）拒绝改写。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        with pytest.raises(SaveFormatError, match="仅 auto"):
            manager.refresh_snapshot(world, s1, game_time=999)

    def test_manual_save_promotes_auto_snapshot(self, manager, world):
        """保存 = 当前 auto 记录晋升为 manual + 开启新当前记录。"""
        manager.write_state(world, {"clock": {"time": 100}})
        auto = manager.create_snapshot(world, suffix="auto", game_time=100)
        manager.write_state(world, {"clock": {"time": 150}})
        manual = manager.create_snapshot(world, suffix="manual", game_time=150)

        assert manual.endswith("-manual.ascendsave"), "晋升后为 manual 来源"
        assert manual != auto, "晋升 = 新文件（唯一段更新）"
        lineage = manager.snapshot_lineage(world)
        assert auto not in lineage["snapshots"], "auto 应被晋升移除而非残留"
        assert lineage["snapshots"][manual]["parent"] == "", "晋升保留原 parent"
        assert lineage["snapshots"][manual]["seq"] == 0, "晋升保留原 seq"
        assert lineage["snapshots"][manual]["game_time"] == 150
        # 保存后开启新当前记录（auto，挂在保存节点下游）
        rec = lineage["live_origin"]
        assert rec != manual and rec.endswith("-auto.ascendsave"), \
            "保存后当前记录为 auto"
        assert lineage["snapshots"][rec]["parent"] == manual, \
            "新当前记录开在保存节点下游"
        assert not os.path.exists(
            os.path.join(manager.snapshot_dir(world), auto)
        ), "原 auto 文件应删除"
        # 晋升后的内容 = 当前活状态（保存语义）
        manager.extract_snapshot(
            os.path.join(manager.snapshot_dir(world), manual),
        )
        assert manager.read_state(world)["clock"]["time"] == 150

    def test_first_save_creates_manual_and_fresh_record(self, manager, world):
        """首次保存（无当前记录）：新建 manual + 开启新当前记录。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        rec = lineage["live_origin"]
        assert lineage["snapshots"][s1]["parent"] == "", "首存 parent = 世界初始"
        assert rec.endswith("-auto.ascendsave"), "首存后开启当前记录"
        assert lineage["snapshots"][rec]["parent"] == s1
        # 第二次保存：当前记录晋升为 s2（串到 s1 下），再开新记录
        s2 = manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        assert s1 in lineage["snapshots"] and s2 in lineage["snapshots"]
        assert lineage["snapshots"][s2]["parent"] == s1, "手动链经晋升保持连续"
        assert lineage["live_origin"].endswith("-auto.ascendsave")

    def test_create_snapshot_file(self, manager, world):
        """保存生成 manual 节点并开启新 auto 当前记录。"""
        filename = manager.create_snapshot(world, suffix="manual")
        assert filename.endswith(SNAPSHOT_SUFFIX)
        assert "-manual" in filename
        snapshots = manager.list_snapshots(world)
        assert {s["file"] for s in snapshots} == {
            filename,
            manager.snapshot_lineage(world)["live_origin"],
        }
        assert {s["suffix"] for s in snapshots} == {"manual", "auto"}, \
            "保存节点 + 当前记录（auto）"

    def test_same_second_snapshots_do_not_collide(self, manager, world):
        """同一秒内多次保存互不覆盖（文件名唯一化）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        f1 = manager.create_snapshot(world, suffix="manual")
        f2 = manager.create_snapshot(world, suffix="manual")
        files = [s["file"] for s in manager.list_snapshots(world)]
        assert len(files) == len(set(files)) == 3, \
            "两个 manual + 一个当前记录，互不覆盖"

    def test_enter_snapshot_manual_forks_and_opens_record(self, manager, world):
        """进入手动档：离开记录冻结，目标下游开启新当前记录（分叉）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        rec = manager.snapshot_lineage(world)["live_origin"]
        assert manager.snapshot_lineage(world)["snapshots"][rec]["parent"] == m2

        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), m1), world_id=world,
        )
        lineage = manager.snapshot_lineage(world)
        assert rec in lineage["snapshots"], "离开记录原地保留（冻结）"
        assert lineage["snapshots"][rec]["parent"] == m2, "冻结不改变位置"
        rec_new = lineage["live_origin"]
        assert rec_new != rec and rec_new.endswith("-auto.ascendsave"), \
            "新当前记录为 auto"
        assert lineage["snapshots"][rec_new]["parent"] == m1, \
            "新当前记录开在目标手动档下游"
        children = {f for f, e in lineage["snapshots"].items()
                    if e["parent"] == m1}
        assert children == {m2, rec_new}, "分叉发生在手动节点 m1 处"

    def test_enter_snapshot_auto_continues_no_new_node(self, manager, world):
        """进入 auto 档：不新建任何节点，目标成为当前记录。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.create_snapshot(world, suffix="manual", game_time=200)
        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), m1), world_id=world,
        )
        lineage = manager.snapshot_lineage(world)
        rec = lineage["live_origin"]
        n_before = len(lineage["snapshots"])

        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), rec), world_id=world,
        )
        lineage = manager.snapshot_lineage(world)
        assert len(lineage["snapshots"]) == n_before, "进入 auto 不新建节点"
        assert lineage["live_origin"] == rec, "auto 目标成为当前记录"

    def test_enter_snapshot_freezes_departed_record_content(self, manager, world):
        """进入节点时离开记录内容刷新为离开时刻活状态。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), m1), world_id=world,
        )
        rec = manager.snapshot_lineage(world)["live_origin"]
        # 在记录上游玩到 150（不保存），直接进入手动 M2
        manager.write_state(world, {"clock": {"time": 150}})
        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), m2), world_id=world,
        )

        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][rec]["game_time"] == 150, \
            "离开记录冻结为离开时刻"
        # 内容同步刷新（展开检查）
        manager.extract_snapshot(os.path.join(manager.snapshot_dir(world), rec))
        assert manager.read_state(world)["clock"]["time"] == 150

    def test_enter_frozen_auto_record_continues_and_promotes(self, manager, world):
        """进入冻结的（非当前）auto 记录：继续该线，保存时原地晋升。

        前端真实场景：时间线点击旧分支的冻结 auto 节点——目标成为
        当前记录（不新建），之后保存晋升保留其原 parent/seq。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        rec_2 = manager.snapshot_lineage(world)["live_origin"]  # m2 下游
        # 进入手动 M1 → 新开当前记录，rec_2 冻结为叶子
        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), m1), world_id=world,
        )
        lineage = manager.snapshot_lineage(world)
        rec_3 = lineage["live_origin"]
        assert rec_2 in lineage["snapshots"], "rec_2 冻结保留"

        # 进入冻结的 rec_2：成为当前记录，不新建任何节点
        n_before = len(lineage["snapshots"])
        manager.enter_snapshot(
            os.path.join(manager.snapshot_dir(world), rec_2), world_id=world,
        )
        lineage = manager.snapshot_lineage(world)
        assert len(lineage["snapshots"]) == n_before, "进入冻结 auto 不新建"
        assert lineage["live_origin"] == rec_2

        # 从该线保存 → rec_2 晋升为 manual（parent=m2 保留），新开记录
        manager.write_state(world, {"clock": {"time": 250}})
        snap = manager.create_snapshot(world, suffix="manual", game_time=250)
        lineage = manager.snapshot_lineage(world)
        assert rec_2 not in lineage["snapshots"], "冻结记录晋升而非残留"
        assert lineage["snapshots"][snap]["parent"] == m2, "晋升保留原 parent"
        assert lineage["live_origin"].endswith("-auto.ascendsave")

    def test_enter_snapshot_without_world_id_skips_freeze(self, manager, world):
        """world_id=None：跳过冻结，按快照内嵌 ID 展开并开新记录。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.enter_snapshot(os.path.join(manager.snapshot_dir(world), m1))
        lineage = manager.snapshot_lineage(world)
        rec = lineage["live_origin"]
        assert rec != m1 and rec.endswith("-auto.ascendsave"), \
            "手动目标开启新当前记录"
        assert lineage["snapshots"][rec]["parent"] == m1

    def test_promote_falls_back_when_auto_file_missing(self, manager, world):
        """当前记录文件缺失时保存退回普通新建（异常态自愈）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        auto = manager.create_snapshot(world, suffix="auto", game_time=100)
        os.remove(os.path.join(manager.snapshot_dir(world), auto))
        snap = manager.create_snapshot(world, suffix="manual", game_time=150)
        lineage = manager.snapshot_lineage(world)
        assert auto not in lineage["snapshots"], "缺失记录不得残留"
        assert lineage["snapshots"][snap]["parent"] == "", \
            "退回普通新建（parent=世界初始）"
        assert lineage["live_origin"].endswith("-auto.ascendsave"), \
            "不变式恢复：当前记录恒为 auto"

    def test_lineage_write_failure_keeps_files(self, manager, world, monkeypatch):
        """血缘写失败：跳过保留策略，新保存文件与旧记录均不被误删。

        回归：_write_lineage 失败（只 warning）后 1b 反向对账会把
        无血缘条目的新文件当幽灵删除（静默丢失用户保存）——失败
        时须跳过 prune，旧 auto 记录保留（血缘仍指向它）。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        auto = manager.create_snapshot(world, suffix="auto", game_time=100)

        def broken(world_id, lineage):
            return False  # 生产契约：写失败返回 False（内部已记 warning）

        monkeypatch.setattr(manager._lineage, "write", broken)
        snap = manager.create_snapshot(world, suffix="manual", game_time=150)

        files = {s["file"] for s in manager.list_snapshots(world)}
        assert snap in files, "保存文件不得被误删（血缘写失败跳过对账）"
        assert auto in files, "旧 auto 记录保留（血缘条目仍指向它）"

    def test_enter_snapshot_record_failure_does_not_block(
        self, manager, world, monkeypatch, caplog,
    ):
        """进入手动档后新记录创建失败：回滚不阻断（活目录已展开）。"""
        import logging

        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.create_snapshot(world, suffix="manual", game_time=200)
        original = manager._create_plain_snapshot

        def broken(world_id, suffix, game_time, **kwargs):
            if suffix == "auto":
                raise OSError("disk full")
            return original(world_id, suffix, game_time, **kwargs)

        monkeypatch.setattr(manager, "_create_plain_snapshot", broken)
        with caplog.at_level(logging.WARNING, logger="ascend.save"):
            assert manager.enter_snapshot(
                os.path.join(manager.snapshot_dir(world), m1), world_id=world,
            ) == world
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == m1, \
            "降级：当前记录缺位（live_origin=目标）"
        assert manager.read_state(world)["clock"]["time"] == 100, \
            "活目录已展开"

    def test_fresh_record_failure_does_not_break_save(self, manager, world, monkeypatch):
        """新当前记录创建失败：保存本身成功，下次保存自愈不变式。"""
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="auto", game_time=100)
        original = manager._create_plain_snapshot
        auto_calls = {"n": 0}

        def flaky(world_id, suffix, game_time, **kwargs):
            if suffix == "auto":
                auto_calls["n"] += 1
                if auto_calls["n"] == 1:
                    raise OSError("disk full")
            return original(world_id, suffix, game_time, **kwargs)

        monkeypatch.setattr(manager, "_create_plain_snapshot", flaky)
        snap = manager.create_snapshot(world, suffix="manual", game_time=150)
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][snap]["parent"] == "", \
            "晋升成功，保存不因新记录失败而失败"
        assert lineage["live_origin"] == snap, "降级：当前记录缺位（live_origin=保存节点）"

        # 下次保存自愈：普通新建 + 新当前记录恢复
        snap2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][snap2]["parent"] == snap, "手动链保持连续"
        assert lineage["live_origin"].endswith("-auto.ascendsave"), \
            "不变式恢复：当前记录恒为 auto"

    def test_snapshot_is_encrypted(self, manager, world, tmp_path):
        """快照内容不含明文（加密打包）。"""
        manager.write_state(world, {"player": {"x": 42.0}})
        filename = manager.create_snapshot(world)
        raw = open(
            os.path.join(manager.snapshot_dir(world), filename), "rb"
        ).read()
        assert b"42.0" not in raw

    def test_snapshot_header_hides_session_keys(self, manager, world):
        """快照头部不落裸钥匙：会话钥匙经 protect 混淆藏入 secrets_blob。

        防护：钥匙明文随头部分发会被普通工具直读——与存档位
        manifest.secrets_blob 同级（防直读/防手贱，不防推导）。
        """
        filename = manager.create_snapshot(world)
        path = os.path.join(manager.snapshot_dir(world), filename)
        with open(path, "rb") as f:
            header = json.loads(f.readline().decode("utf-8"))
        for field in ("fernet_key", "sign_key"):
            assert field not in header, "头部不得出现裸钥匙字段"
        blob = header["secrets_blob"]
        assert "fernet_key" not in blob and "sign_key" not in blob, (
            "混淆串内不得出现钥匙字段名"
        )
        assert header["world_id"] == world
        # 头部身份与混淆串一致 → 可解锁（往返可用性由其余用例覆盖）
        keys = SaveKeys.from_protected(blob, header["world_id"], header["seed"])
        assert len(keys.fernet_key) == 32

    def test_snapshot_header_tampered_identity_rejected(self, manager, world):
        """篡改头部 world_id/seed 与 secrets_blob 不匹配 → 拒绝打开。

        防护：身份是混淆钥匙的派生输入——改身份即改派生密钥，
        混淆串无法还原，按防篡改处理。
        """
        filename = manager.create_snapshot(world)
        path = os.path.join(manager.snapshot_dir(world), filename)
        with open(path, "rb") as f:
            header_line = f.readline()
            payload = f.read()
        header = json.loads(header_line.decode("utf-8"))
        header["seed"] = header["seed"] + 1
        tampered = json.dumps(header, ensure_ascii=False).encode("utf-8") + b"\n"
        with open(path, "wb") as f:
            f.write(tampered)
            f.write(payload)
        with pytest.raises(SaveCryptoError):
            manager.read_snapshot_state(path)

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
                "name": "x", "seed": 1,
                "world_id": world,
            }))
        keys = SaveKeys.generate()
        header = json.dumps({
            "format": "ascendsave", "version": 1,
            "world_id": world, "seed": 1,
            "secrets_blob": keys.protect(world, 1),
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

    def test_extract_copied_snapshot_targets_copy(self, manager, world):
        """复制档快照已改绑副本 ID：不带覆盖的展开作用于副本本身。"""
        manager.write_state(world, {"clock": {"time": 100, "speed": 1.0,
                                              "paused": False}, "player": {},
                                    "archive_max_timestamp": 0})
        filename = manager.create_snapshot(world, suffix="manual")
        new_id = manager.export_world(world)
        copied_snapshot = os.path.join(
            manager.snapshot_dir(new_id), filename
        )
        # 不带覆盖：内嵌 ID 已是副本 → 展开为副本，原世界不受影响
        assert manager.extract_snapshot(copied_snapshot) == new_id
        assert manager.get_manifest(new_id).world_id == new_id

    def test_extract_cross_world_override_warns(
        self, manager, world, caplog,
    ):
        """跨世界覆盖展开记录 warning（显式把 A 世界快照展开进 B）。"""
        import logging

        manager.write_state(world, {"clock": {"time": 100}})
        filename = manager.create_snapshot(world, suffix="manual")
        other_id = manager.create_world("另一个世界", seed=9).world_id
        moved = os.path.join(manager.snapshot_dir(other_id), filename)
        shutil.copy2(
            os.path.join(manager.snapshot_dir(world), filename), moved,
        )
        with caplog.at_level(logging.WARNING, logger="ascend.save"):
            assert manager.extract_snapshot(
                moved, world_id=other_id,
            ) == other_id
        assert any("不一致" in r.message for r in caplog.records), \
            "内嵌世界与目标不一致应记录 warning"

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
        assert lineage["live_origin"] != filename, \
            "保存后当前记录 = 新开的 auto 记录（非保存节点本身）"
        assert lineage["live_origin"].endswith("-auto.ascendsave")
        entry = lineage["snapshots"][filename]
        assert entry["parent"] == ""
        assert entry["game_time"] == 500

    def test_consecutive_snapshots_chain(self, manager, world):
        """连续保存自动串链：后一个快照从最近一个派生（非世界初始）。

        防护：连续保存经晋升串链，parent 恒为最近手动节点——体现
        「从最近手动存档派生」，而非都挂在世界初始上。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        s2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        manager.write_state(world, {"clock": {"time": 300}})
        s3 = manager.create_snapshot(world, suffix="manual", game_time=300)
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"].endswith("-auto.ascendsave"), \
            "当前记录恒为 auto"
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

        防护：按路径直接 open 会 FileNotFoundError，前端回滚请求
        整体失败——必须解析到目标世界的快照目录。
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
        assert lineage["live_origin"].endswith("-auto.ascendsave"), \
            "保存后当前记录 = 新开的 auto 记录"

    def test_lineage_survives_extract(self, manager, world):
        """回滚展开不丢血缘上下文（原快照条目保留）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap_a = manager.create_snapshot(world, suffix="manual")
        manager.write_state(world, {"clock": {"time": 200}})
        manager.create_snapshot(world, suffix="manual")
        path = os.path.join(manager.snapshot_dir(world), snap_a)
        manager.extract_snapshot(path)
        lineage = manager.snapshot_lineage(world)
        assert len(lineage["snapshots"]) == 3, "回滚不应丢原血缘条目"
        assert lineage["live_origin"] == snap_a

    def test_export_copies_lineage(self, manager, world):
        """复制存档携带血缘（副本时间线上下文完整）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        filename = manager.create_snapshot(world, suffix="manual")
        new_id = manager.export_world(world)
        lineage = manager.snapshot_lineage(new_id)
        assert len(lineage["snapshots"]) == 2, "保存节点 + 当前记录均随档复制"
        assert lineage["live_origin"].endswith("-auto.ascendsave")


    def test_lineage_seq_monotonic(self, manager, world):
        """血缘 seq 单调递增（权威排序键，与游戏时间倒退无关）。"""
        manager.write_state(world, {"clock": {"time": 300}})
        s1 = manager.create_snapshot(world, suffix="manual", game_time=300)
        manager.write_state(world, {"clock": {"time": 100}})  # 回滚后时间倒退
        s2 = manager.create_snapshot(world, suffix="manual", game_time=100)
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][s1]["seq"] == 0
        assert lineage["snapshots"][s2]["seq"] == 1, "seq 反映创建顺序而非游戏时间"

    def test_lineage_entries_have_seq(self, manager, world):
        """新格式血缘条目写入即含 seq（权威排序键）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual")
        lineage = manager.snapshot_lineage(world)
        for entry in lineage["snapshots"].values():
            assert "seq" in entry


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
        """删除 live_origin（当前记录）：来源回退到其 parent。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        rec = manager.snapshot_lineage(world)["live_origin"]  # s1 下游当前记录
        manager.delete_snapshot(world, rec)
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == s1, "回退到记录的 parent"

    def test_delete_root_live_origin(self, manager, world):
        """删除链头手动节点：其下记录重接到世界初始并保持当前。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        rec = manager.snapshot_lineage(world)["live_origin"]
        manager.delete_snapshot(world, s1)
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][rec]["parent"] == "", "重接到世界初始"
        assert lineage["live_origin"] == rec, "当前记录保持为活目录来源"

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


class TestSnapshotIncremental:
    """增量快照（v2 链式物化）读路径。

    写路径（diff）尚未接入，增量文件由测试辅助函数按格式构造；
    验证物化 = 全量基座 + 逐级应用文件级/页级变更。
    """

    def test_delta_file_level_merges_over_base(self, manager, world):
        """文件级增量展开：state 变更叠加到全量基座之上。"""
        manager.write_state(world, {"clock": {"time": 100}})
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        # 构造增量：state 变更为 time=200，其余继承基座
        manager.write_state(world, {"clock": {"time": 200}})
        delta = "@2026-01-01-000000-test-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE], {},
        )
        manager._record_snapshot_lineage(world, delta, 200, 0.0)

        assert manager.extract_snapshot(delta, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 200, \
            "增量 state 覆盖基座"
        # 预览路径同样物化
        assert manager.read_snapshot_state(
            os.path.join(manager.snapshot_dir(world), delta),
        )["clock"]["time"] == 200

    def test_delta_page_level_merges_sqlite(self, manager, world):
        """页级增量展开：新增 SQLite 页叠加到基座 DB 之上。"""
        manager.write_state(world, {"clock": {"time": 100}})
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        old_bytes = open(db_path, "rb").read()

        conn = sqlite3.connect(db_path)
        try:
            conn.execute("INSERT INTO t VALUES (2)")
            conn.commit()
        finally:
            conn.close()
        new_bytes = open(db_path, "rb").read()

        page_size = 4096
        old_pages = {
            i: old_bytes[i * page_size:(i + 1) * page_size]
            for i in range((len(old_bytes) + page_size - 1) // page_size)
        }
        changed = {
            i: new_bytes[i * page_size:(i + 1) * page_size]
            for i in range((len(new_bytes) + page_size - 1) // page_size)
            if old_pages.get(i) != new_bytes[i * page_size:(i + 1) * page_size]
        }
        delta = "@2026-01-01-000001-test-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE],
            {"chunks.db": _pages_payload(page_size, len(new_bytes), changed)},
        )
        manager._record_snapshot_lineage(world, delta, 100, 0.0)

        assert manager.extract_snapshot(delta, world_id=world) == world
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT x FROM t ORDER BY x").fetchall()
        finally:
            conn.close()
        assert rows == [(1,), (2,)], "基座行 + 增量行合并完整"

    def test_delta_chain_multi_level(self, manager, world):
        """多层链物化：全量根 → 增量 → 增量，逐级合并。"""
        manager.write_state(world, {"clock": {"time": 100}})
        root = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 150}})
        mid = "@2026-01-01-000000-mid-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), mid),
            root, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE], {},
        )
        manager._record_snapshot_lineage(world, mid, 150, 0.0)
        manager.write_state(world, {"clock": {"time": 200}})
        leaf = "@2026-01-01-000001-leaf-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), leaf),
            mid, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE], {},
        )
        manager._record_snapshot_lineage(world, leaf, 200, 0.0)

        assert manager.extract_snapshot(leaf, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 200, \
            "两层增量逐级叠加"

    def test_delta_missing_base_rejected(self, manager, world):
        """增量基座缺失：物化报错（后代不可恢复）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        delta = "@2026-01-01-000000-orphan-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE], {},
        )
        manager._record_snapshot_lineage(world, delta, 100, 0.0)
        os.remove(os.path.join(manager.snapshot_dir(world), base))
        with pytest.raises(SaveFormatError, match="链上快照缺失"):
            manager.extract_snapshot(delta, world_id=world)

    def test_delta_pages_from_missing_base_db(self, manager, world):
        """DB 从无到有：全页增量可在无基座库时物化（写读对称）。

        回归：_diff_db_pages 在基座无该 DB 时产出全页覆盖，读侧
        _apply_pages 须创建文件（页 0 含 SQLite 头），否则该增量
        及其全部后代不可恢复。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        # 活目录新建 DB（玩家改动路径）
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INTEGER, b BLOB)")
            conn.execute(
                "INSERT INTO t VALUES (1, ?)",
                (sqlite3.Binary(os.urandom(65536)),),
            )
            conn.commit()
        finally:
            conn.close()
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        m2_path = os.path.join(manager.snapshot_dir(world), m2)
        header, zf = manager._snap.open_snapshot(m2_path)
        assert header.get("base") == base
        names = zf.namelist()
        zf.close()
        assert "chunks.db.pages" in names, "DB 从无到有以全页图携带"

        assert manager.extract_snapshot(m2, world_id=world) == world
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT x FROM t").fetchall()
        finally:
            conn.close()
        assert rows == [(1,)], "从无到有的 DB 完整恢复"

    def test_base_same_as_live_falls_back_on_stale_origin(
        self, manager, world, monkeypatch,
    ):
        """血缘写失败后 fresh record 退化为真实 diff（锚点 ≠ 父）。

        回归：空增量特例仅当锚点 == 直接父节点（刚写入）时成立；
        晋升的血缘写失败使磁盘 live_origin 停留在旧 auto 记录，
        此时必须以真实 diff 写入当前状态，否则回滚到旧内容。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        original = manager._write_lineage
        calls = {"n": 0}

        def flaky(wid, lineage):
            calls["n"] += 1
            if calls["n"] == 3:  # 晋升节点的血缘写失败
                return False
            return original(wid, lineage)

        monkeypatch.setattr(manager, "_write_lineage", flaky)
        manager.create_snapshot(world, suffix="manual", game_time=200)
        rec = manager.snapshot_lineage(world)["live_origin"]
        assert rec.endswith("-auto.ascendsave"), "fresh record 血缘写成功"

        assert manager.extract_snapshot(rec, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 200, \
            "内容 = 当前活状态（非旧锚点的 100）"

    def test_anchor_corruption_falls_back_to_full(self, manager, world):
        """锚点损坏（解密失败）→ 保存回退全量基座（不阻断）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        manager.create_snapshot(world, suffix="manual", game_time=200)
        with open(os.path.join(manager.snapshot_dir(world), m1), "wb") as f:
            f.write(b"corrupted")
        manager.write_state(world, {"clock": {"time": 300}})
        m3 = manager.create_snapshot(world, suffix="manual", game_time=300)
        m3_path = os.path.join(manager.snapshot_dir(world), m3)
        header, _ = manager._snap.open_snapshot(m3_path)
        assert header.get("base") is None, "锚点损坏 → 回退全量基座"
        assert manager.extract_snapshot(m3, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 300

    def test_delete_with_corrupted_chain_best_effort(
        self, manager, world, caplog,
    ):
        """删除遇损坏链：重基座 best-effort 跳过，删除本身不中止。"""
        import logging

        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        manager.write_state(world, {"clock": {"time": 300}})
        m3 = manager.create_snapshot(world, suffix="manual", game_time=300)
        with open(os.path.join(manager.snapshot_dir(world), m1), "wb") as f:
            f.write(b"corrupted")
        with caplog.at_level(logging.WARNING, logger="ascend.save"):
            manager.delete_snapshot(world, m2)
        lineage = manager.snapshot_lineage(world)
        assert m2 not in lineage["snapshots"], "删除完成（不被重基座拖累）"
        assert lineage["snapshots"][m3]["parent"] == m1, "血缘重接完成"
        assert any("重基座失败" in r.message for r in caplog.records), \
            "应记录重基座跳过 warning"

    def test_delta_tiny_pages_payload_rejected(self, manager, world):
        """页图 payload 短于 16 字节：归一为 SaveFormatError。"""
        manager.write_state(world, {"clock": {"time": 100}})
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        delta = "@2026-01-01-000000-tiny-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME],
            {"chunks.db": b"x" * 10},
        )
        manager._record_snapshot_lineage(world, delta, 100, 0.0)
        with pytest.raises(SaveFormatError, match="截断"):
            manager.extract_snapshot(delta, world_id=world)

    def test_delta_vacuum_shrink(self, manager, world):
        """DB 缩容（VACUUM）：页图 file_size 收缩截断正确。"""
        manager.write_state(world, {"clock": {"time": 100}})
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INTEGER, b BLOB)")
            for i in range(200):
                conn.execute(
                    "INSERT INTO t VALUES (?, ?)",
                    (i, sqlite3.Binary(os.urandom(2048))),
                )
            conn.commit()
        finally:
            conn.close()
        manager.create_snapshot(world, suffix="manual", game_time=100)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("DELETE FROM t")
            conn.commit()
            conn.execute("VACUUM")
            conn.commit()
        finally:
            conn.close()
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        assert manager.extract_snapshot(m2, world_id=world) == world
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        finally:
            conn.close()
        assert rows == 0, "VACUUM 缩容后内容正确"

    def test_delta_nondefault_page_size(self, manager, world):
        """非默认页尺寸（8192）的 DB 页级增量往返正确。"""
        manager.write_state(world, {"clock": {"time": 100}})
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("PRAGMA page_size = 8192")
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.execute("INSERT INTO t VALUES (1)")
            conn.commit()
        finally:
            conn.close()
        manager.create_snapshot(world, suffix="manual", game_time=100)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("INSERT INTO t VALUES (2)")
            conn.commit()
        finally:
            conn.close()
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        assert manager.extract_snapshot(m2, world_id=world) == world
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT x FROM t ORDER BY x").fetchall()
        finally:
            conn.close()
        assert rows == [(1,), (2,)]

    def test_delta_corrupt_pages_rejected(self, manager, world):
        """页图数据截断：物化报错（防篡改/损坏）。"""
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        delta = "@2026-01-01-000000-trunc-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME],
            # 声明 1 页但字节截断
            {"chunks.db": struct.pack("<IQI", 4096, 4096, 1) + b"xx"},
        )
        manager._record_snapshot_lineage(world, delta, 100, 0.0)
        with pytest.raises(SaveFormatError, match="截断"):
            manager.extract_snapshot(delta, world_id=world)

    def test_write_path_produces_incremental_chain(self, manager, world):
        """写路径：根手动档为全量基座，后续保存为增量（页级/文件级）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        db_path = manager.chunks_db_path(world)
        conn = sqlite3.connect(db_path)
        try:
            conn.execute("CREATE TABLE t (x INTEGER, b BLOB)")
            conn.execute(
                "INSERT INTO t VALUES (1, ?)",
                (sqlite3.Binary(os.urandom(65536)),),
            )
            conn.commit()
        finally:
            conn.close()
        root = manager.create_snapshot(world, suffix="manual", game_time=100)
        root_path = os.path.join(manager.snapshot_dir(world), root)
        header, _ = manager._snap.open_snapshot(root_path)
        assert header.get("base") is None, "根手动档 = 全量基座"
        root_size = os.path.getsize(root_path)

        # 仅 state 变更：DB 未动 → 增量不含 DB 页，显著小于全量
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        m2_path = os.path.join(manager.snapshot_dir(world), m2)
        header, _ = manager._snap.open_snapshot(m2_path)
        assert header.get("base") == root, "晋升节点锚定根手动档"
        assert os.path.getsize(m2_path) < root_size // 2, \
            "未变化的 DB 页不入增量"

        # DB 新增行：增量以页图携带，不存全量 DB
        conn = sqlite3.connect(db_path)
        try:
            conn.execute(
                "INSERT INTO t VALUES (2, ?)",
                (sqlite3.Binary(os.urandom(65536)),),
            )
            conn.commit()
        finally:
            conn.close()
        manager.write_state(world, {"clock": {"time": 300}})
        m3 = manager.create_snapshot(world, suffix="manual", game_time=300)
        m3_path = os.path.join(manager.snapshot_dir(world), m3)
        header, zf = manager._snap.open_snapshot(m3_path)
        assert header.get("base") == m2, "连续保存串链锚定"
        names = zf.namelist()
        zf.close()
        assert "chunks.db.pages" in names, "DB 变更以页图携带"
        assert "chunks.db" not in names, "DB 不整体入增量"

        # fresh record：空增量（内容 == 锚点，仅 manifest）。
        # 注意：须在 extract 之前读取 live_origin——展开会把它重置为
        # 目标节点（enter_snapshot 流程在展开后才建新当前记录）
        rec = manager.snapshot_lineage(world)["live_origin"]
        rec_path = os.path.join(manager.snapshot_dir(world), rec)
        header, zf = manager._snap.open_snapshot(rec_path)
        assert header.get("base") == m3, "fresh record 锚定保存节点"
        assert zf.namelist() == ["manifest.json"], "空增量仅含 manifest"
        zf.close()

        # 往返：m3 展开 → 两行数据 + state=300（页级合并正确）
        assert manager.extract_snapshot(m3, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 300
        conn = sqlite3.connect(db_path)
        try:
            rows = conn.execute("SELECT x FROM t ORDER BY x").fetchall()
        finally:
            conn.close()
        assert rows == [(1,), (2,)], "基座行 + 增量行合并完整"

        # 空增量往返：内容 == 锚点（fresh record 展开）
        assert manager.extract_snapshot(rec, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 300

    def test_write_path_freeze_is_incremental(self, manager, world):
        """冻结（refresh）原地重写为增量：锚点不变、内容刷新。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        rec = manager.snapshot_lineage(world)["live_origin"]
        rec_path = os.path.join(manager.snapshot_dir(world), rec)
        header, _ = manager._snap.open_snapshot(rec_path)
        anchor_before = header.get("base")
        assert anchor_before == m2

        manager.write_state(world, {"clock": {"time": 250}})
        manager.refresh_snapshot(world, rec)
        header, _ = manager._snap.open_snapshot(rec_path)
        assert header.get("base") == anchor_before, "冻结不改变锚点"

        assert manager.extract_snapshot(rec, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 250, \
            "冻结内容刷新为离开时刻"

    def test_rebase_after_mid_manual_delete(self, manager, world):
        """删除中间手动档：后代增量重基座到新锚点，内容不变。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)
        manager.write_state(world, {"clock": {"time": 300}})
        m3 = manager.create_snapshot(world, suffix="manual", game_time=300)
        rec = manager.snapshot_lineage(world)["live_origin"]

        manager.delete_snapshot(world, m2)
        lineage = manager.snapshot_lineage(world)
        assert m2 not in lineage["snapshots"]
        assert lineage["snapshots"][m3]["parent"] == m1, "血缘重接到 m1"

        m3_path = os.path.join(manager.snapshot_dir(world), m3)
        header, _ = manager._snap.open_snapshot(m3_path)
        assert header.get("base") == m1, "m3 重基座到 m1"
        # 内容不变（重基座只改基座引用）
        assert manager.extract_snapshot(m3, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 300

        rec_path = os.path.join(manager.snapshot_dir(world), rec)
        header, _ = manager._snap.open_snapshot(rec_path)
        assert header.get("base") == m3, "rec 锚点（m3）未删，不重基座"
        assert manager.extract_snapshot(rec, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 300

    def test_rebase_to_full_when_root_deleted(self, manager, world):
        """删除根手动档：后代重基座为全量（成为新基座）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        m2 = manager.create_snapshot(world, suffix="manual", game_time=200)

        manager.delete_snapshot(world, m1)
        m2_path = os.path.join(manager.snapshot_dir(world), m2)
        header, _ = manager._snap.open_snapshot(m2_path)
        assert header.get("base") is None, "无存活手动祖先 → 全量基座"
        assert manager.extract_snapshot(m2, world_id=world) == world
        assert manager.read_state(world)["clock"]["time"] == 200

    def test_auto_prune_never_breaks_remaining(self, manager, world):
        """auto 环形淘汰不触发重基座：后代锚点恒为手动节点。"""
        manager.write_state(world, {"clock": {"time": 100}})
        m1 = manager.create_snapshot(world, suffix="manual", game_time=100)
        created = [m1]
        for i in range(5):
            manager.write_state(world, {"clock": {"time": 100 + i}})
            created.append(
                manager.create_snapshot(world, suffix="auto", game_time=100 + i)
            )
        manager.prune_snapshots(world, keep_auto=3)
        remaining = [s["file"] for s in manager.list_snapshots(world)]
        assert len(remaining) == 5, "m1 + 环形 3 个 auto + live_origin 保护"
        # 每个剩余节点都可恢复（锚点链完好）
        for name in remaining:
            assert manager.extract_snapshot(name, world_id=world) == world
        # 被淘汰的 auto 文件已删除
        gone = [f for f in created if f not in remaining]
        for name in gone:
            assert not os.path.exists(
                os.path.join(manager.snapshot_dir(world), name),
            ), f"被淘汰的 {name} 应删除"


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
        """quit 保存保留最近 K 个（当前记录是 auto，不占 quit 名额）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        for _ in range(QUIT_SNAPSHOT_KEEP + 3):
            manager.create_snapshot(world, suffix="quit")
        remaining = manager.list_snapshots(world)
        quits = [s for s in remaining if s["suffix"] == "quit"]
        autos = [s for s in remaining if s["suffix"] == "auto"]
        assert len(quits) == QUIT_SNAPSHOT_KEEP, "quit 环形保留最近 K 个"
        assert len(autos) == 1, "当前记录（auto）不受 quit 策略影响"

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

    def test_prune_cleans_ghost_files(self, manager, world):
        """磁盘上无血缘条目的残留快照文件被清理（反向对账）。

        回归：晋升时旧 auto 文件删除失败会留下幽灵节点——prune
        应对账删除，避免前端时间线出现无血缘的节点。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual")
        ghost = "@2026-01-01-000000-ghost-auto.ascendsave"
        with open(os.path.join(manager.snapshot_dir(world), ghost), "wb") as f:
            f.write(b"x")
        manager.prune_snapshots(world)
        assert not os.path.exists(
            os.path.join(manager.snapshot_dir(world), ghost),
        ), "幽灵快照文件应被清理"
        assert manager.snapshot_lineage(world)["snapshots"], "血缘条目不受影响"

    def test_prune_without_lineage_keeps_files(self, manager, world):
        """血缘缺失/损坏时不反向对账（防全量误删 manual）。

        回归：lineage.json 丢失或损坏时 known 为空，1b 会把全部
        快照文件当幽灵删除——宁缺勿删，文件保留待修复。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        rec = manager.snapshot_lineage(world)["live_origin"]
        os.remove(manager.lineage_path(world))
        manager.prune_snapshots(world)
        assert {s1, rec} <= {s["file"] for s in manager.list_snapshots(world)}, \
            "血缘缺失时不得反向对账删文件"
        # 损坏同样安全
        with open(manager.lineage_path(world), "w", encoding="utf-8") as f:
            f.write("{broken")
        manager.prune_snapshots(world)
        assert {s1, rec} <= {s["file"] for s in manager.list_snapshots(world)}, \
            "血缘损坏时不得反向对账删文件"

    def test_prune_cleans_stale_tmp_files(self, manager, world):
        """快照原子写入中途崩溃残留的临时文件被清理。"""
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual")
        stale = "@2026-01-01-000000-x-auto.ascendsave.tmp-abc123"
        with open(os.path.join(manager.snapshot_dir(world), stale), "wb") as f:
            f.write(b"partial")
        manager.prune_snapshots(world)
        assert not os.path.exists(
            os.path.join(manager.snapshot_dir(world), stale),
        ), "残留临时文件应被清理"

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
        assert len(autos) == AUTO_SNAPSHOT_KEEP + 1, \
            "auto 环形上限 + 当前记录（live_origin）额外保护"

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
        assert len(autos) == 3, "auto 环形 2 + 当前记录（live_origin）"
        assert len(quits) == 1, "quit 环形仅保留最近 1 个（当前记录是 auto）"


class TestSnapshotRemove:
    """快照删除原语 remove_snapshots — 血缘森林节点集合移除。

    三种删除（单点 / 分支裁剪 / 保留策略）共用同一原语：
    删除集由调用方计算，原语负责血缘重接、live_origin 回退与文件删除。
    """

    def test_remove_single_reparents_children(self, manager, world):
        """单点删除：直接子节点上溯重接到最近存活祖先。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        s3 = manager.create_snapshot(world, suffix="manual")
        assert manager.remove_snapshots(world, [s2]) == [s2]
        lineage = manager.snapshot_lineage(world)
        assert s2 not in lineage["snapshots"], "血缘条目随删除移除"
        assert lineage["snapshots"][s3]["parent"] == s1, "子节点重接到祖父"
        assert not os.path.isfile(
            os.path.join(manager.snapshot_dir(world), s2)), "快照文件被删除"

    def test_remove_batch_reattaches_to_nearest_survivor(self, manager, world):
        """批量删除中间层级：子节点跨级上溯到最近存活祖先（结构性重接）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        s3 = manager.create_snapshot(world, suffix="manual")
        s4 = manager.create_snapshot(world, suffix="manual")
        manager.remove_snapshots(world, [s2, s3])
        lineage = manager.snapshot_lineage(world)
        assert s2 not in lineage["snapshots"]
        assert s3 not in lineage["snapshots"]
        assert lineage["snapshots"][s4]["parent"] == s1, "跨两级重接到最近存活祖先"

    def test_remove_chain_head_reattaches_to_initial(self, manager, world):
        """删除链头及其后代：剩余节点重接到世界初始（""）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        s3 = manager.create_snapshot(world, suffix="manual")
        manager.remove_snapshots(world, [s1, s2])
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][s3]["parent"] == ""

    def test_remove_keeps_lineage_invariant(self, manager, world):
        """删除后血缘恒满足 parent ∈ 存活集 or ''（结构性保证，非防御路径）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        manager.set_live_origin(world, s1)  # 回滚分叉
        s3 = manager.create_snapshot(world, suffix="manual")  # 挂在 s1 下
        s4 = manager.create_snapshot(world, suffix="manual")  # 挂在 s3 下
        manager.remove_snapshots(world, [s1, s2, s3])
        lineage = manager.snapshot_lineage(world)
        for name, entry in lineage["snapshots"].items():
            assert entry["parent"] in lineage["snapshots"] or entry["parent"] == "", \
                f"血缘自洽被破坏: {name} → {entry['parent']}"
        assert lineage["snapshots"][s4]["parent"] == "", "唯一幸存者重接到世界初始"

    def test_remove_live_origin_walks_up_to_survivor(self, manager, world):
        """live_origin 被删：沿 parent 链上溯到最近存活祖先。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        rec = manager.snapshot_lineage(world)["live_origin"]  # s2 下游记录
        manager.remove_snapshots(world, [s2, rec])
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == s1, "上溯跨过已删层级"

    def test_remove_live_origin_whole_chain(self, manager, world):
        """live_origin 所在整链删除：回退到世界初始（""）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        rec = manager.snapshot_lineage(world)["live_origin"]
        manager.remove_snapshots(world, [s1, s2, rec])
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == ""

    def test_remove_keeps_seq_holes(self, manager, world):
        """seq 保留空洞：seq 是创建序号（出生序），删除不重编号。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        s3 = manager.create_snapshot(world, suffix="manual")
        manager.remove_snapshots(world, [s2])
        lineage = manager.snapshot_lineage(world)
        assert lineage["snapshots"][s1]["seq"] == 0
        assert lineage["snapshots"][s3]["seq"] == 2, "空洞保留，不重编号"

    def test_remove_unknown_tolerated(self, manager, world):
        """删除不在血缘中的快照无操作（容忍外部删文件后的清理）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual")
        assert manager.remove_snapshots(
            world, ["@2020-01-01-000000-000000-manual.ascendsave"],
        ) == []
        assert len(manager.snapshot_lineage(world)["snapshots"]) == 2, \
            "保存节点 + 当前记录均保留"

    def test_remove_missing_file_still_cleans_lineage(self, manager, world):
        """文件已缺失的条目仍被清理（孤儿清理并入原语）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        s2 = manager.create_snapshot(world, suffix="manual")
        os.remove(os.path.join(manager.snapshot_dir(world), s1))
        manager.remove_snapshots(world, [s1])
        lineage = manager.snapshot_lineage(world)
        assert s1 not in lineage["snapshots"], "孤儿血缘条目被清理"
        assert lineage["snapshots"][s2]["parent"] == "", "子节点重接到祖父"


class TestSnapshotBranchPrune:
    """分支裁剪 — remove_snapshot_branch（节点 + 全部后代）。"""

    def _build_fork(self, manager, world) -> dict:
        """构造分叉血缘: A → B → C1 → C1a, B → C2（Issue #32 场景）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        a = manager.create_snapshot(world, suffix="manual")
        b = manager.create_snapshot(world, suffix="manual")
        c1 = manager.create_snapshot(world, suffix="manual")
        c1a = manager.create_snapshot(world, suffix="manual")
        manager.set_live_origin(world, b)
        c2 = manager.create_snapshot(world, suffix="manual")
        return {"a": a, "b": b, "c1": c1, "c1a": c1a, "c2": c2}

    def test_prune_branch_deletes_subtree_only(self, manager, world):
        """子树（节点 + 后代，含 auto 当前记录）全部删除，兄弟分支不受影响。

        注：Issue #32 例子「裁剪 C1 → 删 C1、C1a、C2」有误——C2 挂 B 下
        是 C1 的兄弟，不是后代；子树定义 = 节点 + 后代。
        """
        s = self._build_fork(manager, world)
        entries = manager.snapshot_lineage(world)["snapshots"]
        rec_c1a = [f for f, e in entries.items() if e["parent"] == s["c1a"]][0]
        assert set(manager.remove_snapshot_branch(world, s["c1"])) == \
            {s["c1"], s["c1a"], rec_c1a}, "C1 + C1a + 其下当前记录"
        lineage = manager.snapshot_lineage(world)
        assert s["c1"] not in lineage["snapshots"]
        assert s["c1a"] not in lineage["snapshots"]
        for name in (s["a"], s["b"], s["c2"]):
            assert name in lineage["snapshots"], f"无关节点 {name} 保留"
        assert lineage["snapshots"][s["c2"]]["parent"] == s["b"], "兄弟分支不受影响"
        assert not os.path.isfile(
            os.path.join(manager.snapshot_dir(world), s["c1"]))

    def test_prune_branch_live_origin_falls_back(self, manager, world):
        """live_origin 位于被裁子树内：回退到子树根的 parent。"""
        s = self._build_fork(manager, world)
        manager.set_live_origin(world, s["c1a"])
        manager.remove_snapshot_branch(world, s["c1"])
        lineage = manager.snapshot_lineage(world)
        assert lineage["live_origin"] == s["b"], "回退到子树根的父节点"

    def test_prune_branch_unknown_tolerated(self, manager, world):
        """裁剪不在血缘中的节点无操作。"""
        assert manager.remove_snapshot_branch(
            world, "@2020-01-01-000000-000000-manual.ascendsave",
        ) == []


class TestWorldIdValidation:
    """world_id 格式校验：路径穿越与非法 ID 全部拒绝。"""

    @pytest.mark.parametrize("bad_id", [
        "../../../etc",
        "..",
        "/abs/path",
        "w1",
        "0" * 31,
        "Z" * 32,  # 大写十六进制也不放行
        "0" * 32 + "/x",
        "0" * 32 + "\\x",
        "",
    ])
    def test_invalid_world_id_rejected(self, manager, bad_id):
        """非法 world_id 全部抛 ValueError（路径穿越注入）。"""
        with pytest.raises(ValueError):
            manager.get_manifest(bad_id)
        with pytest.raises(ValueError):
            manager.delete_world(bad_id)
        with pytest.raises(ValueError):
            manager.rename_world(bad_id, "x")
        with pytest.raises(ValueError):
            manager.write_state(bad_id, {})
        with pytest.raises(ValueError):
            manager.create_snapshot(bad_id)

    def test_valid_world_id_accepted(self, manager):
        """合法格式的未知 ID 走正常不存在路径（SaveFormatError）。"""
        with pytest.raises(SaveFormatError):
            manager.get_manifest("ab" * 16)


class TestWorldOps:
    """世界级管理操作。"""

    def test_delete_world(self, manager, world):
        """删除整个世界目录。"""
        manager.delete_world(world)
        assert not os.path.isdir(manager.world_dir(world))
        with pytest.raises(SaveFormatError):
            manager.get_manifest(world)

    def test_delete_unknown_raises(self, manager):
        """删除不存在的世界（合法 ID 格式）报 SaveFormatError。"""
        with pytest.raises(SaveFormatError):
            manager.delete_world("f" * 32)

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
        """导出的世界携带原快照（保存节点 + auto 当前记录）。"""
        manager.create_snapshot(world)
        new_id = manager.export_world(world)
        assert len(manager.list_snapshots(new_id)) == 2
        assert len(manager.snapshot_lineage(new_id)["snapshots"]) == 2

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


class TestExtractCrashRecovery:
    """回滚换目录的崩溃自愈（P0-06：extract 两段 rename 崩溃窗口）。"""

    def _write_pending(self, manager, world, snap_name):
        """手工写挂起标记（模拟崩溃残留，格式与实现一致）。"""
        path = os.path.join(manager.root, f".extract-pending-{world}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"snapshot": snap_name}, f)

    def _fake_materialize(self, backup, tmp):
        """把墓碑内活文件复制为"物化产物"（资产不随快照打包，不复制）。"""
        os.makedirs(tmp, exist_ok=True)
        for name in (MANIFEST_NAME, STATE_FILE, CHUNKS_DB, EVENTS_DB, ENTITIES_FILE):
            src = os.path.join(backup, name)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(tmp, name))

    def _assert_root_clean(self, manager):
        """断言存档根目录无任何点号前缀残留。"""
        leftovers = [e for e in os.listdir(manager.root) if e.startswith(".")]
        assert leftovers == [], f"崩溃残留未清理: {leftovers}"

    def test_recovers_swap_interrupted_between_renames(self, manager, world):
        """①与②之间崩溃（活目录缺失）→ 向前补全：临时目录上位、旧目录抛弃。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        snap_path = os.path.join(manager.snapshot_dir(world), snap)
        wdir = manager.world_dir(world)
        backup = os.path.join(manager.root, f".old-{world}")
        tmp = os.path.join(manager.root, f".extract-{world}")
        os.rename(wdir, backup)  # ① 已执行
        self._fake_materialize(backup, tmp)  # 物化产物（模拟）
        self._write_pending(manager, world, snap)
        # 进程重启（新管理器构造）→ 自愈
        mgr2 = SaveManager(root=manager.root)
        assert os.path.isdir(wdir)
        assert os.path.isfile(os.path.join(wdir, MANIFEST_NAME))
        assert os.path.isfile(snap_path), "回退点应搬回新活目录"
        assert mgr2.snapshot_lineage(world)["live_origin"] == snap
        assert not os.path.exists(backup)
        assert not os.path.exists(tmp)
        self._assert_root_clean(manager)
        assert [w["world_id"] for w in mgr2.list_worlds()] == [world]
        # 再构造一次：自愈幂等，世界不受影响
        mgr3 = SaveManager(root=manager.root)
        assert os.path.isdir(manager.world_dir(world))
        assert mgr3.read_state(world)["clock"]["time"] == 100

    def test_recovers_swap_landed_before_asset_moves(self, manager, world):
        """②与③之间崩溃（活目录已上位）→ 补搬资产后抛弃旧目录。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        wdir = manager.world_dir(world)
        backup = os.path.join(manager.root, f".old-{world}")
        tmp = os.path.join(manager.root, f".extract-{world}")
        os.rename(wdir, backup)
        self._fake_materialize(backup, tmp)
        os.rename(tmp, wdir)  # ② 已执行、③ 未执行
        self._write_pending(manager, world, snap)
        mgr2 = SaveManager(root=manager.root)
        assert os.path.isfile(os.path.join(wdir, MANIFEST_NAME))
        assert os.path.isfile(os.path.join(wdir, "snapshots", snap))
        assert mgr2.snapshot_lineage(world)["live_origin"] == snap
        assert not os.path.exists(backup)
        self._assert_root_clean(manager)

    def test_marker_only_residue_cleaned_without_touching_world(self, manager, world):
        """仅剩挂起标记（回滚已全部完成）→ 清理标记，不动世界。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        manager.set_live_origin(world, snap)  # 模拟 live_origin 已还原
        self._write_pending(manager, world, snap)
        mgr2 = SaveManager(root=manager.root)
        assert os.path.isdir(manager.world_dir(world))
        assert mgr2.snapshot_lineage(world)["live_origin"] == snap
        assert os.path.isfile(os.path.join(manager.snapshot_dir(world), snap))
        self._assert_root_clean(manager)

    def test_recovers_tombstone_when_tmp_missing(self, manager, world):
        """防御分支：临时目录缺失 → 墓碑整目录移回（回滚按未发生处理）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        wdir = manager.world_dir(world)
        backup = os.path.join(manager.root, f".old-{world}")
        os.rename(wdir, backup)
        self._write_pending(manager, world, snap)
        mgr2 = SaveManager(root=manager.root)
        assert os.path.isdir(wdir)
        assert mgr2.read_state(world)["clock"]["time"] == 100
        assert os.path.isfile(os.path.join(wdir, "snapshots", snap))
        self._assert_root_clean(manager)

    def test_recovers_legacy_uuid_tombstone(self, manager, world):
        """历史遗留 uuid 命名墓碑：按墓碑内 manifest 反查世界并移回。"""
        manager.write_state(world, {"clock": {"time": 100}})
        wdir = manager.world_dir(world)
        legacy = os.path.join(manager.root, ".old-deadbeefdeadbeef")
        os.rename(wdir, legacy)
        mgr2 = SaveManager(root=manager.root)
        assert os.path.isdir(wdir)
        assert mgr2.read_state(world)["clock"]["time"] == 100
        assert not os.path.exists(legacy)
        self._assert_root_clean(manager)

    def test_recovery_merges_lineage_when_live_has_newer(self, manager, world):
        """罕见态：交换已落定、资产搬运曾失败（标记已移除）、活目录
        已产生新血缘 → 自愈合并旧条目，不回写 live_origin。"""
        manager.write_state(world, {"clock": {"time": 100}})
        manager.create_snapshot(world, suffix="manual")
        manager.write_state(world, {"clock": {"time": 200}})
        s2 = manager.create_snapshot(world, suffix="manual")
        old_entries = set(manager.snapshot_lineage(world)["snapshots"].keys())
        wdir = manager.world_dir(world)
        backup = os.path.join(manager.root, f".old-{world}")
        tmp = os.path.join(manager.root, f".extract-{world}")
        os.rename(wdir, backup)
        self._fake_materialize(backup, tmp)
        os.rename(tmp, wdir)
        # 活目录已产生"新血缘"（模拟回滚后新记录）：仅含 live_origin
        manager.set_live_origin(world, "newer-auto.ascendsave")
        mgr2 = SaveManager(root=manager.root)
        lineage = mgr2.snapshot_lineage(world)
        assert lineage["live_origin"] == "newer-auto.ascendsave", "活目录版本优先"
        assert old_entries <= set(lineage["snapshots"].keys()), "旧条目应合并保留"
        assert os.path.isfile(os.path.join(wdir, "snapshots", s2))
        self._assert_root_clean(manager)

    def test_recovery_restores_live_origin_when_marker_present(self, manager, world):
        """崩溃于收尾前（标记仍在）→ 自愈重写 live_origin 为被展开的快照。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        wdir = manager.world_dir(world)
        backup = os.path.join(manager.root, f".old-{world}")
        tmp = os.path.join(manager.root, f".extract-{world}")
        os.rename(wdir, backup)
        self._fake_materialize(backup, tmp)
        os.rename(tmp, wdir)
        self._write_pending(manager, world, snap)
        # 模拟"资产已搬、set_live_origin 前崩溃"：血缘已在新活目录、
        # 值为旧来源
        mgr2 = SaveManager(root=manager.root)
        assert mgr2.snapshot_lineage(world)["live_origin"] == snap
        self._assert_root_clean(manager)

    def test_recovers_other_temp_residue(self, manager, world):
        """其它请求期临时目录残留（物化/重基座/预览）启动时一并回收。"""
        for name in (".preview-abc", ".base-def", ".rebase-ghi", ".extract-xyz"):
            os.makedirs(os.path.join(manager.root, name), exist_ok=True)
        SaveManager(root=manager.root)
        self._assert_root_clean(manager)

    def test_list_worlds_excludes_dot_tombstones(self, manager, world):
        """点号前缀残留（含 manifest 的墓碑）不被列成幽灵世界。"""
        ghost_dir = os.path.join(manager.root, ".old-" + "a" * 32)
        os.makedirs(ghost_dir, exist_ok=True)
        with open(manager.manifest_path(world), encoding="utf-8") as f:
            data = json.load(f)
        data["name"] = "幽灵世界"
        with open(os.path.join(ghost_dir, MANIFEST_NAME), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        assert [w["world_id"] for w in manager.list_worlds()] == [world]
        # 重名检查同样不把墓碑当世界（幽灵名字可正常创建）
        new_world = manager.create_world("幽灵世界", seed=1)
        assert new_world.world_id != world

    def test_extract_rejects_concurrent_residue(self, manager, world):
        """同进程残留（并发回滚/极端故障）→ 拒绝展开而非互相踩踏。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        snap_path = os.path.join(manager.snapshot_dir(world), snap)
        self._write_pending(manager, world, snap)
        with pytest.raises(SaveFormatError, match="尚未完成"):
            manager.extract_snapshot(snap_path)

    def test_extract_self_heals_soft_failure_residue(self, manager, world):
        """软失败后墓碑残留（无标记）：同会话再次回滚内联自愈，无需重启。

        前端真实路径：裸文件名 + 目标世界——快照文件此刻仍在墓碑
        snapshots/ 内，须先自愈搬回活目录才能解析（回归：自愈在
        resolve_snapshot_path 之前执行）。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        wdir = manager.world_dir(world)
        backup = os.path.join(manager.root, f".old-{world}")
        tmp = os.path.join(manager.root, f".extract-{world}")
        os.rename(wdir, backup)
        self._fake_materialize(backup, tmp)
        os.rename(tmp, wdir)  # 交换已落定、资产未搬、标记已移除（软失败现场）
        assert not os.path.isdir(manager.snapshot_dir(world)), \
            "现场前提：活目录无快照子目录（在墓碑内）"
        assert manager.extract_snapshot(snap, world_id=world) == world
        self._assert_root_clean(manager)
        assert os.path.isfile(os.path.join(manager.snapshot_dir(world), snap)), \
            "内联自愈搬回的回退点应在随后回滚中保全"

    def test_extract_cleans_stale_tmp_dir(self, manager, world):
        """无标记无墓碑的临时目录残留：清理后照常回滚。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        snap_path = os.path.join(manager.snapshot_dir(world), snap)
        stale = os.path.join(manager.root, f".extract-{world}")
        os.makedirs(stale, exist_ok=True)
        assert manager.extract_snapshot(snap_path, world_id=world) == world
        self._assert_root_clean(manager)

    def test_extract_leaves_no_residue_and_preserves_assets(self, manager, world):
        """正常回滚后无墓碑/临时目录/标记残留，资产完整搬回。"""
        manager.write_state(world, {"clock": {"time": 100}})
        snap = manager.create_snapshot(world, suffix="manual")
        wdir = manager.world_dir(world)
        with open(os.path.join(wdir, "continent.bin"), "wb") as f:
            f.write(b"fake-continent")
        snap_path = os.path.join(manager.snapshot_dir(world), snap)
        manager.extract_snapshot(snap_path)
        self._assert_root_clean(manager)
        with open(os.path.join(wdir, "continent.bin"), "rb") as f:
            assert f.read() == b"fake-continent"
        assert os.path.isfile(os.path.join(wdir, "snapshots", snap))
        assert manager.snapshot_lineage(world)["live_origin"] == snap


class TestLineageTamperProtection:
    """血缘签名防护（P0-07：lineage.json 无签名 → prune 可被借刀误删）。"""

    def test_prune_skips_when_lineage_unsigned(self, manager, world):
        """无签名血缘（历史格式）→ prune 零淘汰、零删除。"""
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        # 把血缘退回无签名历史格式（模拟升级前的旧档）
        with open(manager.lineage_path(world), encoding="utf-8") as f:
            payload = json.load(f)
        with open(manager.lineage_path(world), "w", encoding="utf-8") as f:
            json.dump(payload["data"], f)
        assert manager.prune_snapshots(world) == 0
        assert os.path.isfile(os.path.join(manager.snapshot_dir(world), s1))

    def test_prune_skips_when_lineage_tampered(self, manager, world):
        """血缘 data 被篡改 → prune 零删除（宁缺勿删）。

        借刀手法复现：把 manual 条目从血缘抹掉，若无签名防线，
        反向对账会把该文件当幽灵删除。
        """
        manager.write_state(world, {"clock": {"time": 100}})
        s1 = manager.create_snapshot(world, suffix="manual")
        rec = manager.snapshot_lineage(world)["live_origin"]
        with open(manager.lineage_path(world), encoding="utf-8") as f:
            payload = json.load(f)
        payload["data"]["snapshots"].pop(s1)
        with open(manager.lineage_path(world), "w", encoding="utf-8") as f:
            json.dump(payload, f)
        assert manager.prune_snapshots(world) == 0
        assert {s1, rec} <= {s["file"] for s in manager.list_snapshots(world)}


class TestSnapshotRebind:
    """复制档快照改绑新世界 ID（P0-08：复制档自愈，不依赖原世界）。"""

    def test_export_rebinds_snapshot_identity(self, manager, world):
        """导出后快照头部与内嵌 manifest 均为副本 ID，钥匙可独立解锁。"""
        manager.write_state(world, {"clock": {"time": 100}})
        filename = manager.create_snapshot(world, suffix="manual")
        new_id = manager.export_world(world)
        copied = os.path.join(manager.snapshot_dir(new_id), filename)
        header, zf = manager._snap.open_snapshot(copied)
        try:
            assert header["world_id"] == new_id
            manifest = Manifest.from_dict(
                json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            )
        finally:
            zf.close()
        assert manifest.world_id == new_id
        # 副本 ID 可解出钥匙（混淆层已换绑）
        SaveKeys.from_protected(manifest.secrets_blob, new_id, manifest.seed)

    def test_copied_delta_previews_after_original_deleted(self, manager, world):
        """原世界删除后，复制档增量快照仍可预览（沿副本链解析）。"""
        manager.write_state(world, {"clock": {"time": 100}})
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        delta = "@2026-01-01-000000-test-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE], {},
        )
        manager._record_snapshot_lineage(world, delta, 200, 0.0)
        new_id = manager.export_world(world)
        manager.delete_world(world)
        state = manager.read_snapshot_state(
            os.path.join(manager.snapshot_dir(new_id), delta),
        )
        assert state["clock"]["time"] == 200

    def test_copied_delta_extracts_standalone(self, manager, world):
        """复制档增量快照不带覆盖即可展开，原世界不受影响。"""
        manager.write_state(world, {"clock": {"time": 100}})
        base = manager.create_snapshot(world, suffix="manual", game_time=100)
        manager.write_state(world, {"clock": {"time": 200}})
        delta = "@2026-01-01-000000-test-auto.ascendsave"
        _build_delta_snapshot(
            os.path.join(manager.snapshot_dir(world), delta),
            base, manager.world_dir(world),
            [MANIFEST_NAME, STATE_FILE], {},
        )
        manager._record_snapshot_lineage(world, delta, 200, 0.0)
        new_id = manager.export_world(world)
        assert manager.extract_snapshot(
            os.path.join(manager.snapshot_dir(new_id), delta),
        ) == new_id
        assert manager.read_state(new_id)["clock"]["time"] == 200
        assert manager.read_state(world)["clock"]["time"] == 200, \
            "原世界活目录不受副本回滚影响"

    def test_export_fails_on_corrupt_snapshot(self, manager, world):
        """任一快照损坏 → 导出整体失败，不留半成品目录。"""
        manager.write_state(world, {"clock": {"time": 100}})
        filename = manager.create_snapshot(world, suffix="manual")
        path = os.path.join(manager.snapshot_dir(world), filename)
        data = bytearray(open(path, "rb").read())
        data[len(data) // 2] ^= 0xFF
        with open(path, "wb") as f:
            f.write(bytes(data))
        before = {w["world_id"] for w in manager.list_worlds()}
        with pytest.raises(SaveCryptoError):
            manager.export_world(world)
        assert {w["world_id"] for w in manager.list_worlds()} == before, \
            "导出失败不得留下半成品目录"
        leftovers = [e for e in os.listdir(manager.root) if e.startswith(".")]
        assert leftovers == [], f"导出失败残留: {leftovers}"
