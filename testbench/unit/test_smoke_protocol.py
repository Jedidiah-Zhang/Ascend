"""打包冒烟客户端与产物协议对账。

冒烟脚本刻意不 import 后端（暴露打包产物自身的协议破损），协议常量因此
需要与生产常量对账——本测试即是那道对账门禁。
"""

import importlib.util
import json
import struct
from pathlib import Path

import pytest

from miskhak.net.protocol import PROTOCOL_VERSION, encode_message
from olam.content.tile_grid import TILE_GRID_VERSION

_SMOKE_PATH = Path(__file__).resolve().parents[2] / "build" / "ci" / "smoke_server.py"


@pytest.fixture(scope="module")
def smoke():
    spec = importlib.util.spec_from_file_location("smoke_server", _SMOKE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_smoke_protocol_constants_match_production(smoke):
    assert smoke.VERSION_BYTE == PROTOCOL_VERSION
    assert smoke.TILE_BLOB_VERSION == TILE_GRID_VERSION


def test_smoke_frame_layout_matches_production(smoke):
    message = {"type": "request", "request_type": "save_list"}
    framebytes = smoke.encode_message(message)
    version, length = struct.unpack(">BI", framebytes[:5])
    assert version == PROTOCOL_VERSION
    assert length == len(framebytes) - 5
    assert json.loads(framebytes[5:].decode("utf-8")) == message
    # 与生产编码逐位一致（同序 JSON 序列化下）
    assert framebytes == encode_message(message)
