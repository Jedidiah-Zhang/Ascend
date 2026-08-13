"""快照文件层 — .ascendsave 的打包、增量 diff、物化与重基座。

纯磁盘层：不依赖存档管理器/运行时子系统，只依赖血缘存储
（LineageStore，链式物化与锚点解析需要父子上下文）。
从 ascend/save/manager.py 拆出，职责单一化（原 SaveManager 的
_write_snapshot_file / _write_delta_* / _diff_* / _open_snapshot /
_unpack_full / _apply_* / _materialize_snapshot / _rebase_* 等）。

快照 .ascendsave 格式（增量链模型）:
    第一行: 明文 JSON {"format": "ascendsave", "version": 2,
                        "base": <锚点文件名|null>,
                        "fernet_key": <b64>, "sign_key": <b64>}
    随后:   HMAC(sign_key, payload) || Fernet(key)(payload)

    payload = zip 打包的差异数据:
      - base = null（全量，v1 兼容）: 完整活目录文件字节
      - base = <文件>（增量）: 与锚点内容（物化后的全量）的差异——
        文件级条目只在变化时携带，SQLite 数据库以 "<库名>.pages"
        页图携带（见 _PAGES_SUFFIX）。

    锚点规则：每个节点锚定其最近手动祖先（沿血缘 parent 上溯，
    跳过 auto/quit；到 "" 则 base=null）。物化 = 沿链合并：
    全量解包 + 逐级应用增量页覆盖。
"""

import io
import json
import os
import shutil
import struct
import uuid
import zipfile
from typing import TYPE_CHECKING

from ascend.log import get_logger

from .crypto import SaveKeys, SaveCryptoError
from .manifest import Manifest, SaveFormatError, MANIFEST_NAME

if TYPE_CHECKING:
    from .lineage import LineageStore

logger = get_logger(__name__)

STATE_FILE: str = "state.json.enc"
ENTITIES_FILE: str = "entities.json.enc"
CHUNKS_DB: str = "chunks.db"
EVENTS_DB: str = "events.db"
SNAPSHOT_DIR: str = "snapshots"
SNAPSHOT_SUFFIX: str = ".ascendsave"
# 增量快照里 SQLite 数据库的页图条目后缀：<库名>.pages
# 页图二进制: <page_size u32 LE><file_size u64 LE><count u32 LE>
#           + {<index u32 LE><length u32 LE><bytes>}*count
_PAGES_SUFFIX: str = ".pages"
# 保留策略：auto（当前/冻结记录）环形保留最近 N 个，quit（退出保存）保留最近 K 个；
# manual（手动）永久保留。live_origin 指向的快照永不自动淘汰
# （当前记录），因此同一来源的实际上限 = N + 1。
# 注：quit 为预留来源（退出保存尚未接入，晋升语义已就绪），
# 保留策略先行——启用时按普通非 auto 保存路径走即可。
AUTO_SNAPSHOT_KEEP: int = 20
QUIT_SNAPSHOT_KEEP: int = 3

# 快照打包的固定文件集合（密钥藏于 manifest.secrets_blob，无需独立文件；
# continent.bin 可再生，不进快照，保持回退点精简；lineage 为世界级元数据，
# 不随快照打包——快照依赖世界内 lineage 提供父子上下文。
# chunks.db 语义：仅玩家改动（dirty chunk）落盘——确定性生成的 tile
# 是 seed 的纯函数、可再生，不写库（ChunkStore v2 策略），
# 因此 chunks.db 本身即动态数据，随快照链走）
_SNAPSHOT_ENTRIES: tuple[str, ...] = (
    MANIFEST_NAME, STATE_FILE, ENTITIES_FILE, CHUNKS_DB, EVENTS_DB,
)


