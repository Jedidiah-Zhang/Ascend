#include <olam/space.hpp>

#include <limits>
#include <utility>

namespace olam {
namespace {

std::optional<SpaceError> fail(SpaceErrorCode code, const SpaceId& space, std::string message) {
    return SpaceError{code, space, std::move(message)};
}

// 候选范围与已登记空间有无共同内部位置；边缘相接不算重叠。
bool overlaps(const SpaceDescription& candidate, const Space& space) noexcept {
    const auto& other = space.origin();
    const auto& other_size = space.size();
    return candidate.origin.x < other.x + other_size.width &&
           other.x < candidate.origin.x + candidate.size.width &&
           candidate.origin.y < other.y + other_size.height &&
           other.y < candidate.origin.y + candidate.size.height;
}

}  // namespace

std::optional<SpaceError> SpaceSet::add(SpaceDescription description) {
    if (description.id.value.empty()) {
        return fail(SpaceErrorCode::invalid_identifier, description.id,
                    "Space identifier must not be empty");
    }
    if (description.size.width <= 0 || description.size.height <= 0) {
        return fail(SpaceErrorCode::invalid_size, description.id, "Space size must be positive");
    }
    if (description.origin.x < 0 || description.origin.y < 0) {
        return fail(SpaceErrorCode::negative_origin, description.id,
                    "Space origin must be non-negative");
    }
    constexpr Tick largest = std::numeric_limits<Tick>::max();
    if (description.size.width > largest - description.origin.x ||
        description.size.height > largest - description.origin.y) {
        return fail(SpaceErrorCode::arithmetic_overflow, description.id,
                    "Space range exceeds the tick range");
    }
    if (by_id_.count(description.id) != 0) {
        return fail(SpaceErrorCode::duplicate_identifier, description.id,
                    "Space identifier is already registered");
    }
    const auto layer = by_layer_.find(description.layer);
    if (layer != by_layer_.end()) {
        for (const auto index : layer->second) {
            if (overlaps(description, spaces_[index])) {
                return fail(SpaceErrorCode::same_layer_overlap, description.id,
                            "Space overlaps another space on the same layer");
            }
        }
    }
    spaces_.push_back(Space(description));  // 私有构造仅在 SpaceSet 中调用；失败不改变集合
    const std::size_t index = spaces_.size() - 1;
    // 索引插入可能分配内存；失败时完整回滚，保持集合原状后重新抛出。
    try {
        by_id_.emplace(description.id, index);
        by_layer_[description.layer].push_back(index);
    } catch (...) {
        const auto layer = by_layer_.find(description.layer);
        if (layer != by_layer_.end()) {
            if (!layer->second.empty() && layer->second.back() == index) layer->second.pop_back();
            if (layer->second.empty()) by_layer_.erase(layer);
        }
        by_id_.erase(description.id);
        spaces_.pop_back();
        throw;
    }
    return std::nullopt;
}

const Space* SpaceSet::find(const SpaceId& id) const noexcept {
    const auto found = by_id_.find(id);
    return found == by_id_.end() ? nullptr : &spaces_[found->second];
}

const Space* SpaceSet::locate(Layer layer, const AbsolutePosition& point) const noexcept {
    const auto found = by_layer_.find(layer);
    if (found == by_layer_.end()) return nullptr;
    for (const auto index : found->second) {
        const Space& space = spaces_[index];
        if (space.contains(point)) return &space;
    }
    return nullptr;
}

}  // namespace olam
