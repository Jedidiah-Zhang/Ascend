"""区域边界门禁 — olam 区独立、kheker 只依赖 olam。

分区依赖规则：

- R1 定义架构（kernel/meta/compile/runtime/evidence/protocols）只依赖本区
  与标准库，不得依赖 modules/content/generation/adapters；
- R2 内容生产（generation）不得依赖运行适配（adapters）与游戏服务；
- R3 运行适配（adapters，含 runtime）不得依赖游戏服务；
- R4 整个 olam 区不得依赖游戏服务（miskhak.* 未经显式登记即违规）；
- R5 kheker（仓库根工具链）只依赖 olam 与自身，不得依赖游戏服务。

过渡豁免（显式登记）：
- ``miskhak.events``：世界侧仅驱动层订阅作用域与运行适配事件发布
  （天气/地形/实体；事件总线属游戏事件层，保留最小接触面）；
- 研究侧仅归档探针（kheker/historical/）构造事件总线。
"""

from __future__ import annotations

import ast
from importlib.util import resolve_name
from pathlib import Path, PurePosixPath

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORLD = ROOT / "olam"

CORE_DIRS = ("kernel", "meta", "compile", "runtime", "evidence", "protocols")

GAME_SERVICES = {"miskhak.save", "miskhak.net", "miskhak.terminal", "miskhak.entity",
                 "miskhak", "miskhak.lifecycle"}
_MISSING = object()

# 过渡豁免：模块全名 → 允许的相对路径集合；None = 区内任意文件
TRANSITIONAL_WORLD: dict[str, set[str] | None] = {
    "miskhak.log": None,
    "olam.kernel.mathutil": None,
    # 事件总线属游戏事件层；当前登记在驱动层、运行适配（天气/地形/实体）
    # 与天气事件契约
    "miskhak.events": {
        "olam/runtime/driver.py",
        "olam/adapters/",
        "olam/generation/weather_field/events.py",
    },
}
TRANSITIONAL_RESEARCH: dict[str, set[str] | None] = {
    # 归档探针构造 WeatherEngine 需要事件总线；入口在 kheker/historical/
    "miskhak.events": {"kheker/historical/"},
}


def _imports(path: Path, rel: str) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = ".".join(PurePosixPath(rel).parent.parts)
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                module = resolve_name("." * node.level + module, package)
            found.add(module)
            found.update(
                f"{module}.{alias.name}" for alias in node.names if alias.name != "*"
            )
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def _registered(transitional: dict[str, set[str] | None], name: str):
    """按最长前缀匹配登记项（支持 ``miskhak.events.event``）。"""
    for key in sorted(transitional, key=len, reverse=True):
        if name == key or name.startswith(key + "."):
            return transitional[key]
    return _MISSING


def _world_files(sub: str) -> list[tuple[Path, str]]:
    base = WORLD / sub
    out: list[tuple[Path, str]] = []
    for path in sorted(base.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        out.append((path, "olam/" + path.relative_to(WORLD).as_posix()))
    return out


def _check(files, label: str, *, forbidden_layers: set[str],
           transitional: dict[str, set[str] | None]) -> list[str]:
    issues: list[str] = []
    for path, rel in files:
        for name in _imports(path, rel):
            allowed = _registered(transitional, name)
            if allowed is not _MISSING:
                if allowed and not any(
                    rel == item or (item.endswith("/") and rel.startswith(item))
                    for item in allowed
                ):
                    issues.append(f"[{label}] {rel}: 过渡依赖未登记 {name}")
                continue
            if any(name == s or name.startswith(s + ".") for s in GAME_SERVICES):
                issues.append(f"[{label}] {rel}: 禁用依赖 {name}")
                continue
            if name == "olam" or name.startswith("olam."):
                if name.count(".") >= 1 and name.split(".")[1] in forbidden_layers:
                    issues.append(f"[{label}] {rel}: 跨层依赖 {name}")
                continue
    return issues


@pytest.mark.parametrize("source", [
    "import olam.generation.continent",
    "from olam.generation.continent import ContinentGenerator",
    "from olam import generation",
    "from ..generation import continent",
    "from .. import generation",
])
def test_gate_rejects_core_to_generation(tmp_path, source):
    path = tmp_path / "probe.py"
    path.write_text(source, encoding="utf-8")
    assert _check([(path, "olam/runtime/probe.py")], "core",
                  forbidden_layers={"generation"}, transitional={})


def test_gate_allows_relative_core_imports(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text("from ..kernel import rng\nfrom . import state\n", encoding="utf-8")
    assert not _check([(path, "olam/runtime/probe.py")], "core",
                      forbidden_layers={"generation"}, transitional={})


def test_gate_limits_registered_game_imports(tmp_path):
    path = tmp_path / "probe.py"
    path.write_text("from miskhak.events import WorldTree", encoding="utf-8")
    assert _check([(path, "olam/kernel/probe.py")], "core",
                  forbidden_layers=set(), transitional=TRANSITIONAL_WORLD)
    assert not _check([(path, "olam/adapters/weather/probe.py")], "adapters",
                      forbidden_layers=set(), transitional=TRANSITIONAL_WORLD)


class TestWorldZoneBoundaries:
    def test_core_only_depends_on_core(self):
        # kernel/meta/compile/runtime 不得依赖 modules/content/generation/adapters；
        # evidence/protocols 允许引用模块声明（toy/primitives 为验收夹具与声明原语）
        strict = {"kernel", "meta", "compile", "runtime"}
        files_strict = [(p, r) for d in strict for p, r in _world_files(d)]
        issues = _check(files_strict, "core",
                        forbidden_layers={"modules", "content", "generation", "adapters"},
                        transitional=TRANSITIONAL_WORLD)
        loose = {"evidence", "protocols"}
        files_loose = [(p, r) for d in loose for p, r in _world_files(d)]
        issues += _check(files_loose, "core",
                         forbidden_layers={"generation", "adapters"},
                         transitional=TRANSITIONAL_WORLD)
        assert not issues, "\n".join(issues)

    def test_generation_does_not_depend_on_adapters(self):
        files = _world_files("generation")
        issues = _check(files, "generation", forbidden_layers={"adapters"},
                        transitional=TRANSITIONAL_WORLD)
        assert not issues, "\n".join(issues)

    def test_adapters_do_not_depend_on_game_services(self):
        files = [(p, r) for d in ("adapters", "runtime") for p, r in _world_files(d)]
        issues = _check(files, "adapters", forbidden_layers=set(),
                        transitional=TRANSITIONAL_WORLD)
        assert not issues, "\n".join(issues)

    def test_world_never_imports_legacy_facades(self):
        issues = _check(_world_files(""), "world", forbidden_layers=set(),
                        transitional=TRANSITIONAL_WORLD)
        assert not issues, "\n".join(issues)


class TestResearchZoneBoundaries:
    def test_research_only_depends_on_world(self):
        files: list[tuple[Path, str]] = []
        for path in sorted((ROOT / "kheker").rglob("*.py")):
            if "__pycache__" in path.parts or ".lake" in path.parts:
                continue
            files.append((path, path.relative_to(ROOT).as_posix()))
        issues = _check(files, "kheker", forbidden_layers=set(),
                        transitional=TRANSITIONAL_RESEARCH)
        assert not issues, "\n".join(issues)
