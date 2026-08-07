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
| `linux/make_tar_gz.sh` | `dist/release/ascend-linux.tar.gz` | 通用分发，打包后删舞台目录 |
| `windows/make_zip.sh` | `dist/release/ascend-windows.zip` | 绿色版，打包后删舞台目录 |

## 规划中

| 平台 | 产物 | 工具 |
|---|---|---|
| Windows | 安装器 `.exe` | Inno Setup |
| Linux | `.deb` / `.rpm` | dpkg-deb / fpm（安装到 /opt、桌面图标） |
| Linux | `.AppImage` | appimagetool |
| macOS | `.dmg` | hdiutil |

## 约定

- 版本号从 `build/nuitka/version.txt` 读取（单一来源），**本地产物不带版本号**，
  版本化命名只发生在发布（`publish_release.sh`）时刻
- 所有脚本只消费 `build/work/` 下的产物，不直接依赖源码树
- 舞台目录生命周期 = 组装 → 归档 → 删除