class SnapshotStore:
    """快照文件的原语层：打包/差异/物化/重基座。

    Args:
        root: 存档根目录（临时物化目录建在其下，与 SaveManager.root 同源）。
        lineage: 血缘存储（锚点解析与链式物化用）。
    """

    def __init__(self, root: str, lineage: "LineageStore") -> None:
        self._root = root
        self._lineage = lineage

    # ── 路径 ──────────────────────────────────────────────

    def _snapshot_dir(self, world_id: str) -> str:
        return os.path.join(self._root, world_id, SNAPSHOT_DIR)

    # ── 标识 ──────────────────────────────────────────────

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

    # ── 写入（全量/增量） ─────────────────────────────────

    def write_snapshot_file(self, path: str, wdir: str) -> None:
        """把活目录规范文件打包为加密快照单文件（v2 全量基座，base=null）。

        新建（create_snapshot）、刷新（refresh_snapshot）与晋升
        （promote）共用同一打包原语：内容永远 = 活目录当前状态。
        原子写入（临时文件 + rename）：刷新覆写既有快照时，写入
        中途崩溃不会损坏原文件（该节点是所在线的唯一记录）。

        增量写见 write_delta_snapshot（有锚点时使用）。
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

    def write_delta_payload(
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

    def write_snapshot(
        self, world_id: str, path: str, parent: str,
        base_content_dir: str | None = None,
    ) -> None:
        """按锚点规则写快照文件：有手动锚点 → 增量；否则全量。

        base_content_dir 提供时视为锚点的物化内容（空增量特例：
        fresh record 内容与锚点一致，免链式物化）。

        锚点物化失败（链上缺失/损坏）回退全量——不阻断保存，
        新节点成为新的全量基座（自愈）。
        """
        wdir = os.path.join(self._root, world_id)
        anchor = self.anchor_of(world_id, parent)
        if anchor is None:
            self.write_snapshot_file(path, wdir)
            return
        try:
            self.write_delta_snapshot(
                world_id, path, wdir, anchor, base_content_dir,
            )
        except (SaveFormatError, SaveCryptoError, OSError):
            logger.warning(
                "增量写失败，回退全量基座: %s (%s)", world_id, anchor,
            )
            self.write_snapshot_file(path, wdir)

    def write_delta_snapshot(
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
                self.materialize_snapshot(
                    world_id,
                    os.path.join(self._snapshot_dir(world_id), base_filename),
                    tmp,
                )
                base_content_dir = tmp
            except Exception:
                shutil.rmtree(tmp, ignore_errors=True)
                raise
        try:
            files, pages = self.diff_snapshot(base_content_dir, wdir)
            self.write_delta_payload(path, base_filename, wdir, files, pages)
        finally:
            if tmp is not None:
                shutil.rmtree(tmp, ignore_errors=True)

    # ── 锚点与差异 ────────────────────────────────────────

    def anchor_of(
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
            lineage = self._lineage.get(world_id)
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
    def sqlite_page_size(data: bytes) -> int:
        """从 SQLite 文件头读取页尺寸（offset 16，2 字节大端；1 = 65536）。"""
        if len(data) >= 18:
            ps = struct.unpack_from(">H", data, 16)[0]
            if ps == 1:
                return 65536
            if 512 <= ps <= 65536 and ps & (ps - 1) == 0:
                return ps
        return 4096

    @staticmethod
    def file_differ(base_path: str, new_path: str) -> bool:
        """文件级差异：大小或内容不同；基座缺失 = 新增；新侧缺失 = 继承。"""
        if not os.path.isfile(new_path):
            return False
        if not os.path.isfile(base_path):
            return True
        if os.path.getsize(base_path) != os.path.getsize(new_path):
            return True
        with open(base_path, "rb") as f1, open(new_path, "rb") as f2:
            return f1.read() != f2.read()

    def diff_db_pages(self, base_path: str, new_path: str) -> bytes | None:
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
        page_size = self.sqlite_page_size(new_bytes)
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

    def diff_snapshot(
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
            if self.file_differ(
                os.path.join(base_dir, name), os.path.join(wdir, name),
            ):
                files.append(name)
        pages: dict[str, bytes] = {}
        for name in (CHUNKS_DB, EVENTS_DB):
            payload = self.diff_db_pages(
                os.path.join(base_dir, name), os.path.join(wdir, name),
            )
            if payload is not None:
                pages[name] = payload
        return files, pages

    # ── 读取与物化 ────────────────────────────────────────

    @staticmethod
    def open_snapshot(snapshot_path: str) -> tuple[dict, zipfile.ZipFile]:
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

    def resolve_snapshot_path(
        self, snapshot_path: str, world_id: str | None,
    ) -> str:
        """快照路径解析：绝对/带目录路径原样使用；裸文件名从目标世界的
        快照目录解析（前端以裸文件名经 --snapshot 下发）。

        Returns:
            可打开的快照路径（解析失败时返回原路径，由调用方报错）。
        """
        if os.path.isfile(snapshot_path):
            return snapshot_path
        if not os.path.dirname(snapshot_path) and world_id:
            candidate = os.path.join(self._snapshot_dir(world_id), snapshot_path)
            if os.path.isfile(candidate):
                return candidate
        return snapshot_path

    def unpack_full(self, tmp_dir: str, zf: zipfile.ZipFile) -> None:
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

    def apply_pages(self, db_path: str, payload: bytes) -> None:
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

    def apply_delta(self, tmp_dir: str, zf: zipfile.ZipFile) -> None:
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
                self.apply_pages(target, zf.read(info))
            # 其余未知条目：跳过

    def materialize_snapshot(
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
        lineage = self._lineage.get(world_id)
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
            path = os.path.join(self._snapshot_dir(world_id), node)
            if not os.path.isfile(path):
                raise SaveFormatError(f"链上快照缺失: {node}")
            header, zf = self.open_snapshot(path)
            try:
                if header.get("base"):
                    if not os.listdir(target_dir):
                        raise SaveFormatError(f"增量快照缺少基座: {node}")
                    self.apply_delta(target_dir, zf)
                else:
                    self.unpack_full(target_dir, zf)
            finally:
                zf.close()

    # ── 重基座（删除手动锚点后） ──────────────────────────

    def rebase_after_removal(
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
            path = os.path.join(self._snapshot_dir(world_id), name)
            if not os.path.isfile(path):
                continue
            try:
                header, zf = self.open_snapshot(path)
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
                self.materialize_snapshot(
                    world_id,
                    os.path.join(self._snapshot_dir(world_id), old_anchor),
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
                    self.rebase_one(world_id, lineage, anchor_tmp, name)
                except (SaveFormatError, SaveCryptoError, OSError) as exc:
                    logger.warning(
                        "重基座失败（跳过）: %s (%s)", name, exc,
                    )
            shutil.rmtree(anchor_tmp, ignore_errors=True)

    def rebase_one(
        self, world_id: str, lineage: dict, anchor_tmp: str, name: str,
    ) -> None:
        """单个后代重基座：还原内容 → 对新锚点重写增量（或无 → 全量）。"""
        snaps = lineage.get("snapshots", {})
        path = os.path.join(self._snapshot_dir(world_id), name)
        content_tmp = os.path.join(self._root, f".rebase-{uuid.uuid4().hex}")
        os.makedirs(content_tmp, exist_ok=True)
        try:
            header, zf = self.open_snapshot(path)
            try:
                if header.get("base"):
                    shutil.copytree(anchor_tmp, content_tmp, dirs_exist_ok=True)
                    self.apply_delta(content_tmp, zf)
                else:
                    self.unpack_full(content_tmp, zf)
            finally:
                zf.close()
            new_anchor = self.anchor_of(
                world_id, snaps[name].get("parent", ""), lineage,
            )
            if new_anchor is None:
                # 无存活手动祖先：成为新的全量基座
                self.write_snapshot_file(path, content_tmp)
                return
            base_tmp = os.path.join(self._root, f".rebase-{uuid.uuid4().hex}")
            os.makedirs(base_tmp, exist_ok=True)
            try:
                self.materialize_snapshot(
                    world_id,
                    os.path.join(self._snapshot_dir(world_id), new_anchor),
                    base_tmp,
                )
                files, pages = self.diff_snapshot(base_tmp, content_tmp)
                self.write_delta_payload(
                    path, new_anchor, content_tmp, files, pages,
                )
            finally:
                shutil.rmtree(base_tmp, ignore_errors=True)
        finally:
            shutil.rmtree(content_tmp, ignore_errors=True)
