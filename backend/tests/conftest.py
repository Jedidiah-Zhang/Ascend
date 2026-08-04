"""pytest 配置。

用法:
    PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/unit/test_space.py \
        --cov=backend/ascend/space --cov-config=backend/tests/.coveragerc --cov-report=term-missing
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_save_root(tmp_path, monkeypatch):
    """全量测试隔离：存档写入 tmp 目录，不污染真实 ~/.ascend。

    读取 ascend.config.SAVE_ROOT 的模块（save.manager / game）以模块级
    常量引用，需逐一替换；大陆缓存已随档分发（continent.bin 在世界目录内），
    由上述隔离自动覆盖。
    """
    root = str(tmp_path / "saves")
    monkeypatch.setattr("ascend.config.SAVE_ROOT", root)
    monkeypatch.setattr("ascend.save.manager.SAVE_ROOT", root)
    monkeypatch.setattr("ascend.game.SAVE_ROOT", root)
