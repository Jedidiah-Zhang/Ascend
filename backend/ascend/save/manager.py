"""存档管理器 — 存档位的创建、实时状态写入、手动快照与管理操作。

纯磁盘层：不依赖 GameEngine / 实体等运行时子系统。
GameEngine 负责把运行时状态喂给 write_state，读档时从 read_state 恢复。

目录结构（docs/世界框架/存档系统/设计.md）:
    <root>/<world_id>/                  # 一个存档位 = 一个目录 = 一个世界
        manifest.json          # 明文元信息 + 密钥混淆串 secrets_blob
        state.json.enc         # 时钟/玩家/天气（加密 + HMAC）
        entities.json.enc      # 实体表（Issue #25 启用）
        chunks.db / events.db  # SQLite（明文，实时增量写）
        continent.bin          # 大陆宏观场缓存（可再生，随档分发）
        snapshots/             # 该世界的回退点集合（回滚时保留自身）
            @<ts>-<suffix>.ascendsave

快照作为世界目录内的兄弟子目录：回滚 = 用快照内容替换活文件，
快照子目录在交换期间先移出再放回，自身不受影响（否则回滚会
删除自己的回退点）。

快照 .ascendsave 格式（自包含解锁，可移植）:
    第一行: 明文 JSON {"format": "ascendsave", "version": 1,
                        "key": <b64>, "sign_key": <b64>}
    随后:   HMAC(sign_key, payload) || Fernet(key)(payload)
    payload = zip 打包的活目录文件字节
"""

import io
import json
import os
import random
import shutil
import time as _real_time
import uuid
import zipfile

from ascend.config import SAVE_ROOT
from ascend.log import get_logger

from .crypto import SaveKeys, SaveCryptoError
from .manifest import Manifest, SaveFormatError, MANIFEST_NAME

logger = get_logger(__name__)

STATE_FILE: str = "state.json.enc"
ENTITIES_FILE: str = "entities.json.enc"
CHUNKS_DB: str = "chunks.db"
EVENTS_DB: str = "events.db"
SNAPSHOT_DIR: str = "snapshots"
SNAPSHOT_SUFFIX: str = ".ascendsave"
# 快照血缘（时间线分叉）元数据：{live_origin, snapshots: {file: {parent, game_time, saved_at, seq}}}
LINEAGE_FILE: str = "lineage.json"
# 保留策略：auto（回滚保护）环形保留最近 N 个，quit（退出保存）保留最近 K 个；
# manual（手动）永久保留。live_origin 指向的快照永不自动淘汰
# （当前时间点的来源），因此同一来源的实际上限 = N + 1。
AUTO_SNAPSHOT_KEEP: int = 20
QUIT_SNAPSHOT_KEEP: int = 3
# 大陆宏观场缓存（可再生数据，随档分发保证换机后首次加载也秒开）
CONTINENT_FILE: str = "continent.bin"

# 快照打包的固定文件集合（密钥藏于 manifest.secrets_blob，无需独立文件；
# continent.bin 可再生，不进快照，保持回退点精简；lineage 为世界级元数据，
# 不随快照打包——快照依赖世界内 lineage 提供父子上下文）
_SNAPSHOT_ENTRIES: tuple[str, ...] = (
    MANIFEST_NAME, STATE_FILE, ENTITIES_FILE, CHUNKS_DB, EVENTS_DB,
)

# 存档位活目录中的规范文件集合（导出/复制只拷这些，排除 WAL/临时文件；
# 含 continent.bin——同 seed 确定性产物，随档复制保证副本首次加载秒开；
# lineage.json 随档复制，保证副本的时间线上下文完整）
_LIVE_ENTRIES: tuple[str, ...] = _SNAPSHOT_ENTRIES + (CONTINENT_FILE, LINEAGE_FILE)


