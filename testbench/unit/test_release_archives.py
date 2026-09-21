"""deb / rpm 归档脚本的隔离校验（无 dpkg/rpmbuild 时以替身驱动）。

替身只做契约检查与产物占位，不解析真实包格式；因此这里锁定的是
"脚本引用的舞台文件与包元数据"，真实包格式由对应发行版工具负责。
"""

import os
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SKIP = pytest.mark.skipif(os.name != "posix", reason="POSIX 打包脚本")

DEB_STUB = """#!/bin/sh
set -eu
[ "$1" = "--build" ]
pkg="$2"
out="$3"
[ -f "$pkg/DEBIAN/control" ] || { echo "missing control" >&2; exit 2; }
for f in opt/ascend/ascend.x86_64 opt/ascend/ascend.pck opt/ascend/server/server \\
         opt/ascend/data/terrain.json opt/ascend/lang/zh_CN.json; do
  [ -e "$pkg/$f" ] || { echo "missing $f" >&2; exit 3; }
done
grep -q '^Package: ascend$' "$pkg/DEBIAN/control" || { echo "bad package" >&2; exit 4; }
grep -q '^Version: 0.0.2~alpha$' "$pkg/DEBIAN/control" || { echo "bad version" >&2; exit 5; }
mkdir -p "$(dirname "$out")"
: > "$out"
"""

RPM_STUB = """#!/bin/sh
set -eu
spec=""
top=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --define) top="$2"; shift 2 ;;
    -bb) spec="$2"; shift 2 ;;
    *) shift ;;
  esac
done
[ -f "$spec" ] || { echo "missing spec" >&2; exit 2; }
grep -q '^Name:           ascend$' "$spec" || { echo "bad name" >&2; exit 3; }
out="${top#_topdir }/RPMS/x86_64/ascend-0.0.2-0.alpha.x86_64.rpm"
mkdir -p "$(dirname "$out")"
: > "$out"
"""


def _stage(root: Path) -> Path:
    (root / "ascend.x86_64").touch()
    (root / "ascend.pck").touch()
    (root / "server").mkdir()
    (root / "server" / "server").touch()
    (root / "data").mkdir()
    (root / "data" / "terrain.json").touch()
    (root / "lang").mkdir()
    (root / "lang" / "zh_CN.json").touch()
    return root


def _fixture(tmp_path: Path) -> dict:
    """按脚本的相对路径约定摆好仓库骨架（只复制脚本所需子树）。"""
    import shutil

    build = tmp_path / "build"
    shutil.copytree(REPO / "build", build,
                    ignore=shutil.ignore_patterns("work", "dist"))
    stage = build / "work" / "staging" / "Ascend-linux"
    stage.mkdir(parents=True, exist_ok=True)
    _stage(stage)
    bindir = tmp_path / "bin"
    bindir.mkdir()
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}")
    return {"tmp": tmp_path, "bindir": bindir, "env": env, "stage": stage}


def _install_stub(bindir: Path, name: str, text: str) -> None:
    stub = bindir / name
    stub.write_text(text)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)


@SKIP
def test_deb_script_packages_stage_with_data_and_lang(tmp_path):
    fx = _fixture(tmp_path)
    _install_stub(fx["bindir"], "dpkg-deb", DEB_STUB)
    script = tmp_path / "build" / "package" / "linux" / "make_deb.sh"
    result = subprocess.run(["bash", str(script)], cwd=tmp_path,
                            env=fx["env"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "build" / "dist" / "release" / "ascend-linux.deb").exists()


@SKIP
def test_rpm_script_packages_stage_with_data_and_lang(tmp_path):
    fx = _fixture(tmp_path)
    _install_stub(fx["bindir"], "rpmbuild", RPM_STUB)
    script = tmp_path / "build" / "package" / "linux" / "make_rpm.sh"
    result = subprocess.run(["bash", str(script)], cwd=tmp_path,
                            env=fx["env"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    spec = (tmp_path / "build" / "work" / "rpm" / "SPECS" / "ascend.spec").read_text()
    install_line = next(line for line in spec.splitlines() if line.startswith("cp -r "))
    # 安装行逐个引用舞台条目（数据与语言文件必须随包配送）；
    # 条目以 `"<stage>"/<条目>` 形式出现，按路径分隔符切分后逐项比对。
    referenced = {token.rsplit('"/', 1)[-1].rstrip('"') for token in install_line.split() if '"/' in token}
    for item in ("ascend.x86_64", "ascend.pck", "server", "data", "lang"):
        assert item in referenced, f"spec 缺少 {item}"
    assert '%files' in spec
    assert (tmp_path / "build" / "dist" / "release" / "ascend-linux.rpm").exists()
