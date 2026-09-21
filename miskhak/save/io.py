"""存档 I/O 工具 — 原子写等共享文件操作。"""

import os
import tempfile


def atomic_write(path: str, data: bytes | str) -> None:
    """原子写文件：mkstemp 唯一临时名 + fsync + os.replace（单一事实来源）。

    崩溃安全：要么旧内容完整、要么新内容完整，绝无半写状态。
    临时文件与目标同目录（同文件系统，replace 原子）；mkstemp 唯一名
    消除并发写同一路径时的互相踩踏。
    data 为 str 时按 UTF-8 编码。

    Args:
        path: 目标路径。
        data: 写入内容（bytes 或 str）。

    Raises:
        OSError: 写入失败。
    """
    payload = data.encode("utf-8") if isinstance(data, str) else data
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".tmp-", suffix=".ascend")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
