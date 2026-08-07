# package/ — 发布打包脚本

## 管线

```
assemble_release.sh [linux|windows]   → 舞台目录（work/staging/Ascend-<平台>/，固定名）
<平台>/make_*.sh                      → 最终交付物（dist/release/，固定名，覆盖式）
build/ci/publish_release.sh           → GitHub Releases（版本化命名，传完即删）
```

## 已实现

| 脚本 | 产物 | 说明 |
|---|---|---|
| `assemble_release.sh` | 舞台目录 | 游戏 exe+pck 到根目录、`server/` 后端目录整体复制 |
| `linux/make_tar_gz.sh` | `ascend-linux.tar.gz` | 通用分发 |
| `linux/make_deb.sh` | `ascend-linux.deb` | Debian/Ubuntu（/opt/ascend + 桌面注册） |
| `linux/make_rpm.sh` | `ascend-linux.rpm` | Fedora/OpenSUSE（结构同 deb） |
| `linux/make_appimage.sh` | `ascend-linux.AppImage` | 单文件免安装（AppRun 启动游戏） |
| `windows/make_zip.sh` | `ascend-windows.zip` | 绿色版 |
| `windows/make_installer.sh` | `ascend-windows-setup.exe` | Inno Setup 安装器 |

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

- 版本号从 `build/nuitka/version.txt` 读取（单一来源），**本地产物不带版本号**，
  版本化命名只发生在发布（`publish_release.sh`）时刻
- 所有脚本只消费 `build/work/` 下的产物，不直接依赖源码树
- 舞台目录生命周期 = 组装 → 归档 → 删除
