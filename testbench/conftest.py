"""pytest 配置。

用法:
    PYTHONPATH=. .venv/bin/python -m pytest testbench/unit/test_space.py \
        --cov=olam/generation --cov-config=testbench/.coveragerc --cov-report=term-missing
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_save_root(tmp_path, monkeypatch):
    """全量测试隔离：存档写入 tmp 目录，不污染真实 ~/.ascend。

    读取 miskhak.config.SAVE_ROOT 的模块（save.manager / game）以模块级
    常量引用，需逐一替换；大陆缓存已随档分发（continent.bin 在世界目录内），
    由上述隔离自动覆盖。ChunkStore / WorldTree 归档同样指向系统临时目录
    （/tmp/ascend-dev，跨测试运行持久）——一并隔离，避免旧格式残留
    （如 BLOB 版本变更）污染后续运行。
    """
    root = str(tmp_path / "saves")
    monkeypatch.setattr("miskhak.save.manager.SAVE_ROOT", root)
    monkeypatch.setattr("miskhak.app.SAVE_ROOT", root)
    # ChunkStore / WorldTree 归档默认指向系统临时目录 /tmp/ascend-dev
    # （跨测试运行持久）——一并隔离，避免旧格式残留（如 BLOB 版本变更）
    # 污染后续运行。game 以模块级绑定引用，需逐模块替换。
    dev = str(tmp_path / "dev")
    monkeypatch.setattr("miskhak.save.chunk_store._DEFAULT_DB_PATH",
                        dev + "/chunks.db")
    monkeypatch.setattr("miskhak.app.CHUNK_STORE_DB_PATH",
                        dev + "/chunks.db")
    monkeypatch.setattr("miskhak.app.WT_ARCHIVE_PATH",
                        dev + "/events.db")
