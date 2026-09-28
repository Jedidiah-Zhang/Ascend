#pragma once

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <vector>

namespace olam {

// 位置与尺寸使用整数刻度；1 刻度 = 0.001 米（首版精度暂定，见设计实施基线）。
inline constexpr std::int64_t ticks_per_meter = 1000;
using Tick = std::int64_t;
using Layer = std::int32_t;

// 空间身份：非空且不重复；创建后不隐式复用。
struct SpaceId {
    std::string value;

    bool operator==(const SpaceId& other) const noexcept { return value == other.value; }
    bool operator!=(const SpaceId& other) const noexcept { return !(*this == other); }
    bool operator<(const SpaceId& other) const noexcept { return value < other.value; }
};

// 统一平面参照中的位置；分量为非负刻度。
struct AbsolutePosition {
    Tick x = 0;
    Tick y = 0;

    bool operator==(const AbsolutePosition& other) const noexcept {
        return x == other.x && y == other.y;
    }
    bool operator!=(const AbsolutePosition& other) const noexcept { return !(*this == other); }
};

// 所属空间内的位置；有效范围左闭右开，即 [0, 尺寸)。
struct LocalPosition {
    Tick x = 0;
    Tick y = 0;

    bool operator==(const LocalPosition& other) const noexcept {
        return x == other.x && y == other.y;
    }
    bool operator!=(const LocalPosition& other) const noexcept { return !(*this == other); }
};

struct Size {
    Tick width = 0;
    Tick height = 0;

    bool operator==(const Size& other) const noexcept {
        return width == other.width && height == other.height;
    }
    bool operator!=(const Size& other) const noexcept { return !(*this == other); }
};

// 创建空间所需的完整描述；各字段的合法性在登记时检查。
struct SpaceDescription {
    SpaceId id;
    Layer layer = 0;
    AbsolutePosition origin;
    Size size;
};

// 空间创建的失败类别；说明为默认语言原文，模块接入展示层前不本地化。
enum class SpaceErrorCode {
    invalid_identifier,
    invalid_size,
    negative_origin,
    arithmetic_overflow,
    duplicate_identifier,
    same_layer_overlap,
};

struct SpaceError {
    SpaceErrorCode code = SpaceErrorCode::invalid_identifier;
    SpaceId space;
    std::string message;
};

// 已登记空间：身份、层序与范围创建后固定，只提供只读查询。
class Space {
public:
    const SpaceId& id() const noexcept { return id_; }
    Layer layer() const noexcept { return layer_; }
    const AbsolutePosition& origin() const noexcept { return origin_; }
    const Size& size() const noexcept { return size_; }
    // 上界是几何边界描述，不是合法内部位置。
    AbsolutePosition upper_bound() const noexcept {
        return {origin_.x + size_.width, origin_.y + size_.height};
    }

    bool contains(const AbsolutePosition& point) const noexcept {
        return point.x >= origin_.x && point.y >= origin_.y &&
               point.x - origin_.x < size_.width && point.y - origin_.y < size_.height;
    }
    bool contains_local(const LocalPosition& point) const noexcept {
        return point.x >= 0 && point.y >= 0 && point.x < size_.width && point.y < size_.height;
    }

    // 越界转换返回空，不夹取、不环绕、不换到相邻空间。
    std::optional<LocalPosition> to_local(const AbsolutePosition& point) const noexcept {
        if (!contains(point)) return std::nullopt;
        return LocalPosition{point.x - origin_.x, point.y - origin_.y};
    }
    std::optional<AbsolutePosition> to_absolute(const LocalPosition& point) const noexcept {
        if (!contains_local(point)) return std::nullopt;
        return AbsolutePosition{origin_.x + point.x, origin_.y + point.y};
    }

private:
    friend class SpaceSet;
    // 复制身份可能分配内存，因此不声明 noexcept。
    explicit Space(const SpaceDescription& description)
        : id_(description.id), layer_(description.layer), origin_(description.origin),
          size_(description.size) {}

    SpaceId id_;
    Layer layer_ = 0;
    AbsolutePosition origin_;
    Size size_;
};

// 同一集合内的二维空间：同层范围不能重叠，异层可以重叠；重叠不建立连通。
class SpaceSet {
public:
    // 校验并登记空间：校验失败返回错误且不改变已有空间，成功返回空；
    // 索引插入的内存分配失败会回滚已推送的空间并重新抛出 std::bad_alloc，集合保持原状。
    std::optional<SpaceError> add(SpaceDescription description);

    // 返回的指针在下次 add 前有效。
    const Space* find(const SpaceId& id) const noexcept;
    // 按层序与绝对位置定位；未命中返回空，不隐式创建、不跨层查找。
    const Space* locate(Layer layer, const AbsolutePosition& point) const noexcept;

    // 登记顺序的只读列表；元素引用在下次 add 前有效。
    const std::vector<Space>& spaces() const noexcept { return spaces_; }
    std::size_t size() const noexcept { return spaces_.size(); }
    bool empty() const noexcept { return spaces_.empty(); }

private:
    std::vector<Space> spaces_;
    std::map<SpaceId, std::size_t> by_id_;
    std::map<Layer, std::vector<std::size_t>> by_layer_;
};

}  // namespace olam
