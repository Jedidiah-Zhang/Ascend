# build/ — 构建与打包

两类包共用同一套流程底座，入口统一为 `build/package.sh`：

| 通道（内部） | 对外产品 | 内容 | 产物目录 |
|---|---|---|---|
| `miskhak` | game | Godot 前端 + 后端 | `build/dist/miskhak/` |
| `kheker` | research | 仅后端（`server/` + `data/` + `lang/`） | `build/dist/kheker/` |

内部目录与脚本参数用通道名（与仓库分区同名），产物文件名 / 归档顶层目录 /
发布 tag 用对外产品名（`ascend-game-*` / `ascend-research-*`）。

## 一键打包

```bash
bash build/package.sh miskhak            # 游戏包（本机平台）
bash build/package.sh miskhak linux      # 或指定单平台
bash build/package.sh kheker all         # 研究包（linux + windows）
```

平台缺省 = 本机；`all` = linux + windows（Windows 在本机走 wine 交叉编译，
CI 的 Windows job 用原生脚本）。

流程（两通道共用；游戏包多前端步骤）：

```text
[0] 同步客户端资源   语言文件 + 版本号 → miskhak/client/（进 PCK；仅游戏包）
[1] 导出前端         godot --headless --export-release（仅游戏包）
[2] 编译后端         Nuitka standalone（build/nuitka/）
[3] 组装舞台         server/ + data/ + lang/（游戏包另叠加 ascend 可执行 + ascend.pck）
[4] 冒烟             build/ci/smoke.sh：端口就绪 → hello 握手 → save_list（game 另验前端文件）
[5] 归档             build/dist/<通道>/（固定名，每次覆盖）
```

## 目录职责

| 路径 | 内容 | 是否进 git |
|---|---|---|
| `package.sh` | 打包入口（通道 × 平台分派） | ✅ |
| `lib/` | 共享底座：`common.sh`（版本/命名/平台）、`stage.sh`（舞台+冒烟）、`make_zip.py` | ✅ |
| `version/` | 版本号单一来源（`core` / `miskhak` / `kheker`） | ✅ |
| `nuitka/` | 后端编译（Linux / Windows 交叉 / Windows 原生） | ✅ |
| `package/miskhak/` | 游戏包：组装 + Linux（tar.gz/deb/rpm/AppImage）与 Windows（zip/安装器）归档 | ✅ |
| `package/kheker/` | 研究包：组装 + 归档（tar.gz/zip） | ✅ |
| `ci/` | 冒烟、版本对账、发布、runner 工具安装 | ✅ |
| `work/` | **中间产物**：前端导出、Nuitka 编译缓存、舞台目录（构建前清空） | ❌ |
| `dist/<通道>/` | **最终交付物**（固定名，每次覆盖；版本化命名发生在发布时刻） | ❌ |

## 版本号

单一来源 `build/version/`：

| 文件 | 含义 |
|---|---|
| `core.txt` | 共享核心版本（olam + miskhak 后端 + data/lang + 协议） |
| `miskhak.txt` | 游戏包版本 = 核心版本 + 游戏序号（如 `0.0.3-alpha.1`） |
| `kheker.txt` | 研究包版本 = 核心版本 + 研究序号 |

- 从任一产品版本**去掉最后一段**即得核心版本；`build/ci/check_version.sh` 强制
  "core 是两个产品版本的前缀"
- 后端二进制（两类包共用的同一编译产物）版本资源取核心版本数字段
- 发布 tag：`game-v<游戏版本>` / `research-v<研究版本>`（对账脚本校验）
- 本地固定名产物不带版本号；发布时才做版本化命名

## 产物命名

| 通道 | 产物 | 归档顶层目录 |
|---|---|---|
| miskhak | `ascend-game-linux.tar.gz`、`.deb`、`.rpm`、`.AppImage`、`ascend-game-windows.zip`、`ascend-game-windows-setup.exe` | `Ascend-Game/` |
| kheker | `ascend-research-linux.tar.gz`、`ascend-research-windows.zip` | `Ascend-Research/` |

发行布局（解压后）：

```text
Ascend-Game/               # 研究包为 Ascend-Research/，内容不含 ascend 可执行与 pck
├── ascend[.exe|x86_64]    # 游戏可执行（根目录）
├── ascend.pck             # 游戏资源包（embed_pck=false）
├── server/                # 后端 standalone 目录（二进制 + 依赖库 + 声明表）
│   └── server[.exe]
├── data/                  # 世界内容数据（JSON；后端启动即需）
├── lang/                  # 后端 i18n 语言文件
├── .ascend_token          # （运行时由后端生成）
└── README.txt
```

`data/` 与 `lang/` 的落位按后端模块相对路径解析（Nuitka standalone 下
`__file__` 含包前缀，`olam/content/loader.py` 与 `miskhak/i18n.py`
以各自包目录为锚向外解析 → 舞台根）；两者内置 `server/<dir>` 回退。

