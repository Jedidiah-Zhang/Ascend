"""两类包（game / research）的命名、版本与归档契约。

内部通道 miskhak/kheker ↔ 对外产品 game/research 的映射是打包的对外契约，
这里逐条锁定；归档结构与版本前缀规则一旦漂移即失败。
"""

import subprocess
import tarfile
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
COMMON = "build/lib/common.sh"
SKIP_WIN = pytest.mark.skipif("win32" in __import__("sys").platform, reason="POSIX 脚本")


def _bash(script: str) -> str:
    """在仓库根执行 bash 片段（source 共享底座后调用函数）。"""
    result = subprocess.run(
        ["bash", "-c", f"source {COMMON}\n{script}"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_version_files_exist_for_each_channel():
    for channel in ("core", "miskhak", "kheker"):
        path = REPO / "build" / "version" / f"{channel}.txt"
        assert path.is_file(), f"缺少版本文件 {path}"
        assert _bash(f"ascend_version {channel}") == path.read_text().strip()


def test_product_versions_are_prefixed_by_core():
    core = _bash("ascend_version core")
    for channel in ("miskhak", "kheker"):
        version = _bash(f"ascend_version {channel}")
        assert version.startswith(core + "."), (
            f"{channel} 版本 {version} 未以核心版本 {core} 为前缀"
        )


def test_version_check_script_passes_on_current_tree():
    result = subprocess.run(
        ["bash", "build/ci/check_version.sh"], cwd=REPO,
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "版本对账通过" in result.stdout


def test_version_check_rejects_release_tag_mismatch():
    result = subprocess.run(
        ["bash", "build/ci/check_version.sh", "--tag", "research-v9.9.9"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "版本对账失败" in result.stderr


@pytest.mark.parametrize("channel,product", [("miskhak", "game"), ("kheker", "research")])
def test_channel_maps_to_external_product(channel, product):
    assert _bash(f"ascend_product_of {channel}") == product
    assert _bash(f"ascend_artifact_base {channel} linux") == f"ascend-{product}-linux"


def test_archive_top_dir_uses_external_product_name():
    assert _bash("ascend_stage_top miskhak") == "Ascend-Game"
    assert _bash("ascend_stage_top kheker") == "Ascend-Research"


@pytest.mark.parametrize("tag,channel", [
    ("game-v0.0.3-alpha.1", "miskhak"),
    ("research-v0.0.3-alpha.1", "kheker"),
])
def test_release_tag_prefix_maps_to_channel(tag, channel):
    assert _bash(f"ascend_channel_of_tag {tag}") == channel


def test_release_tag_without_product_prefix_is_rejected():
    result = subprocess.run(
        ["bash", "-c", f"source {COMMON}; ascend_channel_of_tag v0.0.3-alpha.1"],
        cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode != 0


def test_entry_rejects_unknown_channel():
    result = subprocess.run(
        ["bash", "build/package.sh", "unknown"], cwd=REPO,
        capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "未知打包通道" in result.stderr


@pytest.mark.parametrize("argv", [
    ["build/package.sh", "kheker", "mars"],
    ["build/package/kheker/build.sh", "mars"],
    ["build/package/miskhak/assemble.sh", "mars"],
])
def test_entry_rejects_unknown_platform(argv):
    # 入口必须在主 shell 校验平台：映射函数内的 ascend_die 只退出命令替换子 shell，
    # 漏校验会让脚本带着空平台继续跑（历史缺陷）。
    result = subprocess.run(
        ["bash", *argv], cwd=REPO, capture_output=True, text=True,
    )
    assert result.returncode != 0
    assert "未知平台" in result.stderr


def _fixture(tmp_path: Path, channel: str, platform: str) -> Path:
    """复制 build/ 脚本骨架并造一个舞台目录（不含真实产物）。"""
    import shutil

    build = tmp_path / "build"
    if not build.exists():
        shutil.copytree(REPO / "build", build,
                        ignore=shutil.ignore_patterns("work", "dist"))
    stage = build / "work" / "staging" / f"{channel}-{platform}"
    shutil.rmtree(stage, ignore_errors=True)
    (stage / "server").mkdir(parents=True)
    (stage / "server" / "server").write_bytes(b"stub")
    for name in ("data", "lang"):
        (stage / name).mkdir()
        (stage / name / "x.json").write_text("{}")
    return stage


@SKIP_WIN
def test_miskhak_tar_archive_uses_product_top_dir(tmp_path):
    stage = _fixture(tmp_path, "miskhak", "linux")
    (stage / "ascend.x86_64").write_bytes(b"stub")
    (stage / "ascend.pck").write_bytes(b"stub")
    result = subprocess.run(
        ["bash", "build/package/miskhak/linux/make_tar_gz.sh"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    archive = tmp_path / "build" / "dist" / "miskhak" / "ascend-game-linux.tar.gz"
    assert archive.is_file()
    with tarfile.open(archive) as tf:
        names = tf.getnames()
    assert "Ascend-Game/ascend.x86_64" in names
    assert "Ascend-Game/server/server" in names
    assert all(name in ("Ascend-Game",) or name.startswith("Ascend-Game/") for name in names)


@SKIP_WIN
def test_kheker_tar_and_zip_use_research_top_dir(tmp_path):
    stage = _fixture(tmp_path, "kheker", "linux")
    result = subprocess.run(
        ["bash", "build/package/kheker/archive.sh", "linux"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    archive = tmp_path / "build" / "dist" / "kheker" / "ascend-research-linux.tar.gz"
    with tarfile.open(archive) as tf:
        assert all(n in ("Ascend-Research",) or n.startswith("Ascend-Research/") for n in tf.getnames())

    _fixture(tmp_path, "kheker", "windows")
    result = subprocess.run(
        ["bash", "build/package/kheker/archive.sh", "windows"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
    zip_path = tmp_path / "build" / "dist" / "kheker" / "ascend-research-windows.zip"
    with zipfile.ZipFile(zip_path) as zf:
        names = zf.namelist()
    assert "Ascend-Research/server/server" in names
    assert all(n in ("Ascend-Research",) or n.startswith("Ascend-Research/") for n in names)
