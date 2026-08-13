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

快照模型（设计意图：auto 节点 = 当前线的滚动记录，永无下游）:
  - manual/quit = 玩家保存的不可变节点（树的分叉点）；
  - auto = 当前状态记录。恒为叶子：当前记录在保存时晋升为
    manual、在离开该线时原地冻结刷新，从不新增子节点；
  - 不变式：live_origin（活目录来源）恒为当前 auto 记录
    （世界初始 "" 除外）；进入手动档会在其下游开启新当前记录。

快照作为世界目录内的兄弟子目录：回滚 = 用快照内容替换活文件，
快照子目录在交换期间先移出再放回，自身不受影响（否则回滚会
删除自己的回退点）。

快照 .ascendsave 格式（增量链模型）:
    第一行: 明文 JSON {"format": "ascendsave", "version": 2,
                        "base": <锚点文件名|null>,
                        "fernet_key": <b64>, "sign_key": <b64>}
    随后:   HMAC(sign_key, payload) || Fernet(key)(payload)

    payload = zip 打包的差异数据:
      - base = null（全量，v1 兼容）: 完整活目录文件字节
      - base = <文件>（增量）: 与锚点内容（物化后的全量）的差异——
        文件级条目（manifest/state/entities 等）只在变化时携带，
        SQLite 数据库以 "<库名>.pages" 页图携带（见 _PAGES_SUFFIX）。

    锚点规则：每个节点锚定其最近手动祖先（沿血缘 parent 上溯，
    跳过 auto/quit；到 "" 则 base=null）。物化 = 沿链合并：
    全量解包 + 逐级应用增量页覆盖。
