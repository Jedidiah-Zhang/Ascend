"""内容数据加载 — 世界内容（JSON 数据文件）。

把"能调整的内容"（地形/群系/气候/天气/世界生成参数）从代码里抽出来
存成 JSON 文件：改内容只改数据文件、不用改代码，也为未来 Mod 提供
数据层的修改入口（详见 Mod 三层基础设施的第 1 层设计）。
- `data/<kind>.json`：仓库内置默认内容（单一事实源，入库）。
- 类型校验/缺省由各领域模块（如 space/terrain.py 的 TerrainDef）负责。
- 未来 Mod 覆盖机制（mods/*/<kind>.json）留待 mod 加载器设计时再定。

路径解析（双布局回退，与 i18n.py 同源约定）：
- 开发：`backend/ascend/data.py` 上三级 → 仓库根 → `根/data`。
- 发布（Nuitka standalone）：`__file__` 含包前缀，上三级 → 舞台根
  `STAGE`；数据配送到 `STAGE/data`（主）或 `STAGE/server/data`（回退）。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

_NS_PATTERN = re.compile(r"^[a-z0-9_]+:[a-z0-9_]+$")


def _resolve_content_dir(dirname: str, here: Path | None = None) -> Path:
    """按模块位置解析内容目录（开发=仓库根；发布=舞台根或 server/ 内）。

    Args:
        dirname: 内容目录名（"data"/"lang"）。
        here: 模块文件路径（测试注入模拟布局用；None = 本模块 __file__）。
    """
    here = (here or Path(__file__)).resolve()
    primary = here.parent.parent.parent / dirname  # 开发根 / 发布舞台根
    if primary.is_dir():
        return primary
    fallback = here.parent.parent / dirname  # 发布 server/ 内
    return fallback if fallback.is_dir() else primary


DATA_DIR: Path = _resolve_content_dir("data")


def split_ns_id(ns_id: str) -> tuple[str, str]:
    """拆分命名空间 id `<ns>:<local>`，非法格式抛 ValueError（fail fast）。

    Args:
        ns_id: 形如 "ascend:grassland" 的命名空间 id。

    Returns:
        (ns, local) 二元组。

    Raises:
        ValueError: 不匹配 `<ns>:<local>`（小写字母/数字/下划线）。
    """
    if not _NS_PATTERN.match(ns_id):
        raise ValueError(f"注册表键非法（应为 <ns>:<local>）: {ns_id!r}")
    ns, _, local = ns_id.partition(":")
    return ns, local


def load_content(kind: str) -> dict:
    """加载 <kind> 内容：data/<kind>.json。

    Args:
        kind: 内容种类（如 "terrain"），对应 data/<kind>.json。

    Returns:
        JSON 文档（顶层对象）。

    Raises:
        FileNotFoundError: data/<kind>.json 缺失。
        ValueError: JSON 解析失败或顶层非对象。
    """
    path = DATA_DIR / f"{kind}.json"
    if not path.exists():
        raise FileNotFoundError(f"内容数据缺失: {path}")
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError(f"内容数据解析失败: {path}: {exc}") from exc
    if not isinstance(doc, dict):
        raise ValueError(f"内容数据顶层必须是对象: {path}")
    return doc
