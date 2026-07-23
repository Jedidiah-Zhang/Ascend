"""请求处理程序 — 按 request_type 组织。

每个模块提供一个工厂函数，返回 {request_type: handler} 映射。
"""


def parse_coord(coord) -> "tuple[int, int] | None":
    """校验并解析单个 chunk 坐标（各 handler 共用）。

    合法坐标：长度 ≥2 的序列，前两元素为整值 int/float（排除 bool）。
    非整值浮点（如 10.9）视为非法——不静默截断到错误 chunk。

    Args:
        coord: 客户端载荷中的单个坐标项。

    Returns:
        (cx, cy) 或 None（非法时）。
    """
    if not isinstance(coord, (list, tuple)) or len(coord) < 2:
        return None
    result = []
    for v in coord[:2]:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return None
        if isinstance(v, float) and not v.is_integer():
            return None
        result.append(int(v))
    return (result[0], result[1])