"""

import io
import json
import os
import random
import re
import shutil
import struct
import time as _real_time
import uuid
import zipfile

from ascend.config import SAVE_ROOT
from ascend.log import get_logger

from .crypto import SaveKeys, SaveCryptoError
from .io import atomic_write
from .manifest import Manifest, SaveFormatError, MANIFEST_NAME, SEED_MAX

logger = get_logger(__name__)

STATE_FILE: str = "state.json.enc"

# world_id 规范格式：32 位小写十六进制（uuid4().hex）；也拦截
# "…32 个任意字符"伪装——字符集限定防目录穿越的最终防线
_WORLD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
ENTITIES_FILE: str = "entities.json.enc"
CHUNKS_DB: str = "chunks.db"
EVENTS_DB: str = "events.db"
SNAPSHOT_DIR: str = "snapshots"
SNAPSHOT_SUFFIX: str = ".ascendsave"
# 增量快照里 SQLite 数据库的页图条目后缀：<库名>.pages
# 页图二进制: <page_size u32 LE><file_size u64 LE><count u32 LE>
#           + {<index u32 LE><length u32 LE><bytes>}*count
_PAGES_SUFFIX: str = ".pages"
# 快照血缘（时间线分叉）元数据：{live_origin, snapshots: {file: {parent, game_time, saved_at, seq}}}
LINEAGE_FILE: str = "lineage.json"
# 保留策略：auto（当前/冻结记录）环形保留最近 N 个，quit（退出保存）保留最近 K 个；
# manual（手动）永久保留。live_origin 指向的快照永不自动淘汰
# （当前记录），因此同一来源的实际上限 = N + 1。
# 注：quit 为预留来源（退出保存尚未接入，晋升语义已就绪），
# 保留策略先行——启用时按普通非 auto 保存路径走即可。
AUTO_SNAPSHOT_KEEP: int = 20
QUIT_SNAPSHOT_KEEP: int = 3
# 大陆宏观场缓存（可再生数据，随档分发保证换机后首次加载也秒开）
CONTINENT_FILE: str = "continent.bin"

# 快照打包的固定文件集合（密钥藏于 manifest.secrets_blob，无需独立文件；
# continent.bin 可再生，不进快照，保持回退点精简；lineage 为世界级元数据，
# 不随快照打包——快照依赖世界内 lineage 提供父子上下文。
# chunks.db 语义：仅玩家改动（dirty chunk）落盘——确定性生成的 tile
# 是 seed 的纯函数、可再生，不写库（ChunkStore v2 策略），
# 因此 chunks.db 本身即动态数据，随快照链走）
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

    # ── 校验 ──────────────────────────────────────────────

    def _validate_world_id(self, world_id: str) -> str:
        """校验 world_id 格式（32 位小写十六进制），所有入口统一放行。

        拒绝路径穿越：world_id 直接拼入目录路径（world_dir），
        任何非规范格式（含 ../、绝对路径、自定义串）一律拒绝。

        Args:
            world_id: 世界 ID。

        Returns:
            校验通过的原值。

        Raises:
            ValueError: 格式非法。
        """
        if not isinstance(world_id, str) or _WORLD_ID_RE.fullmatch(world_id) is None:
            raise ValueError(f"非法 world_id: {world_id!r}")
        return world_id

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

    def create_world(
        self,
        name: str,
        seed: int,
        *,
        world_id: str | None = None,
        gen_params: dict | None = None,
    ) -> Manifest:
        """创建新的存档位（活目录 + 密钥 + 初版 manifest）。

        种子在创建时定案：seed=0（前端"随机"占位）在此随机化并写入
        manifest——存档身份（world_id+seed）出生即一致，密钥混淆层
        （secrets_blob 绑定 world_id+seed）不会与 manifest 失配。

        gen_params 为创建世界流程的调参产出（Issue #8），随档定案：
        目前含 land_ratio；非法值抛 ValueError（与 Manifest 校验一致）。

        Args:
            name: 存档名称（存档选择页展示；全集合唯一，重名拒绝）。
            seed: 世界种子；0 = 创建时随机。
            world_id: 可选，指定 ID（测试用）；缺省自动生成。
            gen_params: 可选，世界生成参数（land_ratio 等）。

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
            seed = random.randint(1, SEED_MAX)
        world_id = world_id or uuid.uuid4().hex
        self._validate_world_id(world_id)
        gen_params = Manifest._validate_gen_params(gen_params or {}) or None
        wdir = self.world_dir(world_id)
        os.makedirs(wdir, exist_ok=False)
        os.makedirs(self.snapshot_dir(world_id), exist_ok=True)
        now = _real_time.time()
        manifest = Manifest(
            name=name, seed=seed, world_id=world_id,
            created_at=now, last_played_at=now,
            gen_params=gen_params,
        )
        # 密钥不落盘明文：加密后藏入 manifest.secrets_blob 随档分发
        manifest.secrets_blob = SaveKeys.generate().protect(world_id, seed)
        manifest.write(self.manifest_path(world_id))
        logger.info("创建存档位: %s (%s, seed=%d)", world_id, name, seed)
        return manifest

    def get_manifest(self, world_id: str) -> Manifest:
        """读取世界的 manifest。

        Raises:
            SaveFormatError: 世界不存在或 manifest 损坏。
        """
        self._validate_world_id(world_id)
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
            # 跳过运行期残留/临时目录（.extract-* / .old-* / live / snapshots）
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
        self._validate_world_id(world_id)
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
        self._validate_world_id(world_id)
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
        self._validate_world_id(world_id)
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
        self._validate_world_id(world_id)
        keys = self._load_keys(world_id)
        payload = keys.encrypt(
            json.dumps(state, ensure_ascii=False).encode("utf-8")
        )
        atomic_write(self.state_path(world_id), payload)

    def read_state(self, world_id: str) -> dict:
        """解密读取 state.json.enc。

        Raises:
            SaveFormatError: 世界不存在或状态文件缺失。
            SaveCryptoError: 解密/签名校验失败（被篡改）。
        """
        self._validate_world_id(world_id)
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

    def _load_lineage(self, world_id: str) -> dict | None:
        """读取血缘文件原始数据；缺失或损坏返回 None。

        区分「世界尚无血缘」与「血缘缺失/损坏」——后者不允许做
        反向对账（否则会把全部快照文件当幽灵误删）。
        """
        path = self.lineage_path(world_id)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as exc:
            logger.warning("血缘文件损坏，按空血缘处理: %s (%s)", path, exc)
            return None
        return data if isinstance(data, dict) else None

    def snapshot_lineage(self, world_id: str) -> dict:
        """读取世界血缘：快照的父子关系与当前活目录来源。

        Returns:
            {"live_origin": str|"", "snapshots": {file: {parent, game_time,
             saved_at, seq}}}。文件缺失或损坏时返回空血缘（初始世界
             无快照 / 损坏按空处理，反向对账由 _load_lineage 另行把关）。
            seq 是唯一的权威排序键，其余时间字段仅作展示。
        """
        self._validate_world_id(world_id)
        default: dict = {"live_origin": "", "snapshots": {}}
        data = self._load_lineage(world_id)
        if data is None:
            return default
        data.setdefault("live_origin", "")
        data.setdefault("snapshots", {})
        if not isinstance(data["snapshots"], dict):
            data["snapshots"] = {}
        return data

    def _write_lineage(self, world_id: str, lineage: dict) -> bool:
        """原子写入血缘文件（世界外元数据，失败不阻断主流程）。

        Returns:
            写入是否成功（失败时调用方应跳过依赖血缘一致性的
            后续步骤——如保留策略的反向对账，防止误删新文件）。
        """
        try:
            atomic_write(
                self.lineage_path(world_id),
                json.dumps(lineage, ensure_ascii=False, indent=2),
            )
            return True
        except OSError as exc:
            logger.warning("血缘文件写入失败: %s (%s)", world_id, exc)
            return False

    def _record_snapshot_lineage(
        self, world_id: str, filename: str,
        game_time: int, saved_at: float,
    ) -> bool:
        """记录快照血缘条目并更新活目录来源。

        parent = 创建时活目录来源（最近一个快照 / "" = 世界初始）；
        创建后活目录来源更新为该快照——活状态从快照内容继续，
        连续保存自动串链：后一个快照从最近一个派生。

        seq = 世界内单调递增的权威排序键（创建顺序，不受回滚后
        游戏时间倒退影响），时间线/编号/串链排序的唯一事实来源。

        Returns:
            血缘写入是否成功（False = 条目未落盘，调用方跳过 prune）。
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
        return self._write_lineage(world_id, lineage)

    def set_live_origin(self, world_id: str, snapshot_file: str) -> None:
        """记录活目录来源：回滚后调用，标记当前时间点从该快照派生。"""
        self._validate_world_id(world_id)
        lineage = self.snapshot_lineage(world_id)
        lineage["live_origin"] = snapshot_file
        self._write_lineage(world_id, lineage)

    # ── 快照 ──────────────────────────────────────────────

    @staticmethod
    def snapshot_kind(filename: str) -> str:
        """解析快照来源标识（manual/auto/quit）。

        Args:
            filename: 快照文件名（可带路径，按 basename 解析）。

        Returns:
            来源标识（无法识别时返回文件名末段）。
        """
        return (
            os.path.basename(str(filename))
            .removesuffix(SNAPSHOT_SUFFIX)
            .rsplit("-", 1)[-1]
        )

    def _write_snapshot_file(self, path: str, wdir: str) -> None:
        """把活目录规范文件打包为加密快照单文件（v2 全量基座，base=null）。

        新建（create_snapshot）、刷新（refresh_snapshot）与晋升
        （promote）共用同一打包原语：内容永远 = 活目录当前状态。
        原子写入（临时文件 + rename）：刷新覆写既有快照时，写入
        中途崩溃不会损坏原文件（该节点是所在线的唯一记录）。

        增量写见 _write_delta_snapshot（有锚点时使用）。
        """
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
            "version": 2,
            "base": None,
            "fernet_key": key_dict["fernet_key"],
            "sign_key": key_dict["sign_key"],
        }, ensure_ascii=False).encode("utf-8") + b"\n"
        encrypted = session_key.encrypt(zip_bytes)
        tmp = f"{path}.tmp-{uuid.uuid4().hex}"
        try:
            with open(tmp, "wb") as f:
                f.write(header)
                f.write(encrypted)
            os.replace(tmp, path)
        except OSError:
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise

    # ── 增量写（v2 锚点 + diff） ─────────────────────────

    def _anchor_of(
        self, world_id: str, parent: str, lineage: dict | None = None,
    ) -> str | None:
        """解析增量锚点：沿血缘 parent 链上溯到最近手动节点。

        锚点规则：增量只引用永不淘汰的 manual 节点（auto/quit
        会被环形淘汰/晋升，不可作基座）；到 ""（世界初始）返回
        None（该节点写全量，成为新的全量基座）。

        Args:
            world_id: 世界 ID。
            parent: 候选节点的血缘 parent（live_origin 或晋升前的父）。
            lineage: 可选的已加载血缘（删除重基座时传入内存版，
                避免磁盘旧数据；None 时重新读取）。

        Returns:
            最近手动祖先文件名；无则 None。
        """
        if lineage is None:
            lineage = self.snapshot_lineage(world_id)
        snaps = lineage.get("snapshots", {})
        cur = str(parent)
        seen: set[str] = set()
        while cur and cur in snaps and cur not in seen:
            if self.snapshot_kind(cur) == "manual":
                return cur
            seen.add(cur)
            cur = str(snaps[cur].get("parent", ""))
        return None

    @staticmethod
    def _sqlite_page_size(data: bytes) -> int:
        """从 SQLite 文件头读取页尺寸（offset 16，2 字节大端；1 = 65536）。"""
        if len(data) >= 18:
            ps = struct.unpack_from(">H", data, 16)[0]
            if ps == 1:
                return 65536
            if 512 <= ps <= 65536 and ps & (ps - 1) == 0:
                return ps
        return 4096

    @staticmethod
    def _file_differ(base_path: str, new_path: str) -> bool:
        """文件级差异：大小或内容不同；基座缺失 = 新增；新侧缺失 = 继承。"""
        if not os.path.isfile(new_path):
            return False
        if not os.path.isfile(base_path):
            return True
        if os.path.getsize(base_path) != os.path.getsize(new_path):
            return True
        with open(base_path, "rb") as f1, open(new_path, "rb") as f2:
            return f1.read() != f2.read()

    def _diff_db_pages(self, base_path: str, new_path: str) -> bytes | None:
        """SQLite 页级差异：逐页字节比较，返回页图 payload（无差异 None）。"""
        if not os.path.isfile(new_path):
            return None
        with open(new_path, "rb") as f:
            new_bytes = f.read()
        if os.path.isfile(base_path):
            with open(base_path, "rb") as f:
                base_bytes = f.read()
        else:
            base_bytes = b""
        page_size = self._sqlite_page_size(new_bytes)
        changed: dict[int, bytes] = {}
        for i in range((len(new_bytes) + page_size - 1) // page_size):
            blob = new_bytes[i * page_size:(i + 1) * page_size]
            if i * page_size >= len(base_bytes) or (
                base_bytes[i * page_size:(i + 1) * page_size] != blob
            ):
                changed[i] = blob
        if not changed:
            return None
        out = struct.pack("<IQI", page_size, len(new_bytes), len(changed))
        for index, blob in changed.items():
            out += struct.pack("<II", index, len(blob)) + blob
        return out

    def _diff_snapshot(
        self, base_dir: str, wdir: str,
    ) -> tuple[list[str], dict[str, bytes]]:
        """计算活目录相对基座内容目录的差异。

        Returns:
            (变化文件列表（manifest 恒携带——extract 先读它）,
             DB 页图 {库名: payload})。
        """
        files = [MANIFEST_NAME]
        for name in _SNAPSHOT_ENTRIES:
            if name in (MANIFEST_NAME, CHUNKS_DB, EVENTS_DB):
                continue
            if self._file_differ(
                os.path.join(base_dir, name), os.path.join(wdir, name),
            ):
                files.append(name)
        pages: dict[str, bytes] = {}
        for name in (CHUNKS_DB, EVENTS_DB):
            payload = self._diff_db_pages(
                os.path.join(base_dir, name), os.path.join(wdir, name),
            )
            if payload is not None:
                pages[name] = payload
        return files, pages

    def _write_delta_snapshot(
        self, world_id: str, path: str, wdir: str,
        base_filename: str, base_content_dir: str | None = None,
    ) -> None:
        """把活目录与锚点的差异打包为 v2 增量快照（base=锚点）。

        Args:
            world_id: 世界 ID（锚点物化用）。
            path: 输出快照路径。
            wdir: 活目录（差异的新侧）。
            base_filename: 锚点快照文件名（写入 header.base）。
            base_content_dir: 锚点的物化内容目录；None 时链式物化。
                调用方已知基座内容 == 活目录时可传入 wdir 本身
                （空增量特例：fresh record 内容与锚点一致）。

        Raises:
            SaveFormatError: 锚点物化失败（链上缺失/损坏）。
        """
        tmp: str | None = None
        if base_content_dir is None:
            tmp = os.path.join(self._root, f".base-{uuid.uuid4().hex}")
            os.makedirs(tmp, exist_ok=True)
            try:
                self._materialize_snapshot(
                    world_id,
                    os.path.join(self.snapshot_dir(world_id), base_filename),
                    tmp,
                )
                base_content_dir = tmp
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
        try:
            files, pages = self._diff_snapshot(base_content_dir, wdir)
            self._write_delta_payload(path, base_filename, wdir, files, pages)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    def _write_delta_payload(
        self, path: str, base_filename: str, wdir: str,
        files: list[str], pages: dict[str, bytes],
    ) -> None:
        """把差异打包为 v2 增量快照文件（base=锚点，原子写入）。"""
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in files:
                zf.write(os.path.join(wdir, name), name)
            for db_name, payload in pages.items():
                zf.writestr(db_name + _PAGES_SUFFIX, payload)
        zip_bytes = buffer.getvalue()

        session_key = SaveKeys.generate()
        key_dict = session_key.to_dict()
        header = json.dumps({
            "format": "ascendsave",
            "version": 2,
            "base": base_filename,
            "fernet_key": key_dict["fernet_key"],
            "sign_key": key_dict["sign_key"],
        }, ensure_ascii=False).encode("utf-8") + b"\n"
        encrypted = session_key.encrypt(zip_bytes)
        tmp_path = f"{path}.tmp-{uuid.uuid4().hex}"
        try:
            with open(tmp_path, "wb") as f:
                f.write(header)
                f.write(encrypted)
            os.replace(tmp_path, path)
        except OSError:
            try:
                os.remove(tmp_path)
            except OSError:
                pass
            raise

    def _write_snapshot(
        self, world_id: str, path: str, parent: str,
        base_content_dir: str | None = None,
    ) -> None:
        """按锚点规则写快照文件：有手动锚点 → 增量；否则全量。

        base_content_dir 提供时视为锚点的物化内容（空增量特例：
        fresh record 内容与锚点一致，免链式物化）。

        锚点物化失败（链上缺失/损坏）回退全量——不阻断保存，
        新节点成为新的全量基座（自愈）。
        """
        wdir = self.world_dir(world_id)
        anchor = self._anchor_of(world_id, parent)
        if anchor is None:
            self._write_snapshot_file(path, wdir)
            return
        try:
            self._write_delta_snapshot(
                world_id, path, wdir, anchor, base_content_dir,
            )
        except (SaveFormatError, SaveCryptoError, OSError):
            logger.warning(
                "增量写失败，回退全量基座: %s (%s)", world_id, anchor,
            )
            self._write_snapshot_file(path, wdir)

    def _create_plain_snapshot(
        self, world_id: str, suffix: str, game_time: int | None,
        base_same_as_live: bool = False,
    ) -> str:
        """把活目录打包为快照单文件（新建原语，锚点规则增量写）。

        不改动既有节点的血缘关系：新节点从当前活目录来源派生并成为
        新的活目录来源（live_origin）。

        Args:
            world_id: 世界 ID。
            suffix: 快照来源标识（manual/auto/quit），拼入文件名。
            game_time: 创建时刻的世界时间（tick）；None 时从活目录
                state.json 读取（引擎路径传入更准，磁盘路径兜底）。
            base_same_as_live: 锚点内容 == 活目录（fresh record 特例，
                免链式物化直接写空增量；如刚展开/刚晋升后）。

        Returns:
            快照文件名（不含目录）。
        """
        wdir = self.world_dir(world_id)
        # 唯一段置于 suffix 前：list_snapshots 的 rsplit("-", 1)[-1]
        # 解析与按文件名排序（时间序）均不受影响；同秒多次快照不覆盖
        stamp = _real_time.strftime("%Y-%m-%d-%H%M%S")
        uniq = uuid.uuid4().hex[:6]
        filename = f"@{stamp}-{uniq}-{suffix}{SNAPSHOT_SUFFIX}"
        path = os.path.join(self.snapshot_dir(world_id), filename)
        parent = self.snapshot_lineage(world_id).get("live_origin", "")
        if base_same_as_live:
            # 空增量特例：仅当锚点 == 直接父节点（刚写入的 manual，
            # 其内容必 == 活目录）时成立；血缘写失败等异常态下
            # live_origin 仍是旧 auto 记录，锚点 ≠ 父 → 退化为
            # 真实 diff（_write_snapshot），避免写入错误内容
            anchor = self._anchor_of(world_id, parent)
            if anchor is not None and anchor == parent:
                try:
                    self._write_delta_snapshot(
                        world_id, path, wdir, anchor, base_content_dir=wdir,
                    )
                except (SaveFormatError, SaveCryptoError, OSError):
                    logger.warning(
                        "空增量写失败，回退全量: %s (%s)", world_id, anchor,
                    )
                    self._write_snapshot_file(path, wdir)
            else:
                self._write_snapshot(world_id, path, parent)
        else:
            self._write_snapshot(world_id, path, parent)

        # 血缘记录（时间线分叉数据源）：game_time 缺省时读活目录状态
        if game_time is None:
            game_time = self._state_game_time(world_id)
        lineage_ok = self._record_snapshot_lineage(
            world_id, filename, game_time, _real_time.time(),
        )
        logger.info("创建快照: %s → %s", world_id, filename)
        if not lineage_ok:
            # 血缘未落盘：跳过保留策略（反向对账会误删本文件）
            return filename
        # 保留策略：每次创建后淘汰超量快照（失败不阻断快照本身）
        try:
            self.prune_snapshots(world_id)
        except OSError as exc:
            logger.warning("快照保留策略执行失败: %s (%s)", world_id, exc)
        return filename

    def create_snapshot(
        self, world_id: str, *, suffix: str = "manual",
        game_time: int | None = None,
    ) -> str:
        """保存语义：把当前 auto 记录晋升为保存节点并开启新当前记录。

        auto 节点 = 当前线的滚动记录（不变式：live_origin 恒为 auto）。
        非 auto 保存（manual/quit）时当前记录原地晋升为保存来源
        （内容刷新为当前活状态，parent/seq 保留，原 auto 文件删除），
        随后在该保存节点下游开启新的 auto 当前记录；suffix="auto"
        时为原语路径（创建/冻结当前记录本身）。

        Args:
            world_id: 世界 ID。
            suffix: 快照来源标识（manual/auto/quit），拼入文件名。
            game_time: 创建时刻的世界时间（tick）；None 时从活目录
                state.json 读取（引擎路径传入更准，磁盘路径兜底）。

        Returns:
            快照文件名（不含目录；非 auto 保存返回晋升后的保存节点）。

        Raises:
            SaveFormatError: 世界不存在。
        """
        self.get_manifest(world_id)  # 校验世界存在
        if suffix == "auto":
            return self._create_plain_snapshot(world_id, suffix, game_time)
        # 当前记录 → 保存节点（内容 = 当前活状态，位置不变）；
        # live_origin 非 auto（世界初始/异常态）时按普通新建处理
        filename = self._promote_auto_snapshot(world_id, suffix, game_time)
        if filename is None:
            filename = self._create_plain_snapshot(world_id, suffix, game_time)
        # 开启新当前记录（保存节点下游）：auto 节点是当前线的滚动记录。
        # 失败（如磁盘满）不阻断本次保存——live_origin 落在保存节点，
        # 下次保存/进入自愈恢复不变式。
        # 空增量特例：锚点（刚晋升的保存节点）内容 == 活目录
        try:
            self._create_plain_snapshot(
                world_id, "auto", game_time, base_same_as_live=True,
            )
        except OSError as exc:
            logger.warning(
                "新当前记录创建失败（保存本身成功）: %s (%s)", world_id, exc,
            )
        return filename

    def _promote_auto_snapshot(
        self, world_id: str, suffix: str, game_time: int | None,
    ) -> str | None:
        """把 live_origin（当前 auto 记录）原地晋升为保存来源。

        语义：auto 节点是当前线的滚动记录；保存 = 该节点变为保存
        来源（内容刷新为当前活状态，parent/seq 保留），原 auto
        文件删除——auto 节点永无下游、时间线位置不变。

        Returns:
            晋升后的新文件名；live_origin 非 auto 或文件异常时 None
            （调用方按普通新建处理）。
        """
        lineage = self.snapshot_lineage(world_id)
        origin = lineage.get("live_origin", "")
        if not origin or self.snapshot_kind(origin) != "auto":
            return None
        entries = lineage.get("snapshots", {})
        if origin not in entries:
            logger.warning("auto 节点无血缘条目，退回普通新建: %s/%s", world_id, origin)
            return None
        old_path = os.path.join(self.snapshot_dir(world_id), origin)
        if not os.path.isfile(old_path):
            logger.warning("auto 节点文件缺失，退回普通新建: %s/%s", world_id, origin)
            return None

        stamp = _real_time.strftime("%Y-%m-%d-%H%M%S")
        uniq = uuid.uuid4().hex[:6]
        filename = f"@{stamp}-{uniq}-{suffix}{SNAPSHOT_SUFFIX}"
        path = os.path.join(self.snapshot_dir(world_id), filename)
        # 锚点规则：晋升节点锚定原记录的手动祖先（增量写/回退全量）
        self._write_snapshot(
            world_id, path, str(entries[origin].get("parent", "")),
        )

        if game_time is None:
            game_time = self._state_game_time(world_id)
        entry = dict(entries[origin])
        entry["game_time"] = int(game_time)
        entry["saved_at"] = _real_time.time()
        entries[filename] = entry
        entries.pop(origin, None)
        lineage["live_origin"] = filename
        if not self._write_lineage(world_id, lineage):
            # 血缘未落盘：保留旧 auto 记录（条目仍指向它，状态一致），
            # 跳过删除与保留策略——新文件留待下次对账清理
            return filename
        try:
            os.remove(old_path)
        except OSError as exc:
            logger.warning("晋升后旧 auto 文件删除失败: %s (%s)", origin, exc)
        logger.info("晋升快照: %s %s → %s", world_id, origin, filename)
        try:
            self.prune_snapshots(world_id)
        except OSError as exc:
            logger.warning("快照保留策略执行失败: %s (%s)", world_id, exc)
        return filename

    def _state_game_time(self, world_id: str) -> int:
        """从活目录 state 读取世界时间（game_time 兜底；失败归零）。"""
        try:
            return int(
                self.read_state(world_id).get("clock", {}).get("time", 0)
            )
        except (SaveFormatError, SaveCryptoError):
            return 0

    def refresh_snapshot(
        self, world_id: str, filename: str, *, game_time: int | None = None,
    ) -> None:
        """把活目录当前状态重打包进既有快照（auto 节点原地冻结）。

        仅允许 auto 记录（manual/quit 是不可变保存节点，禁止改写）；
        血缘位置不变（parent/seq 保留），内容与 game_time/saved_at
        刷新为离开时刻的活状态——离开当前线时用它原地记录进度，
        不新建节点、不产生下游（时间线分叉只发生在手动节点处）。

        Args:
            world_id: 世界 ID。
            filename: 既有快照文件名（auto 记录）。
            game_time: 世界时间（tick）；None 时读活目录 state 兜底。

        Raises:
            SaveFormatError: 快照不在血缘中、非 auto 或文件缺失。
        """
        self._validate_world_id(world_id)
        lineage = self.snapshot_lineage(world_id)
        filename = os.path.basename(str(filename))
        if filename not in lineage.get("snapshots", {}):
            raise SaveFormatError(f"快照不在血缘中: {filename}")
        if self.snapshot_kind(filename) != "auto":
            raise SaveFormatError(f"仅 auto 记录可刷新冻结: {filename}")
        path = os.path.join(self.snapshot_dir(world_id), filename)
        if not os.path.isfile(path):
            raise SaveFormatError(f"快照文件缺失: {filename}")
        # 原地重写：锚点规则增量写（锚定节点的手动祖先，位置不变）
        self._write_snapshot(
            world_id, path, str(lineage["snapshots"][filename].get("parent", "")),
        )
        if game_time is None:
            game_time = self._state_game_time(world_id)
        entry = lineage["snapshots"][filename]
        entry["game_time"] = int(game_time)
        entry["saved_at"] = _real_time.time()
        self._write_lineage(world_id, lineage)
        logger.info("刷新快照: %s %s", world_id, filename)

    def enter_snapshot(
        self, snapshot_path: str, world_id: str | None = None,
    ) -> str:
        """进入快照（回滚/继续）—— 完整的进入语义。

        流程：冻结离开的当前记录（内容 = 活目录当前状态）→ 展开
        目标 → 手动/退出目标在其下游开启新 auto 当前记录（分叉点）。
        auto 目标 = 继续：目标本身成为当前记录，不新建任何节点。

        冻结是语义优化而非回滚硬前置：离开记录文件缺失等异常态
        下降级为 warning 继续展开（回滚是用户恢复手段，不得中断）；
        展开后的新当前记录创建同理降级（活目录已替换成功，
        live_origin 落手动节点，下次保存/进入自愈）。

        Args:
            snapshot_path: 快照文件路径（绝对/相对/文件名）。
            world_id: 目标 world_id 覆盖（回滚必须显式传入，见
                extract_snapshot）。None 时无法冻结离开记录（跳过分）。

        Returns:
            展开后的 world_id。

        Raises:
            SaveCryptoError: 快照解密/校验失败。
            SaveFormatError: 快照损坏或展开失败。
        """
        if world_id is not None:
            self._validate_world_id(world_id)
            lineage = self.snapshot_lineage(world_id)
            origin = lineage.get("live_origin", "")
            if origin and self.snapshot_kind(origin) == "auto":
                # 冻结离开的当前记录（auto 是滚动记录：原地刷新，不新建）；
                # 异常态（文件缺失等）降级继续，不阻断回滚
                try:
                    self.refresh_snapshot(world_id, origin)
                except (SaveFormatError, OSError):
                    logger.warning(
                        "冻结离开记录失败，继续展开: %s/%s", world_id, origin,
                    )
        world_id = self.extract_snapshot(snapshot_path, world_id=world_id)
        if self.snapshot_kind(snapshot_path) != "auto":
            # 手动/退出目标：开启新当前记录（分叉发生在手动节点处）；
            # 失败（如磁盘满）不阻断回滚——活目录已展开成功，
            # 下次保存/进入自愈恢复不变式。
            # 空增量特例：锚点（刚展开的手动目标）内容 == 活目录
            try:
                self._create_plain_snapshot(
                    world_id, "auto", None, base_same_as_live=True,
                )
            except OSError as exc:
                logger.warning(
                    "开启新当前记录失败（回滚已成功）: %s (%s)", world_id, exc,
                )
        return world_id

    def remove_snapshots(self, world_id: str, files: list[str]) -> list[str]:
        """从血缘森林移除节点集合（快照删除的唯一原语）。

        三种删除（单点 / 分支裁剪 / 保留策略）共用本方法：删除集由
        调用方计算，本方法负责统一的结构变换：

          1. 血缘重接：存活节点的 parent 在删除集时，沿 parent 链
             上溯挂到**最近存活祖先**——结构性保证「parent ∈ 存活集
             or ''」。区别于单级提升：批量删除中间层（如分支裁剪）
             后不会产生指向已删节点的悬空引用。
          2. live_origin 在删除集：同样上溯回退到最近存活祖先
             （"" = 世界初始）。
          3. seq 保留空洞：seq 是创建序号（出生序），删除不重编号；
             排序与前端编号（位置序号 i+1）对空洞免疫。
          4. 文件删除容忍缺失：血缘条目在而文件不在的孤儿清理并入
             原语（文件跳过，条目照删）。
          5. 增量链重基座：被删手动锚点的存活后代改锚定最近存活
             手动祖先（内容不变、只改基座引用；见 _rebase_after_removal）。

        Args:
            world_id: 世界 ID。
            files: 待删快照文件名集合（不在血缘中的条目无操作）。

        Returns:
            实际删除的血缘条目文件名（排序，空列表 = 无操作）。
        """
        self._validate_world_id(world_id)
        lineage = self.snapshot_lineage(world_id)
        snaps = lineage.get("snapshots", {})
        removed = {os.path.basename(str(f)) for f in files} & set(snaps)
        if not removed:
            return []
        surviving = set(snaps) - removed

        def nearest_survivor(name: str) -> str:
            """沿 parent 链上溯到最近存活祖先（防环；悬空父链视为初始）。"""
            seen: set[str] = set()
            cur = str(snaps[name].get("parent", ""))
            while cur in snaps and cur not in surviving:
                if cur in seen:
                    return ""
                seen.add(cur)
                cur = str(snaps[cur].get("parent", ""))
            return cur if cur in surviving else ""

        for name, entry in snaps.items():
            if name not in removed and entry.get("parent") in removed:
                entry["parent"] = nearest_survivor(name)
        if lineage.get("live_origin", "") in removed:
            lineage["live_origin"] = nearest_survivor(lineage["live_origin"])

        # 增量链重基座：被删手动锚点的存活后代改锚定最近存活手动
        # 祖先（须在文件删除前执行——旧链仍可物化）
        self._rebase_after_removal(world_id, lineage, removed)

        sdir = self.snapshot_dir(world_id)
        for name in removed:
            path = os.path.join(sdir, name)
            if os.path.isfile(path):
                os.remove(path)
            snaps.pop(name, None)
        if not self._write_lineage(world_id, lineage):
            # 血缘未落盘：磁盘血缘仍指向已删文件，后代暂不可物化，
            # 靠下次 prune 孤儿清理自愈——记录 warning 供排查
            logger.warning(
                "删除后血缘写入失败（下次清理自愈）: %s", world_id,
            )
        deleted = sorted(removed)
        logger.info("删除快照 %d 个: %s", len(deleted), world_id)
        return deleted

    def _rebase_after_removal(
        self, world_id: str, lineage: dict, removed: set[str],
    ) -> None:
        """删除手动锚点后重基座：受影响后代改锚定最近存活手动祖先。

        受影响后代 = 文件 header.base ∈ 删除集 的存活节点（文件
        的实际基座才是权威，血缘重接后的父链仅用于计算新锚点）。
        重写只改基座引用与格式、内容不变：以被删锚点的物化内容 +
        自身增量还原内容，再对新的最近手动祖先（或无 → 全量）
        重新 diff 写回原文件名（树位置/seq 不变）。

        须在删除文件之前调用（旧链仍可物化）。物化失败（链上损坏）
        的后代跳过——本就不可恢复，不拖累删除（best-effort）。
        """
        removed_manuals = {
            f for f in removed if self.snapshot_kind(f) == "manual"
        }
        if not removed_manuals:
            return
        snaps = lineage.get("snapshots", {})
        affected: dict[str, list[str]] = {}
        for name in snaps:
            if name in removed:
                continue
            path = os.path.join(self.snapshot_dir(world_id), name)
            if not os.path.isfile(path):
                continue
            try:
                header, zf = self._open_snapshot(path)
                zf.close()
            except SaveCryptoError:
                continue
            if header.get("base") in removed_manuals:
                affected.setdefault(header.get("base"), []).append(name)
        if not affected:
            return
        logger.info(
            "重基座 %d 个后代: %s",
            sum(len(nodes) for nodes in affected.values()), world_id,
        )
        for old_anchor, nodes in affected.items():
                anchor_tmp = os.path.join(
                    self._root, f".rebase-{uuid.uuid4().hex}",
                )
                os.makedirs(anchor_tmp, exist_ok=True)
                try:
                    self._materialize_snapshot(
                        world_id,
                        os.path.join(self.snapshot_dir(world_id), old_anchor),
                        anchor_tmp,
                    )
                except (SaveFormatError, SaveCryptoError, OSError):
                    logger.warning(
                        "重基座失败（锚点不可物化，跳过）: %s", old_anchor,
                    )
                    shutil.rmtree(anchor_tmp, ignore_errors=True)
                    continue
                for name in nodes:
                    try:
                        self._rebase_one(world_id, lineage, anchor_tmp, name)
                    except (SaveFormatError, SaveCryptoError, OSError) as exc:
                        logger.warning(
                            "重基座失败（跳过）: %s (%s)", name, exc,
                        )
                shutil.rmtree(anchor_tmp, ignore_errors=True)

    def _rebase_one(
        self, world_id: str, lineage: dict, anchor_tmp: str, name: str,
    ) -> None:
        """单个后代重基座：还原内容 → 对新锚点重写增量（或无 → 全量）。"""
        snaps = lineage.get("snapshots", {})
        path = os.path.join(self.snapshot_dir(world_id), name)
        content_tmp = os.path.join(self._root, f".rebase-{uuid.uuid4().hex}")
        os.makedirs(content_tmp, exist_ok=True)
        try:
            header, zf = self._open_snapshot(path)
            try:
                if header.get("base"):
                    shutil.copytree(anchor_tmp, content_tmp, dirs_exist_ok=True)
                    self._apply_delta(content_tmp, zf)
                else:
                    self._unpack_full(content_tmp, zf)
            finally:
                zf.close()
            new_anchor = self._anchor_of(
                world_id, snaps[name].get("parent", ""), lineage,
            )
            if new_anchor is None:
                # 无存活手动祖先：成为新的全量基座
                self._write_snapshot_file(path, content_tmp)
                return
            base_tmp = os.path.join(self._root, f".rebase-{uuid.uuid4().hex}")
            os.makedirs(base_tmp, exist_ok=True)
            try:
                self._materialize_snapshot(
                    world_id,
                    os.path.join(self.snapshot_dir(world_id), new_anchor),
                    base_tmp,
                )
                files, pages = self._diff_snapshot(base_tmp, content_tmp)
                self._write_delta_payload(
                    path, new_anchor, content_tmp, files, pages,
                )
            finally:
                shutil.rmtree(base_tmp, ignore_errors=True)
        finally:
            shutil.rmtree(content_tmp, ignore_errors=True)

    def remove_snapshot_branch(self, world_id: str, filename: str) -> list[str]:
        """裁剪分支：删除节点及其全部后代（子树），无关分支不受影响。

        子树定义 = 节点 + 后代（血缘森林中沿 parent 链可到达该节点的
        全体节点）；兄弟分支（parent 相同但非该节点后代）不在删除集内。
        删除集算完统一走 remove_snapshots 原语，与保留策略（
        prune_snapshots）无耦合。

        Args:
            world_id: 世界 ID。
            filename: 裁剪起点快照文件名。

        Returns:
            删除的血缘条目文件名（起点不在血缘中返回空列表）。
        """
        self._validate_world_id(world_id)
        lineage = self.snapshot_lineage(world_id)
        snaps = lineage.get("snapshots", {})
        filename = os.path.basename(str(filename))
        if filename not in snaps:
            return []
        children: dict[str, list[str]] = {}
        for name, entry in snaps.items():
            parent = str(entry.get("parent", ""))
            if parent in snaps:
                children.setdefault(parent, []).append(name)
        removed: list[str] = []
        visited: set[str] = set()
        stack = [filename]
        while stack:
            cur = stack.pop()
            if cur in visited:
                continue
            visited.add(cur)
            removed.append(cur)
            stack.extend(children.get(cur, []))
        return self.remove_snapshots(world_id, removed)

    def delete_snapshot(self, world_id: str, filename: str) -> None:
        """删除单个快照并重接血缘父链（子树提升）— 单点便捷入口。

        实现 = remove_snapshots(world_id, [filename])：删除集为单点，
        血缘重接、live_origin 回退与文件容忍语义由原语统一保证。

        Raises:
            OSError: 快照文件删除失败（血缘已先行自洽重接）。
        """
        self.remove_snapshots(world_id, [filename])

    def prune_snapshots(
        self, world_id: str, *,
        keep_auto: int = AUTO_SNAPSHOT_KEEP,
        keep_quit: int = QUIT_SNAPSHOT_KEEP,
    ) -> int:
        """按保留策略淘汰超量快照（手动快照永久保留）。

        规则：
          - auto（当前/冻结记录）环形保留最近 keep_auto 个；
          - quit（退出保存）保留最近 keep_quit 个；
          - live_origin 指向的快照永不淘汰（当前记录，淘汰会让
            时间线的「当前点」悬空）；
          - 血缘条目存在但文件已缺失的孤儿条目一并清理（重接父链）；
          - 磁盘上无血缘条目的残留快照文件（如晋升时旧文件删除失败
            的幽灵节点）一并删除（反向对账）——仅在血缘文件完好时
            执行：血缘缺失/损坏时无法判断何为幽灵，宁缺勿删。
        淘汰列表算齐后统一经 remove_snapshots 原语删除（血缘重接、
        live_origin 回退、文件容忍由原语结构性保证）。

        Returns:
            淘汰数量。
        """
        self._validate_world_id(world_id)
        sdir = self.snapshot_dir(world_id)
        lineage = self.snapshot_lineage(world_id)
        # 血缘完好性：文件存在且可解析（区分「世界尚无血缘」与
        # 「血缘缺失/损坏」——后者不做反向对账，防全量误删）
        lineage_ok = self._load_lineage(world_id) is not None
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

        # 1b. 反向对账：磁盘上无血缘条目的残留快照文件（幽灵节点）
        #     直接删除（不产生血缘变更；live_origin 文件永不误删）。
        #     血缘缺失/损坏时跳过——known 为空会把全部文件当幽灵
        if lineage_ok:
            known = set(lineage.get("snapshots", {}))
            for name in on_disk:
                if SNAPSHOT_SUFFIX + ".tmp-" in name:
                    # 快照原子写入中途崩溃残留的临时文件（永不有效）
                    try:
                        os.remove(os.path.join(sdir, name))
                    except OSError:
                        pass
                    continue
                if not name.endswith(SNAPSHOT_SUFFIX):
                    continue
                if name not in known and name != live_origin:
                    try:
                        os.remove(os.path.join(sdir, name))
                        logger.warning(
                            "清理无血缘快照文件: %s/%s", world_id, name,
                        )
                    except OSError as exc:
                        logger.warning(
                            "幽灵快照文件删除失败: %s (%s)", name, exc,
                        )

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

        if to_delete:
            self.remove_snapshots(world_id, to_delete)
            logger.info("快照保留策略淘汰 %d 个: %s", len(to_delete), world_id)
        return len(to_delete)

    def list_snapshots(self, world_id: str) -> list[dict]:
        """列出世界的快照（按文件名升序 = 时间序）。

        Returns:
            [{file, saved_at, suffix, size}]。
        """
        self._validate_world_id(world_id)
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
        """读取快照内的世界状态（回滚前展示/确认用）。

        增量快照先沿其所属世界血缘链式物化再读取（基座文件须在
        该世界快照目录内；脱离存档孤立拷贝的增量文件无法预览）。

        Args:
            snapshot_path: 快照文件路径（绝对路径或相对当前目录）。

        Returns:
            state 字典。

        Raises:
            SaveCryptoError: 快照解密/校验失败。
            SaveFormatError: 快照内数据损坏或链上基座缺失。
        """
        header, zf = self._open_snapshot(snapshot_path)
        is_delta = bool(header.get("base"))
        try:
            # state 由世界密钥加密，密钥藏于快照内 manifest.secrets_blob
            try:
                manifest = Manifest.from_dict(
                    json.loads(zf.read(MANIFEST_NAME).decode("utf-8"))
                )
            except KeyError as exc:
                raise SaveFormatError("快照缺少 manifest") from exc
            except (json.JSONDecodeError, UnicodeDecodeError, SaveFormatError) as exc:
                raise SaveFormatError(f"快照 manifest 损坏: {exc}") from exc
            world_keys = SaveKeys.from_protected(
                manifest.secrets_blob, manifest.world_id, manifest.seed,
            )
            if not is_delta:
                try:
                    state_blob = zf.read(STATE_FILE)
                except KeyError as exc:
                    raise SaveFormatError(
                        f"快照缺少状态文件: {snapshot_path}"
                    ) from exc
                data = world_keys.decrypt(state_blob)
                return json.loads(data.decode("utf-8"))
            # 增量：物化后从目录读取（先释放本文件句柄）
            zf.close()
            tmp_dir = os.path.join(self._root, f".preview-{uuid.uuid4().hex}")
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                self._materialize_snapshot(
                    manifest.world_id, snapshot_path, tmp_dir,
                )
                state_path = os.path.join(tmp_dir, STATE_FILE)
                if not os.path.isfile(state_path):
                    raise SaveFormatError(f"快照缺少状态文件: {snapshot_path}")
                with open(state_path, "rb") as f:
                    data = world_keys.decrypt(f.read())
                return json.loads(data.decode("utf-8"))
            finally:
                shutil.rmtree(tmp_dir, ignore_errors=True)
        finally:
            zf.close()

    def _resolve_snapshot_path(self, snapshot_path: str, world_id: str | None) -> str:
        """快照路径解析：绝对/带目录路径原样使用；裸文件名从目标世界的
        快照目录解析（前端以裸文件名经 --snapshot 下发）。

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
        """把快照展开为活目录（回滚）：解密 → 物化（增量链合并）→ 原子替换。

        原活目录被覆盖前应已完成离开记录的冻结（由 enter_snapshot 负责）。
        增量快照沿目标世界血缘链物化（基座文件在快照目录内）。

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
            SaveFormatError: 快照损坏、链上基座缺失或展开失败。
        """
        if world_id is not None:
            self._validate_world_id(world_id)
        snapshot_path = self._resolve_snapshot_path(snapshot_path, world_id)
        header, zf = self._open_snapshot(snapshot_path)
        is_delta = bool(header.get("base"))
        try:
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
                if embedded_id != world_id:
                    # 跨世界快照展开（复制档回滚属正常用法）：展开后
                    # live_origin 指向目标世界内可能不存在的文件名，
                    # 血缘会短暂悬空（前端串链兜底、删除时归一），
                    # 记录 warning 供排查
                    logger.warning(
                        "快照内嵌世界 %s 与目标 %s 不一致（复制档回滚？）",
                        embedded_id, world_id,
                    )
                manifest.world_id = world_id
                # 密钥混淆层绑定存档身份：换 ID 后需用原 ID 解出、
                # 新 ID 重新混淆，否则新档位无法解出密钥
                if manifest.secrets_blob:
                    world_keys = SaveKeys.from_protected(
                        manifest.secrets_blob, embedded_id, manifest.seed,
                    )
                    manifest.secrets_blob = world_keys.protect(
                        world_id, manifest.seed,
                    )
            world_id = manifest.world_id

            tmp_dir = os.path.join(
                self._root, f".extract-{uuid.uuid4().hex}"
            )
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                if is_delta:
                    # 增量：物化需打开链上其它快照，先释放本文件句柄
                    zf.close()
                    self._materialize_snapshot(
                        world_id, snapshot_path, tmp_dir,
                    )
                else:
                    try:
                        self._unpack_full(tmp_dir, zf)
                    finally:
                        zf.close()
                # world_id 被覆盖时以覆盖后的 manifest 替换 zip 内原版
                if override:
                    manifest.write(os.path.join(tmp_dir, MANIFEST_NAME))
            except Exception:
                shutil.rmtree(tmp_dir, ignore_errors=True)
                raise

            # 原子替换活目录：整体 swap（2 次 rename）——回滚绝不能删除
            # 自己的回退点（快照子目录），也不应丢秒开缓存（大陆场）；
            # 二者从备份目录拷回，即使中途失败原目录也完整保留在 backup
            wdir = self.world_dir(world_id)
            backup = wdir + f".old-{uuid.uuid4().hex}"
            if os.path.isdir(wdir):
                os.rename(wdir, backup)
            try:
                os.rename(tmp_dir, wdir)
            except OSError:
                # 回滚失败时恢复原目录（整体移回，快照与缓存一并还原）
                if os.path.isdir(wdir):
                    shutil.rmtree(wdir, ignore_errors=True)
                if os.path.isdir(backup):
                    os.rename(backup, wdir)
                raise
            if os.path.isdir(backup):
                try:
                    # 快照子目录（回退点自身）与大陆缓存从旧目录拷回
                    old_snaps = os.path.join(backup, SNAPSHOT_DIR)
                    if os.path.isdir(old_snaps):
                        shutil.copytree(old_snaps, os.path.join(wdir, SNAPSHOT_DIR))
                    old_cont = os.path.join(backup, CONTINENT_FILE)
                    if os.path.isfile(old_cont):
                        shutil.copy2(old_cont, os.path.join(wdir, CONTINENT_FILE))
                    # 血缘文件随活目录被替换进了 backup：回滚后必须保留
                    # 原血缘（否则分叉上下文丢失），再记录活目录新来源
                    old_lineage = os.path.join(backup, LINEAGE_FILE)
                    if os.path.isfile(old_lineage):
                        shutil.copy2(old_lineage, os.path.join(wdir, LINEAGE_FILE))
                except OSError:
                    logger.warning("快照/缓存回拷失败（已展开，仅保底丢失）: %s", world_id)
                shutil.rmtree(backup, ignore_errors=True)
            self.set_live_origin(world_id, os.path.basename(snapshot_path))
            logger.info("快照展开为活目录: %s", world_id)
            return world_id
        finally:
            # 双分支已各自关闭 zf（增量分支先关以释放链上文件句柄）；
            # 此处仅兜底未走分支的异常路径
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

    # ── 增量物化（v2 链式合并） ─────────────────────────

    def _unpack_full(self, tmp_dir: str, zf: zipfile.ZipFile) -> None:
        """把全量快照解包进目录（重建内容，防路径穿越白名单）。"""
        for name in os.listdir(tmp_dir):
            p = os.path.join(tmp_dir, name)
            if os.path.isfile(p):
                os.remove(p)
        for info in zf.infolist():
            if info.is_dir() or info.filename not in _SNAPSHOT_ENTRIES:
                continue
            target = os.path.join(tmp_dir, os.path.basename(info.filename))
            with zf.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    def _apply_pages(self, db_path: str, payload: bytes) -> None:
        """把页图 payload 应用到 SQLite 数据库文件（页级覆写/追加）。

        页图格式见 _PAGES_SUFFIX 注释。基座文件缺失 = DB 从无到有
        的增量（写侧产出全页覆盖，页 0 含 SQLite 头）——直接创建；
        存在时校验页尺寸与库头一致，防按错误步长覆写。
        """
        if len(payload) < 16:
            raise SaveFormatError("页图数据截断")
        try:
            page_size, file_size, count = struct.unpack_from("<IQI", payload, 0)
        except struct.error as exc:
            raise SaveFormatError(f"页图数据截断: {exc}") from exc
        if page_size <= 0:
            raise SaveFormatError(f"页图页尺寸非法: {page_size}")
        existed = os.path.isfile(db_path)
        off = 16
        max_end = 0
        try:
            with open(db_path, "w+b" if not existed else "r+b") as f:
                if existed:
                    # 校验页尺寸与既有库头一致（offset 16，2 字节大端；1 = 65536）
                    f.seek(16)
                    head = f.read(2)
                    real = struct.unpack(">H", head)[0] if len(head) == 2 else 0
                    real = 65536 if real == 1 else real
                    if real != page_size:
                        raise SaveFormatError(
                            f"页图页尺寸 {page_size} 与库头 {real} 不一致"
                        )
                for _ in range(count):
                    index, length = struct.unpack_from("<II", payload, off)
                    off += 8
                    if off + length > len(payload):
                        raise SaveFormatError("页图数据截断")
                    f.seek(index * page_size)
                    f.write(payload[off:off + length])
                    max_end = max(max_end, index * page_size + length)
                    off += length
                if max_end > file_size:
                    raise SaveFormatError(
                        f"页图越界: 最大页尾 {max_end} > 声明的文件尺寸 {file_size}"
                    )
                f.truncate(file_size)  # 扩展零填充 / 收缩（防御）
        except struct.error as exc:
            raise SaveFormatError(f"页图数据截断: {exc}") from exc

    def _apply_delta(self, tmp_dir: str, zf: zipfile.ZipFile) -> None:
        """把增量快照合并进已物化的基座目录。

        文件级条目（_SNAPSHOT_ENTRIES）直接替换；"<库名>.pages"
        页图条目按页覆写基座文件（基座缺失 = 从无到有，页图自
        含全文件，直接创建）；未知条目跳过（宽容，同全量解包）。
        """
        for info in zf.infolist():
            if info.is_dir():
                continue
            name = info.filename
            if name in _SNAPSHOT_ENTRIES:
                target = os.path.join(tmp_dir, os.path.basename(name))
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
            elif name.endswith(_PAGES_SUFFIX):
                base_name = name[: -len(_PAGES_SUFFIX)]
                target = os.path.join(tmp_dir, os.path.basename(base_name))
                self._apply_pages(target, zf.read(info))
            # 其余未知条目：跳过

    def _materialize_snapshot(
        self, world_id: str, snapshot_path: str, target_dir: str,
    ) -> None:
        """链式物化：把（可能为增量的）快照合并为全量内容目录。

        沿目标世界的血缘 parent 链回溯到根（全量基座），逐节点
        应用——全量节点解包重建，增量节点页级/文件级合并进
        target_dir。链上任意节点损坏/缺失即失败（后代不可恢复）。

        Args:
            world_id: 快照所属世界（基座文件在其 snapshots/ 目录）。
            snapshot_path: 目标快照路径（已解析）。
            target_dir: 输出目录（须已存在；内容被重建/合并）。
        """
        lineage = self.snapshot_lineage(world_id)
        snaps = lineage.get("snapshots", {})
        base = os.path.basename(snapshot_path)
        chain: list[str] = []
        seen: set[str] = set()
        cur = base
        while cur in snaps and cur not in seen:
            chain.append(cur)
            seen.add(cur)
            cur = str(snaps[cur].get("parent", ""))
        if cur not in ("", base):
            # parent 链悬空（血缘缺失/异常）：单节点物化，
            # 由增量节点的基座缺失报错兜底
            logger.warning("血缘链悬空，单节点物化: %s/%s", world_id, base)
        chain.reverse()
        if not chain or chain[-1] != base:
            # 目标不在血缘（孤立/跨世界拷贝）：从目标自身出发，
            # 只应用可解析的父链（父为全量则结果正确，父为增量
            # 则缺基座报错兜底）
            chain = [base]
        for node in chain:
            path = os.path.join(self.snapshot_dir(world_id), node)
            if not os.path.isfile(path):
                raise SaveFormatError(f"链上快照缺失: {node}")
            header, zf = self._open_snapshot(path)
            try:
                if header.get("base"):
                    if not os.listdir(target_dir):
                        raise SaveFormatError(f"增量快照缺少基座: {node}")
                    self._apply_delta(target_dir, zf)
                else:
                    self._unpack_full(target_dir, zf)
            finally:
                zf.close()