class SaveManager:
    """存档位磁盘操作。

    用法:
        mgr = SaveManager("/path/to/saves")
        manifest = mgr.create_world("我的世界", seed=12345)
        mgr.write_state(world_id, state_dict)
        mgr.create_snapshot(world_id, suffix="manual")
    """

    def __init__(self, root: str = SAVE_ROOT) -> None:
        """初始化存档管理器。

        Args:
            root: 存档根目录（默认 ~/.ascend/saves，可在设置调整）。
        """
        self._root = os.path.abspath(root)
        os.makedirs(self._root, exist_ok=True)
        logger.info("存档管理器就绪: %s", self._root)

    def __repr__(self) -> str:
        return f"SaveManager(root={self._root})"

    # ── 路径 ──────────────────────────────────────────────

    @property
    def root(self) -> str:
        """存档根目录。"""
        return self._root

    def world_dir(self, world_id: str) -> str:
        """指定世界的目录（一个存档位 = 一个目录）。"""
        return os.path.join(self._root, world_id)

    def snapshot_dir(self, world_id: str) -> str:
        """世界的快照子目录（与活文件同目录，回滚时保留）。"""
        return os.path.join(self.world_dir(world_id), SNAPSHOT_DIR)

    def manifest_path(self, world_id: str) -> str:
        return os.path.join(self.world_dir(world_id), MANIFEST_NAME)

    def state_path(self, world_id: str) -> str:
        return os.path.join(self.world_dir(world_id), STATE_FILE)

    def lineage_path(self, world_id: str) -> str:
        """世界血缘文件路径（时间线分叉元数据）。"""
        return os.path.join(self.world_dir(world_id), LINEAGE_FILE)

    def chunks_db_path(self, world_id: str) -> str:
        """世界专属 chunk 数据库路径（引擎直接以该路径打开）。"""
        return os.path.join(self.world_dir(world_id), CHUNKS_DB)

    def events_db_path(self, world_id: str) -> str:
        """世界专属事件归档路径。"""
        return os.path.join(self.world_dir(world_id), EVENTS_DB)

    def continent_path(self, world_id: str) -> str:
        """大陆宏观场缓存路径（可再生数据，随档分发）。"""
        return os.path.join(self.world_dir(world_id), CONTINENT_FILE)

    # ── 世界管理 ──────────────────────────────────────────

    def create_world(self, name: str, seed: int, *, world_id: str | None = None) -> Manifest:
        """创建新的存档位（活目录 + 密钥 + 初版 manifest）。

        种子在创建时定案：seed=0（前端"随机"占位）在此随机化并写入
        manifest——存档身份（world_id+seed）出生即一致，密钥混淆层
        （secrets_blob 绑定 world_id+seed）不会与 manifest 失配
        （回归：旧版在首次进入时随机化，导致 blob 身份失配、
        state 加解密全部失败）。

        Args:
            name: 存档名称（存档选择页展示；全集合唯一，重名拒绝）。
            seed: 世界种子；0 = 创建时随机。
            world_id: 可选，指定 ID（测试用）；缺省自动生成。

        Returns:
            新建的 Manifest。

        Raises:
            ValueError: 名称为空或与现有存档重名。
        """
        name = str(name).strip()
        if not name:
            raise ValueError("存档名称不能为空")
        self._ensure_name_unique(name)
        seed = int(seed)
        if seed == 0:
            seed = random.randint(1, 2**31 - 1)
        world_id = world_id or uuid.uuid4().hex
        wdir = self.world_dir(world_id)
        os.makedirs(wdir, exist_ok=False)
        os.makedirs(self.snapshot_dir(world_id), exist_ok=True)
        now = _real_time.time()
        manifest = Manifest(
            name=name, seed=seed, world_id=world_id,
            created_at=now, last_played_at=now,
        )
        # 密钥不落盘明文：加密后藏入 manifest.secrets_blob 随档分发
        manifest.secrets_blob = SaveKeys.generate().protect(world_id, seed)
        manifest.write(self.manifest_path(world_id))
        logger.info("创建存档位: %s (%s, seed=%d)", world_id, name, seed)
        return manifest

    def rekey(self, world_id: str, *, old_seed: int, new_seed: int) -> None:
        """存档身份（seed）变更后重混淆密钥并回写 manifest。

        密钥混淆层绑定 (world_id, seed)；seed 变更后须用旧 seed 解出
        密钥、新 seed 重新混淆，否则后续 state 加解密解不出密钥。
        供旧版 seed=0 存档首次进入随机化时迁移使用。

        Args:
            world_id: 世界 ID。
            old_seed: 现 manifest.secrets_blob 派生所用的旧 seed。
            new_seed: 新的世界种子。

        Raises:
            SaveFormatError: 存档不存在。
            SaveCryptoError: 旧 seed 解不出密钥（存档身份不符）。
        """
        manifest = self.get_manifest(world_id)
        keys = SaveKeys.from_protected(
            manifest.secrets_blob, world_id, old_seed,
        )
        manifest.secrets_blob = keys.protect(world_id, new_seed)
        manifest.seed = new_seed
        manifest.write(self.manifest_path(world_id))
        logger.info(
            "存档重混淆: %s seed %d → %d", world_id, old_seed, new_seed,
        )

    def get_manifest(self, world_id: str) -> Manifest:
        """读取世界的 manifest。

        Raises:
            SaveFormatError: 世界不存在或 manifest 损坏。
        """
        path = self.manifest_path(world_id)
        if not os.path.isfile(path):
            raise SaveFormatError(f"存档不存在: {world_id}")
        return Manifest.read(path)

    def list_worlds(self) -> list[dict]:
        """列出所有存档位摘要（存档选择页数据源）。

        损坏的 manifest 跳过并记录警告，不使整个列表失败。

        Returns:
            摘要字典列表（含 snapshot_count / latest_snapshot_at）。
        """
        result: list[dict] = []
        if not os.path.isdir(self._root):
            return result
        for entry in sorted(os.listdir(self._root)):
            path = os.path.join(self._root, entry)
            # 跳过迁移残留/临时目录（.extract-* / .old-* / live / snapshots）
            if not os.path.isdir(path) or not os.path.isfile(
                os.path.join(path, MANIFEST_NAME)
            ):
                continue
            try:
                manifest = Manifest.read(os.path.join(path, MANIFEST_NAME))
            except SaveFormatError:
                logger.warning("跳过损坏存档: %s", entry)
                continue
            snapshots = self.list_snapshots(entry)
            d = manifest.dict
            d.pop("secrets_blob", None)  # 密钥混淆串不下发前端
            d["snapshot_count"] = len(snapshots)
            d["latest_snapshot_at"] = (
                snapshots[-1]["saved_at"] if snapshots else None
            )
            result.append(d)
        return result

    def rename_world(self, world_id: str, name: str) -> Manifest:
        """重命名存档。

        Args:
            world_id: 世界 ID。
            name: 新名称（全集合唯一，重名拒绝；保持自身名称允许）。

        Returns:
            更新后的 Manifest。

        Raises:
            SaveFormatError: 世界不存在。
            ValueError: 名称为空或与其它存档重名。
        """
        name = str(name).strip()
        if not name:
            raise ValueError("存档名称不能为空")
        self._ensure_name_unique(name, exclude_world_id=world_id)
        manifest = self.get_manifest(world_id)
        manifest.name = name
        manifest.write(self.manifest_path(world_id))
        return manifest

    def delete_world(self, world_id: str) -> None:
        """删除整个存档位（含快照）。

        Raises:
            SaveFormatError: 世界不存在。
        """
        wdir = self.world_dir(world_id)
        if not os.path.isdir(wdir):
            raise SaveFormatError(f"存档不存在: {world_id}")
        shutil.rmtree(wdir)
        logger.info("删除存档位: %s", world_id)

    def export_world(self, world_id: str) -> str:
        """复制世界为新的存档位（Issue #14 "复制存档"），含快照。

        只复制活目录的规范文件（manifest/key/state/DB），排除运行期
        残留的 -wal/-shm/.tmp 等垃圾；快照目录整体复制（单文件安全）。
        复制出的快照仍指向原世界，回滚时须以目标 world_id 覆盖
        （见 extract_snapshot 的 world_id 参数）。

        Returns:
            新存档位的 world_id。
        """
        self.get_manifest(world_id)
        new_id = uuid.uuid4().hex
        new_dir = self.world_dir(new_id)
        os.makedirs(new_dir, exist_ok=False)
        os.makedirs(self.snapshot_dir(new_id), exist_ok=True)
        src_dir = self.world_dir(world_id)
        for entry in _LIVE_ENTRIES:
            src = os.path.join(src_dir, entry)
            if os.path.isfile(src):
                shutil.copy2(src, os.path.join(new_dir, entry))
        for filename in os.listdir(self.snapshot_dir(world_id)):
            if not filename.endswith(SNAPSHOT_SUFFIX):
                continue
            shutil.copy2(
                os.path.join(self.snapshot_dir(world_id), filename),
                os.path.join(self.snapshot_dir(new_id), filename),
            )
        # 新世界的 manifest 换 ID，其余元信息保留；
        # 密钥混淆层绑定存档身份：须用原 ID 解出、新 ID 重新混淆
        new_manifest = Manifest.read(self.manifest_path(new_id))
        new_manifest.world_id = new_id
        # 名称唯一：副本追加"副本"后缀（原档仍占用原名称）
        new_manifest.name = self._unique_copy_name(
            new_manifest.name, exclude_world_id=new_id,
        )
        if new_manifest.secrets_blob:
            keys = SaveKeys.from_protected(
                new_manifest.secrets_blob, world_id, new_manifest.seed,
            )
            new_manifest.secrets_blob = keys.protect(new_id, new_manifest.seed)
        new_manifest.created_at = _real_time.time()
        new_manifest.write(self.manifest_path(new_id))
        logger.info("复制存档: %s → %s (%s)", world_id, new_id, new_manifest.name)
        return new_id

    # ── 实时状态 ──────────────────────────────────────────

    def write_state(self, world_id: str, state: dict) -> None:
        """加密写入 state.json.enc（原子替换）。

        Args:
            world_id: 世界 ID。
            state: collect_state 输出的状态字典。

        Raises:
            SaveFormatError: 世界不存在。
        """
        keys = self._load_keys(world_id)
        payload = keys.encrypt(
            json.dumps(state, ensure_ascii=False).encode("utf-8")
        )
        tmp = self.state_path(world_id) + ".tmp"
        with open(tmp, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.state_path(world_id))

    def read_state(self, world_id: str) -> dict:
        """解密读取 state.json.enc。

        Raises:
            SaveFormatError: 世界不存在或状态文件缺失。
            SaveCryptoError: 解密/签名校验失败（被篡改）。
        """
        path = self.state_path(world_id)
        if not os.path.isfile(path):
            raise SaveFormatError(f"存档状态缺失: {world_id}")
        keys = self._load_keys(world_id)
        with open(path, "rb") as f:
            payload = f.read()
        try:
            data = keys.decrypt(payload)
        except SaveCryptoError as exc:
            raise SaveCryptoError(
                f"存档 {world_id} 状态校验失败: {exc}"
            ) from exc
        try:
            return json.loads(data.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise SaveFormatError(f"存档状态损坏: {exc}") from exc

    # ── 快照血缘（时间线分叉） ─────────────────────────────

    def snapshot_lineage(self, world_id: str) -> dict:
        """读取世界血缘：快照的父子关系与当前活目录来源。

        Returns:
            {"live_origin": str|"", "snapshots": {file: {parent, game_time,
             saved_at, seq}}}。文件缺失时返回空血缘（初始世界无快照）。
            旧版条目（无 seq）按 (saved_at, game_time) 合成一次——
            seq 是唯一的权威排序键，其余时间字段仅作展示。
        """
        default: dict = {"live_origin": "", "snapshots": {}}
        path = self.lineage_path(world_id)
        if not os.path.isfile(path):
            return default
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("血缘文件损坏，按空血缘处理: %s (%s)", path, exc)
            return default
        if not isinstance(data, dict):
            return default
        data.setdefault("live_origin", "")
        data.setdefault("snapshots", {})
        if not isinstance(data["snapshots"], dict):
            data["snapshots"] = {}
        self._migrate_lineage_seqs(data["snapshots"])
        return data

    @staticmethod
    def _migrate_lineage_seqs(snapshots: dict) -> None:
        """为旧版血缘条目合成 seq（权威排序键）。

        任一条目缺 seq 即整体按 (saved_at, game_time, file) 重排编号
        （一次性迁移；随后每次写入自然落盘）。保证「血缘内 seq 唯一、
        与创建顺序一致」，是时间线/编号/串链的单一事实来源。
        """
        entries = [
            (name, entry) for name, entry in snapshots.items()
            if isinstance(entry, dict) and "seq" not in entry
        ]
        if not entries:
            return
        existing = [
            int(entry["seq"]) for entry in snapshots.values()
            if isinstance(entry, dict) and "seq" in entry
        ]
        next_seq = (max(existing) + 1) if existing else 0
        entries.sort(key=lambda kv: (
            float(kv[1].get("saved_at", 0.0)),
            int(kv[1].get("game_time", 0)),
            kv[0],
        ))
        for _name, entry in entries:
            entry["seq"] = next_seq
            next_seq += 1

    def _write_lineage(self, world_id: str, lineage: dict) -> None:
        """原子写入血缘文件（世界外元数据，失败不阻断主流程）。"""
        try:
            tmp = self.lineage_path(world_id) + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(lineage, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.lineage_path(world_id))
        except OSError as exc:
            logger.warning("血缘文件写入失败: %s (%s)", world_id, exc)

    def _record_snapshot_lineage(
        self, world_id: str, filename: str,
        game_time: int, saved_at: float,
    ) -> None:
        """记录快照血缘条目并更新活目录来源。

        parent = 创建时活目录来源（最近一个快照 / "" = 世界初始）；
        创建后活目录来源更新为该快照——活状态从快照内容继续，
        连续保存（含回滚保护等自动快照）自动串链：后一个快照
        从最近一个派生。回滚（extract_snapshot）会再次改来源。

        seq = 世界内单调递增的权威排序键（创建顺序，不受回滚后
        游戏时间倒退影响），时间线/编号/串链排序的唯一事实来源。
        """
        lineage = self.snapshot_lineage(world_id)
        seqs = [
            int(entry["seq"]) for entry in lineage["snapshots"].values()
            if isinstance(entry, dict) and "seq" in entry
        ]
        lineage["snapshots"][filename] = {
            "parent": lineage.get("live_origin", ""),
            "game_time": int(game_time),
            "saved_at": float(saved_at),
            "seq": (max(seqs) + 1) if seqs else 0,
        }
        lineage["live_origin"] = filename
        self._write_lineage(world_id, lineage)

    def set_live_origin(self, world_id: str, snapshot_file: str) -> None:
        """记录活目录来源：回滚后调用，标记当前时间点从该快照派生。"""
        lineage = self.snapshot_lineage(world_id)
        lineage["live_origin"] = snapshot_file
        self._write_lineage(world_id, lineage)

    # ── 快照 ──────────────────────────────────────────────

    def create_snapshot(
        self, world_id: str, *, suffix: str = "manual",
        game_time: int | None = None,
    ) -> str:
        """把活目录打包为加密快照单文件（手动保存/回滚保护）。

        Args:
            world_id: 世界 ID。
            suffix: 快照来源标识（manual/auto/quit），拼入文件名。
            game_time: 创建时刻的世界时间（tick）；None 时从活目录
                state.json 读取（引擎路径传入更准，磁盘路径兜底）。

        Returns:
            快照文件名（不含目录）。

        Raises:
            SaveFormatError: 世界不存在。
        """
        manifest = self.get_manifest(world_id)
        wdir = self.world_dir(world_id)
        # 唯一段置于 suffix 前：list_snapshots 的 rsplit("-", 1)[-1]
        # 解析与按文件名排序（时间序）均不受影响；同秒多次快照不覆盖
        stamp = _real_time.strftime("%Y-%m-%d-%H%M%S")
        uniq = uuid.uuid4().hex[:6]
        filename = f"@{stamp}-{uniq}-{suffix}{SNAPSHOT_SUFFIX}"
        path = os.path.join(self.snapshot_dir(world_id), filename)

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in _SNAPSHOT_ENTRIES:
                src = os.path.join(wdir, entry)
                if os.path.isfile(src):
                    zf.write(src, entry)
        zip_bytes = buffer.getvalue()

        session_key = SaveKeys.generate()
        key_dict = session_key.to_dict()
        header = json.dumps({
            "format": "ascendsave",
            "version": 1,
            "fernet_key": key_dict["fernet_key"],
            "sign_key": key_dict["sign_key"],
        }, ensure_ascii=False).encode("utf-8") + b"\n"
        encrypted = session_key.encrypt(zip_bytes)
        with open(path, "wb") as f:
            f.write(header)
            f.write(encrypted)

        # 血缘记录（时间线分叉数据源）：game_time 缺省时读活目录状态
        if game_time is None:
            try:
                game_time = int(
                    self.read_state(world_id).get("clock", {}).get("time", 0)
                )
            except (SaveFormatError, SaveCryptoError):
                game_time = 0
        self._record_snapshot_lineage(
            world_id, filename, game_time, _real_time.time(),
        )
        logger.info("创建快照: %s → %s", world_id, filename)
        # 保留策略：每次创建后淘汰超量快照（失败不阻断快照本身）
        try:
            self.prune_snapshots(world_id)
        except OSError as exc:
            logger.warning("快照保留策略执行失败: %s (%s)", world_id, exc)
        return filename

    def delete_snapshot(self, world_id: str, filename: str) -> None:
        """删除快照并重接血缘父链（子树提升），保持血缘自洽。

        被删节点的直接子节点 parent 提升为被删节点的 parent；
        live_origin 指向被删节点时同步回退到其 parent（"" = 世界初始）。
        血缘重接后持久化血缘恒满足「parent ∈ 存活集 or ''」，
        时间线的串链回退降级为纯防御路径（suffix 过滤/外部删文件）。

        文件与血缘条目均可缺失（容忍外部删文件后的清理），
        两者皆不存在时仅记录警告。

        Raises:
            OSError: 快照文件删除失败（血缘已先行自洽重接）。
        """
        filename = os.path.basename(str(filename))
        if not filename.endswith(SNAPSHOT_SUFFIX):
            filename += SNAPSHOT_SUFFIX
        lineage = self.snapshot_lineage(world_id)
        snaps = lineage.get("snapshots", {})
        entry = snaps.get(filename)
        if isinstance(entry, dict):
            parent = str(entry.get("parent", ""))
            snaps.pop(filename, None)
            # 子树提升：直接子节点改挂到被删节点的父节点
            for child in snaps.values():
                if isinstance(child, dict) and child.get("parent") == filename:
                    child["parent"] = parent
            if lineage.get("live_origin") == filename:
                lineage["live_origin"] = parent
            self._write_lineage(world_id, lineage)
        path = os.path.join(self.snapshot_dir(world_id), filename)
        if os.path.isfile(path):
            os.remove(path)
            logger.info("删除快照: %s → %s", world_id, filename)
        elif not isinstance(entry, dict):
            logger.warning("快照不存在（文件与血缘条目均缺失）: %s/%s",
                           world_id, filename)

    def prune_snapshots(
        self, world_id: str, *,
        keep_auto: int = AUTO_SNAPSHOT_KEEP,
        keep_quit: int = QUIT_SNAPSHOT_KEEP,
    ) -> int:
        """按保留策略淘汰超量快照（手动快照永久保留）。

        规则：
          - auto（回滚保护）环形保留最近 keep_auto 个；
          - quit（退出保存）保留最近 keep_quit 个；
          - live_origin 指向的快照永不淘汰（当前时间点的来源，
            淘汰会让时间线的「当前点」悬空）；
          - 血缘条目存在但文件已缺失的孤儿条目一并清理（重接父链）。
        淘汰经 delete_snapshot 逐个重接血缘父链，血缘保持自洽。

        Returns:
            淘汰数量。
        """
        sdir = self.snapshot_dir(world_id)
        lineage = self.snapshot_lineage(world_id)
        live_origin = lineage.get("live_origin", "")
        to_delete: list[str] = []

        # 1. 血缘孤儿条目清理（文件已不存在）
        if os.path.isdir(sdir):
            on_disk = set(os.listdir(sdir))
        else:
            on_disk = set()
        for name in lineage.get("snapshots", {}):
            if name not in on_disk and name != live_origin:
                to_delete.append(name)

        # 2. 按来源分组保留策略（组内按血缘 seq = 创建顺序；
        #    文件名同秒内排序任意，不能作时间序依据）
        lineage_by_name: dict[str, int] = {}
        for name, entry in lineage.get("snapshots", {}).items():
            if isinstance(entry, dict):
                lineage_by_name[name] = int(entry.get("seq", -1))
        groups: dict[str, list[tuple[str, int]]] = {}
        if os.path.isdir(sdir):
            for name in sorted(os.listdir(sdir)):
                if not name.endswith(SNAPSHOT_SUFFIX):
                    continue
                suffix = name.removesuffix(SNAPSHOT_SUFFIX).rsplit("-", 1)[-1]
                groups.setdefault(suffix, []).append(
                    (name, lineage_by_name.get(name, -1)),
                )
        for suffix, keep in (("auto", keep_auto), ("quit", keep_quit)):
            group = sorted(groups.get(suffix, []), key=lambda kv: kv[1])
            names = [name for name, _ in group]
            if live_origin in names:
                names.remove(live_origin)
            to_delete.extend(names[:max(0, len(names) - keep)])

        for filename in to_delete:
            self.delete_snapshot(world_id, filename)
        if to_delete:
            logger.info("快照保留策略淘汰 %d 个: %s", len(to_delete), world_id)
        return len(to_delete)

    def list_snapshots(self, world_id: str) -> list[dict]:
        """列出世界的快照（按文件名升序 = 时间序）。

        Returns:
            [{file, saved_at, suffix, size}]。
        """
        sdir = self.snapshot_dir(world_id)
        result: list[dict] = []
        if not os.path.isdir(sdir):
            return result
        for filename in sorted(os.listdir(sdir)):
            if not filename.endswith(SNAPSHOT_SUFFIX):
                continue
            path = os.path.join(sdir, filename)
            stat = os.stat(path)
            suffix = filename.removesuffix(SNAPSHOT_SUFFIX).rsplit("-", 1)[-1]
            result.append({
                "file": filename,
                "saved_at": stat.st_mtime,
                "suffix": suffix,
                "size": stat.st_size,
            })
        return result

    def read_snapshot_state(self, snapshot_path: str) -> dict:
        """读取快照包内的世界状态（回滚前展示/确认用）。

        Args:
            snapshot_path: 快照文件路径（绝对路径或相对当前目录）。

        Returns:
            state 字典。

        Raises:
            SaveCryptoError: 快照解密/校验失败。
            SaveFormatError: 快照内数据损坏。
        """
        header, zf = self._open_snapshot(snapshot_path)
        try:
            # state 由世界密钥加密，密钥藏于快照内 manifest.secrets_blob
            manifest = Manifest.from_dict(
                json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
            )
            world_keys = SaveKeys.from_protected(
                manifest.secrets_blob, manifest.world_id, manifest.seed,
            )
            try:
                state_blob = zf.read(STATE_FILE)
            except KeyError as exc:
                raise SaveFormatError(f"快照缺少状态文件: {snapshot_path}") from exc
            data = world_keys.decrypt(state_blob)
            return json.loads(data.decode("utf-8"))
        finally:
            zf.close()

    def _resolve_snapshot_path(self, snapshot_path: str, world_id: str | None) -> str:
        """快照路径解析：绝对/带目录路径原样使用；裸文件名从目标世界的
        快照目录解析（协议 save_load 下发的是文件名）。

        Returns:
            可打开的快照路径（解析失败时返回原路径，由调用方报错）。
        """
        if os.path.isfile(snapshot_path):
            return snapshot_path
        if not os.path.dirname(snapshot_path) and world_id:
            candidate = os.path.join(self.snapshot_dir(world_id), snapshot_path)
            if os.path.isfile(candidate):
                return candidate
        return snapshot_path

    def extract_snapshot(
        self, snapshot_path: str, world_id: str | None = None,
    ) -> str:
        """把快照展开为活目录（回滚）：解密 → 临时目录 → 原子替换。

        原活目录被覆盖前应已完成自动快照保护（由调用方负责）。

        Args:
            snapshot_path: 快照文件路径（绝对路径、相对路径或文件名——
                文件名从目标世界的快照目录解析）。
            world_id: 目标 world_id 覆盖。快照内 manifest 记录的是
                创建时所属世界；复制存档（export）后快照仍指向原世界，
                回滚到复制档时必须显式传入目标 ID，避免覆盖原世界。

        Returns:
            展开后的 world_id。

        Raises:
            SaveCryptoError: 快照解密/校验失败。
            SaveFormatError: 快照损坏或展开失败。
        """
        snapshot_path = self._resolve_snapshot_path(snapshot_path, world_id)
        header, zf = self._open_snapshot(snapshot_path)
        try:
            keys = SaveKeys.from_dict(header)
            try:
                manifest = Manifest.from_dict(
                    json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                )
            except KeyError as exc:
                raise SaveFormatError("快照缺少 manifest") from exc
            except (json.JSONDecodeError, UnicodeDecodeError, SaveFormatError) as exc:
                raise SaveFormatError(f"快照 manifest 损坏: {exc}") from exc
            override = world_id is not None
            if override:
                embedded_id = manifest.world_id
                manifest.world_id = world_id
                # 密钥混淆层绑定存档身份：换 ID 后需用原 ID 解出、
                # 新 ID 重新混淆，否则新档位无法解出密钥
                if manifest.secrets_blob:
                    keys = SaveKeys.from_protected(
                        manifest.secrets_blob, embedded_id, manifest.seed,
                    )
                    manifest.secrets_blob = keys.protect(world_id, manifest.seed)
            world_id = manifest.world_id

            tmp_dir = os.path.join(
                self._root, f".extract-{uuid.uuid4().hex}"
            )
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                for info in zf.infolist():
                    # 只提取规范文件集合：防 zip 路径穿越（只取文件名），
                    # 且恶意快照携带的任意条目（evil.txt 等）不会被植入
                    if info.is_dir() or info.filename not in _SNAPSHOT_ENTRIES:
                        continue
                    target = os.path.join(tmp_dir, os.path.basename(info.filename))
                    with zf.open(info) as src, open(target, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                # world_id 被覆盖时以覆盖后的 manifest 替换 zip 内原版
                if override:
                    manifest.write(os.path.join(tmp_dir, MANIFEST_NAME))
            except Exception:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise

            # 原子替换活目录；快照子目录与大陆缓存（同 seed 确定性产物）
            # 先移出再放回——回滚绝不能删除自己的回退点，也不应丢秒开缓存
            wdir = self.world_dir(world_id)
            backup = wdir + f".old-{uuid.uuid4().hex}"
            snaps_backup: str | None = None
            cont_backup: str | None = None
            if os.path.isdir(wdir):
                snaps_dir = os.path.join(wdir, SNAPSHOT_DIR)
                if os.path.isdir(snaps_dir):
                    snaps_backup = wdir + f".snaps-{uuid.uuid4().hex}"
                    os.rename(snaps_dir, snaps_backup)
                cont_path = os.path.join(wdir, CONTINENT_FILE)
                if os.path.isfile(cont_path):
                    cont_backup = wdir + f".cont-{uuid.uuid4().hex}"
                    os.rename(cont_path, cont_backup)
                os.rename(wdir, backup)
            try:
                os.rename(tmp_dir, wdir)
            except Exception:
                # 回滚失败时恢复原目录（含快照与缓存）
                if os.path.isdir(wdir):
                    shutil.rmtree(wdir, ignore_errors=True)
                if os.path.isdir(backup):
                    os.rename(backup, wdir)
                if snaps_backup and os.path.isdir(snaps_backup):
                    os.makedirs(
                        os.path.join(wdir, SNAPSHOT_DIR), exist_ok=True,
                    )
                    os.rename(snaps_backup, os.path.join(wdir, SNAPSHOT_DIR))
                if cont_backup and os.path.isfile(cont_backup):
                    os.rename(cont_backup, os.path.join(wdir, CONTINENT_FILE))
                raise
            if snaps_backup and os.path.isdir(snaps_backup):
                os.makedirs(os.path.join(wdir, SNAPSHOT_DIR), exist_ok=True)
                os.rename(snaps_backup, os.path.join(wdir, SNAPSHOT_DIR))
            if cont_backup and os.path.isfile(cont_backup):
                os.rename(cont_backup, os.path.join(wdir, CONTINENT_FILE))
            # 血缘文件随活目录被替换进了 backup：回滚后必须保留原血缘
            # （否则分叉上下文丢失），再记录活目录新来源（本次回滚目标）
            lineage_backup = os.path.join(backup, LINEAGE_FILE)
            if os.path.isfile(lineage_backup):
                try:
                    shutil.copy2(
                        lineage_backup, os.path.join(wdir, LINEAGE_FILE),
                    )
                except OSError:
                    logger.warning("血缘文件回滚保留失败: %s", world_id)
            self.set_live_origin(world_id, os.path.basename(snapshot_path))
            shutil.rmtree(backup, ignore_errors=True)
            logger.info("快照展开为活目录: %s", world_id)
            return world_id
        finally:
            zf.close()

    # ── 内部 ──────────────────────────────────────────────

    def _existing_names(self, exclude_world_id: str | None = None) -> set[str]:
        """收集全集合存档名称（损坏 manifest 跳过；可排除指定世界）。"""
        names: set[str] = set()
        if not os.path.isdir(self._root):
            return names
        for entry in os.listdir(self._root):
            if entry == exclude_world_id:
                continue
            mp = os.path.join(self._root, entry, MANIFEST_NAME)
            if not os.path.isfile(mp):
                continue
            try:
                names.add(Manifest.read(mp).name)
            except SaveFormatError:
                logger.warning("跳过损坏存档（名称收集）: %s", entry)
        return names

    def _ensure_name_unique(
        self, name: str, *, exclude_world_id: str | None = None,
    ) -> None:
        """校验存档名称唯一（精确匹配）。

        Raises:
            ValueError: 与现有存档重名。
        """
        if name in self._existing_names(exclude_world_id=exclude_world_id):
            raise ValueError(f"存档名称已存在：{name}")

    def _unique_copy_name(self, name: str, *, exclude_world_id: str) -> str:
        """为复制档生成唯一名称：被占用时追加"副本"后缀并递增编号。

        Args:
            name: 原档名称。
            exclude_world_id: 排除的新档 ID（其 manifest 已写入原名称）。

        Returns:
            唯一名称（"X" → "X 副本" → "X 副本 2" → ...）。
        """
        existing = self._existing_names(exclude_world_id=exclude_world_id)
        if name not in existing:
            return name
        base = f"{name} 副本"
        candidate = base
        i = 2
        while candidate in existing:
            candidate = f"{base} {i}"
            i += 1
        return candidate

    def _load_keys(self, world_id: str) -> SaveKeys:
        """从 manifest.secrets_blob 解出世界的密钥对。

        Raises:
            SaveCryptoError: 密钥缺失/混淆串校验失败（存档被篡改）。
            SaveFormatError: 存档不存在。
        """
        manifest = self.get_manifest(world_id)
        if not manifest.secrets_blob:
            raise SaveCryptoError(f"存档密钥缺失: {world_id}（存档损坏）")
        return SaveKeys.from_protected(
            manifest.secrets_blob, world_id, manifest.seed,
        )

    @staticmethod
    def _open_snapshot(snapshot_path: str) -> tuple[dict, zipfile.ZipFile]:
        """解析快照文件：头部明文 JSON 行 + 解密 payload 为 ZipFile。

        Returns:
            (header dict, 打开的 ZipFile)。

        Raises:
            SaveCryptoError: 头部非法或解密失败。
        """
        with open(snapshot_path, "rb") as f:
            header_line = f.readline()
            payload = f.read()
        if not header_line or not payload:
            raise SaveCryptoError(f"快照文件为空或损坏: {snapshot_path}")
        try:
            header = json.loads(header_line.decode("utf-8"))
            if header.get("format") != "ascendsave":
                raise SaveCryptoError("快照格式标识非法")
            keys = SaveKeys.from_dict(header)
        except (json.JSONDecodeError, UnicodeDecodeError, SaveCryptoError) as exc:
            raise SaveCryptoError(f"快照头部非法: {exc}") from exc
        try:
            zip_bytes = keys.decrypt(payload)
            return header, zipfile.ZipFile(io.BytesIO(zip_bytes))
        except (SaveCryptoError, zipfile.BadZipFile) as exc:
            raise SaveCryptoError(
                f"快照解密失败（可能被篡改）: {exc}"
            ) from exc