后端为 **standalone 目录模式**（非 onefile）：onefile 在 Linux 上会 fork
出子进程（bootstrap 监督进程 + 真实服务），前端按 PID 无法可靠终止、
孤儿进程占端口会卡死"进入世界"；standalone 下二进制即服务，PID/SIGTERM
语义与前端进程模型一致。

## 发布

**研究包**走 GitHub Releases（CI 自动）：

```bash
# 1. 更新 build/version/kheker.txt（如需同步核心版本，先改 core.txt 再改两产品）
# 2. 打 tag 推送 → CI 构建 linux + windows → 自动发版
git tag research-v<版本> && git push origin research-v<版本>

# 手动发布（本地已构建产物）
bash build/ci/publish_release.sh kheker
```

**游戏包**含闭源前端资产（`miskhak/client/assets/` 不入库），走私有流程；
`publish_release.sh miskhak` 会拒绝公开上传。

CI 工作流（`.github/workflows/release.yml`，触发：推 `research-v*` 标签）：

```text
push tag research-v* ─► [ubuntu] Linux 研究包 ─┐
                        [windows] Windows 研究包 ─┴─► [release] 上传 GitHub Releases
```

- **Windows 构建用原生 runner**（`build_backend_windows_native.sh`），
  非 wine——真 Windows 上 Nuitka/pefile 依赖扫描均正常
- runner 工具由 `build/ci/setup_mingw.sh`（winlibs gcc，供 C 加速模块 .dll）安装并缓存
- 手动运行（workflow_dispatch）只构建不上传 Release（产物在 Actions 页下载）

## 手动分步流程（调试用）

```bash
# 0. 同步语言文件（开发期客户端直读 miskhak/lang/，仅打包需要这一步）
rm -rf miskhak/client/lang && mkdir -p miskhak/client/lang && cp miskhak/lang/*.json miskhak/client/lang/
cp build/version/miskhak.txt miskhak/client/version.txt

# 1. 导出前端（输出 build/work/exports/<平台>/；目标目录须先存在）
mkdir -p build/work/exports/linux
godot --headless --path miskhak/client --export-release "Linux X11"

# 2. 编译后端（Linux 本机 / Windows 用 wine 交叉编译）
bash build/nuitka/build_backend.sh
bash build/nuitka/build_backend_windows.sh

# 3. 组装舞台 → 冒烟 → 打归档（舞台目录用完即删）
bash build/package/miskhak/assemble.sh linux
bash build/ci/smoke.sh build/work/staging/miskhak-linux linux game
bash build/package/miskhak/linux/make_tar_gz.sh
rm -rf build/work/staging/miskhak-linux

# 研究包同理：build/package/kheker/{assemble,build}.sh
```

## 约定

- 版本号单一来源：`build/version/`（三个文件；产品版本以核心版本为前缀）
- **本地永远只有最新版**：中间产物构建前清空；产物固定名每次覆盖；
  历史版本归档只存在于 GitHub Releases（或制品库），不进工作区
- 打包数据文件仅：C 加速模块 `.so`/`.dll` + `schema.sqlite.sql` + `data/*.json`
  + `lang/*.json`（舞台根配送，见上）+ `olam/declarations/impl_digests.json`；
  **`.c` 源码不随包分发**（打包环境无 gcc，编译兜底无意义，且避免源码暴露）
- AI 模型层（torch/LLM 等）不进编译，作为侧车进程运行，与编译后的后端走现有 TCP/IPC
- 签名证书、密钥绝不进 git

## 发布清单

**生成算法变更时**：改了大陆生成算法或 `olam/constants.py` 中影响宏观场的
常量（见 `CONTINENT_GEN_CONSTANT_NAMES`），递增 `CONTINENT_GEN_VERSION`——
打包环境的大陆缓存漂移诊断依赖它（开发环境靠源码哈希自动覆盖）。

**C 扩展变更时**：改了 `olam/generation/_*.c` 或 `olam/modules/terrain/_state.c` 后，
须重建各平台二进制（Linux `.so` / Windows `.dll`，见 `nuitka/` 构建脚本）——
旧的 `.dll` 不含新符号，Windows 打包前不重建会导致符号缺失。

**版本变更时**：`core.txt` 变更须同步更新 `miskhak.txt` / `kheker.txt` 的前缀
（`check_version.sh` 兜底）；打 tag 前跑 `bash build/ci/check_version.sh --tag <ref>`。

## Windows 交叉编译（wine）

`build_backend_windows.sh` 前置：
- wine 内 Windows Python **≤3.12**（Nuitka `--mingw64` 不支持 3.13+）
- `~/mingw64`（mingw-w64 Windows 版 gcc）——仅用于交叉编译 C 加速模块
  为 `.dll`；Nuitka 本体编译使用其自行下载的 winlibs gcc
- wine 内 `pip install nuitka cryptography`

已知要点：`--lto=no --jobs=4`（wine 下 gcc LTO 汇编器偶发段错误）；
`--experimental=force-dependencies-pefile`（wine 缺 x64 MFC42.dll，
64 位 depends.exe 无法运行，改用纯 Python PE 扫描）。
