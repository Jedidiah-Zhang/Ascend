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
# 大陆宏观场缓存（可再生数据，随档分发保证换机后首次加载也秒开）
CONTINENT_FILE: str = "continent.bin"

# 快照打包的固定文件集合（密钥藏于 manifest.secrets_blob，无需独立文件；
# continent.bin 可再生，不进快照，保持回退点精简）
_SNAPSHOT_ENTRIES: tuple[str, ...] = (
    MANIFEST_NAME, STATE_FILE, ENTITIES_FILE, CHUNKS_DB, EVENTS_DB,
)

# 存档位活目录中的规范文件集合（导出/复制只拷这些，排除 WAL/临时文件；
# 含 continent.bin——同 seed 确定性产物，随档复制保证副本首次加载秒开）
_LIVE_ENTRIES: tuple[str, ...] = _SNAPSHOT_ENTRIES + (CONTINENT_FILE,)


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

        Args:
            name: 存档名称（存档选择页展示）。
            seed: 世界种子。
            world_id: 可选，指定 ID（测试用）；缺省自动生成。

        Returns:
            新建的 Manifest。
        """
        if not name:
            raise ValueError("存档名称不能为空")
        world_id = world_id or uuid.uuid4().hex
        wdir = self.world_dir(world_id)
        os.makedirs(wdir, exist_ok=False)
        os.makedirs(self.snapshot_dir(world_id), exist_ok=True)
        now = _real_time.time()
        manifest = Manifest(
            name=name, seed=int(seed), world_id=world_id,
            created_at=now, last_played_at=now,
        )
        # 密钥不落盘明文：加密后藏入 manifest.secrets_blob 随档分发
        manifest.secrets_blob = SaveKeys.generate().protect(world_id, int(seed))
        manifest.write(self.manifest_path(world_id))
        logger.info("创建存档位: %s (%s, seed=%d)", world_id, name, seed)
        return manifest

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
            name: 新名称。

        Returns:
            更新后的 Manifest。

        Raises:
            SaveFormatError: 世界不存在。
        """
        if not name:
            raise ValueError("存档名称不能为空")
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
        if new_manifest.secrets_blob:
            keys = SaveKeys.from_protected(
                new_manifest.secrets_blob, world_id, new_manifest.seed,
            )
            new_manifest.secrets_blob = keys.protect(new_id, new_manifest.seed)
        new_manifest.created_at = _real_time.time()
        new_manifest.write(self.manifest_path(new_id))
        logger.info("复制存档: %s → %s", world_id, new_id)
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

    # ── 快照 ──────────────────────────────────────────────

    def create_snapshot(self, world_id: str, *, suffix: str = "manual") -> str:
        """把活目录打包为加密快照单文件（手动保存/回滚保护）。

        Args:
            world_id: 世界 ID。
            suffix: 快照来源标识（manual/auto/quit），拼入文件名。

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
        logger.info("创建快照: %s → %s", world_id, filename)
        return filename

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

    def extract_snapshot(
        self, snapshot_path: str, world_id: str | None = None,
    ) -> str:
        """把快照展开为活目录（回滚）：解密 → 临时目录 → 原子替换。

        原活目录被覆盖前应已完成自动快照保护（由调用方负责）。

        Args:
            snapshot_path: 快照文件路径。
            world_id: 目标 world_id 覆盖。快照内 manifest 记录的是
                创建时所属世界；复制存档（export）后快照仍指向原世界，
                回滚到复制档时必须显式传入目标 ID，避免覆盖原世界。

        Returns:
            展开后的 world_id。

        Raises:
            SaveCryptoError: 快照解密/校验失败。
            SaveFormatError: 快照损坏或展开失败。
        """
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
            shutil.rmtree(backup, ignore_errors=True)
            logger.info("快照展开为活目录: %s", world_id)
            return world_id
        finally:
            zf.close()

    # ── 内部 ──────────────────────────────────────────────

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
