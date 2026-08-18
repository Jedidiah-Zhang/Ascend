# build/ — 构建与打包

## 目录职责

| 路径 | 内容 | 是否进 git |
|---|---|---|
| `nuitka/` | 后端编译脚本（Linux `build_backend.sh`、Windows 交叉编译 `build_backend_windows.sh`）、`excludes.txt`、`version.txt` | ✅ |
| `package/` | 舞台目录组装 `assemble_release.sh` + 各平台归档脚本 | ✅ |
| `ci/` | CI 工作流与发布脚本 `publish_release.sh` | ✅ |
| `work/` | **中间产物**：Godot 导出、Nuitka 编译缓存、舞台目录（构建前清空，非版本化） | ❌ |
| `dist/release/` | **最终交付物**（固定名，每次覆盖；版本化存档进 GitHub Releases） | ❌ |

## 一键打包（推荐）

```bash
bash build/build_release.sh all       # 本地全量：前端 + 后端（linux + windows）
bash build/build_release.sh linux     # 或单平台
```

流程：同步语言文件（repo 根 `lang/*.json` → `frontend/lang/`，进 PCK）→
导出前端 → 编译后端 → 组装舞台目录 → 冒烟测试（协议级握手）→ 打归档。
产物：`build/dist/release/ascend-linux.tar.gz`、`ascend-windows.zip`。

发布：`git tag v<版本> && bash build/ci/publish_release.sh`（版本化命名上传
GitHub Releases，本地不留历史产物）。

**发布清单（生成算法变更时）**：若本版本改了大陆生成算法或
`config.py` 中影响宏观场的常量（见 `CONTINENT_GEN_CONSTANT_NAMES`），
递增 `ascend/config.py` 的 `CONTINENT_GEN_VERSION`——打包环境的
大陆缓存漂移诊断依赖它（开发环境靠源码哈希自动覆盖，无需维护）。

**发布清单（C 扩展变更时）**：改了 `backend/ascend/space/_*.c` 后，
须重建各平台二进制（Linux `.so` / Windows `.dll`，见 `nuitka/`
构建脚本）——本地 `.dll` 等旧构建不含新符号，Windows 打包前不
重建会导致符号缺失。

## CI 打包后端（研究平台发行）

前端为闭源商业资产（`frontend/assets/` 不入库），CI 不参与前端构建；
CI 仅打包后端供研究平台使用：

```bash
bash build/build_backend_release.sh linux     # 后端 server 包
bash build/build_backend_release.sh windows   # 或 windows
```

流程：编译后端（Nuitka）→ 组装 server-only 舞台目录（含 `server/lang/`，
后端 i18n 按模块相对路径解析）→ 冒烟 → 归档。
产物：`build/dist/release/ascend-server-linux.tar.gz`、`ascend-server-windows.zip`。

## 发行布局

```
Ascend-<平台>/
├── ascend[.exe|x86_64]   # 游戏可执行（根目录）
├── ascend.pck            # 游戏资源包（前端为双文件模式，embed_pck=false）
├── server/               # 后端 standalone 目录（二进制 + 依赖库）
│   └── server[.exe]
├── .ascend_token         # （运行时由后端生成）
└── README.txt
```

后端为 **standalone 目录模式**（非 onefile）：onefile 在 Linux 上会 fork
出子进程（bootstrap 监督进程 + 真实服务），前端按 PID 无法可靠终止、
孤儿进程占端口会卡死"进入世界"；standalone 下二进制即服务，PID/SIGTERM
语义与前端进程模型一致。

## 手动分步流程（调试用）

```bash
# 0. 同步语言文件（开发期前端直读仓库根 lang/，仅打包需要这一步）
rm -rf frontend/lang && mkdir -p frontend/lang && cp lang/*.json frontend/lang/

# 1. 导出前端（输出 build/work/exports/）
godot --headless --path frontend --export-release "Windows Desktop"
godot --headless --path frontend --export-release "Linux X11"

# 2. 编译后端（Linux 本机 / Windows 用 wine 交叉编译）
bash build/nuitka/build_backend.sh
bash build/nuitka/build_backend_windows.sh

# 3. 组装舞台目录 → 打归档（舞台目录用完即删）
bash build/package/assemble_release.sh linux && bash build/package/linux/make_tar_gz.sh
bash build/package/assemble_release.sh windows && bash build/package/windows/make_zip.sh
```

## GitHub Actions（CI）

工作流：`.github/workflows/release.yml`（触发：推 `v*` 标签或手动运行）。
**CI 仅打包后端**（研究平台发行；前端为闭源商业资产，不走 CI）：

```
push tag v* ──► [ubuntu-latest]  Linux 后端 ──┐
                 [windows-latest] Windows 后端 ─┴─► [release] 上传 GitHub Releases
```

- **Windows 构建用原生 runner**（`build_backend_windows_native.sh`），
  非 wine——真 Windows 上 Nuitka/pefile 依赖扫描均正常，无需 wine 那些规避参数
- runner 工具由 `build/ci/setup_mingw.sh`（winlibs gcc，供 C 加速模块 .dll）安装并缓存
- 手动运行（workflow_dispatch）只构建不上传 Release（产物在 Actions 页下载）
- 发布 job 复用 `build/ci/publish_release.sh`（GITHUB_TOKEN 自动认证）

## 约定

- 版本号单一来源：`build/nuitka/version.txt`（当前 0.0.2-alpha）——Release 命名、
  产物文件名、Windows exe 属性、主菜单显示（`build_release.sh` 拷入前端 PCK）全部
  由此派生；`frontend/project.godot` 无版本字段。打 tag 前跑
  `bash build/ci/check_version.sh --tag v<版本>` 对账（CI 的 check-version job 自动执行）
- **本地永远只有最新版**：中间产物构建前清空；产物固定名每次覆盖；
  历史版本归档只存在于 GitHub Releases（或制品库），不进工作区
- 打包数据文件仅：C 加速模块 `.so`/`.dll` + `schema.sqlite.sql`；
  **`.c` 源码不随包分发**（打包环境无 gcc，编译兜底无意义，且避免源码暴露）
- AI 模型层（torch/LLM 等）不进编译，作为侧车进程运行，与编译后的后端走现有 TCP/IPC
- 签名证书、密钥绝不进 git

## Windows 交叉编译（wine）

`build_backend_windows.sh` 前置：
- wine 内 Windows Python **≤3.12**（Nuitka `--mingw64` 不支持 3.13+）
- `~/mingw64`（mingw-w64 Windows 版 gcc）——仅用于交叉编译 C 加速模块
  为 `.dll`；Nuitka 本体编译使用其自行下载的 winlibs gcc
- wine 内 `pip install nuitka cryptography`

已知要点：`--lto=no --jobs=4`（wine 下 gcc LTO 汇编器偶发段错误）；
`--experimental=force-dependencies-pefile`（wine 缺 x64 MFC42.dll，
64 位 depends.exe 无法运行，改用纯 Python PE 扫描）。
