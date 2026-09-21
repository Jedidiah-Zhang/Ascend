# package/ — 发布打包脚本

两类包各占一个子目录；流水线编排与共享逻辑在 `build/package.sh` 与 `build/lib/`。

```text
build/package.sh <通道> [linux|windows|all]
  └─ build/package/<通道>/build.sh <平台>    # 完整流程（导出/编译 → 组装 → 冒烟 → 归档）
       ├─ assemble.sh                        # 组装舞台（后端舞台 + 可选前端）
       └─ <平台>/make_*.sh | archive.sh      # 各格式归档
```

| 通道 | 组装 | 归档脚本 | 产物（`build/dist/<通道>/`） |
|---|---|---|---|
| `miskhak` | `miskhak/assemble.sh` | `miskhak/linux/make_tar_gz.sh` | `ascend-game-linux.tar.gz` |
| | | `miskhak/linux/make_deb.sh` | `ascend-game-linux.deb` |
| | | `miskhak/linux/make_rpm.sh` | `ascend-game-linux.rpm` |
| | | `miskhak/linux/make_appimage.sh` | `ascend-game-linux.AppImage` |
| | | `miskhak/windows/make_zip.sh` | `ascend-game-windows.zip` |
| | | `miskhak/windows/make_installer.sh` | `ascend-game-windows-setup.exe` |
| `kheker` | `kheker/assemble.sh` | `kheker/archive.sh <平台>` | `ascend-research-linux.tar.gz` / `ascend-research-windows.zip` |

舞台目录：`build/work/staging/<通道>-<平台>/`（内部命名），归档顶层目录为
对外产品名（`Ascend-Game/` / `Ascend-Research/`）。

舞台目录生命周期 = 组装 → 各格式消费（共享同一舞台目录）→ 统一删除。

## 格式特性

- **deb/rpm**：安装到 `/opt/ascend`，`/usr/bin/ascend` 软链，桌面菜单 + 图标注册
- **AppImage**：squashfs 只读挂载——token/日志经 `--data-root` 落到
  `user://`（用户可写目录），不依赖程序目录可写性
- **Windows 安装器**：Program Files 安装 + 开始菜单/桌面快捷方式（同上，
  token/日志在 `user://`，避免权限问题）

## 工具依赖与跳过策略

| 格式 | 工具 | 本地缺失时 |
|---|---|---|
| deb | `dpkg-deb` | 跳过并提示 |
| rpm | `rpmbuild` | 跳过并提示 |
| AppImage | `magick` + FUSE | 跳过并提示 |
| 安装器 | `ISCC.exe` + `magick` | 跳过并提示 |

（CI runner 均完整安装；本地缺工具时自动跳过不阻断其余格式。）

## 规划中

| 平台 | 产物 | 工具 |
|---|---|---|
| macOS | `.dmg` | hdiutil（需 macOS runner + 签名） |

## 约定

- 版本号从 `build/version/` 读取（`core` 为共享核心；产品版本见
  `miskhak.txt` / `kheker.txt`），**本地产物不带版本号**，
  版本化命名只发生在发布（`build/ci/publish_release.sh`）时刻
- 所有脚本只消费 `build/work/` 下的产物，不直接依赖源码树
- 组装逻辑只有一份（`build/lib/stage.sh`），两通道的差异只在「是否叠加前端」
