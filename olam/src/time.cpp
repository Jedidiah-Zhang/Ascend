#include <olam/time.hpp>

#include <limits>
#include <numeric>
#include <stdexcept>
#include <utility>

namespace olam {
namespace {

std::optional<TimeError> make_error(TimeErrorCode code, const std::string& clock,
                                    std::string message) {
    return TimeError{code, clock, std::move(message)};
}

std::optional<TimeError> normalize_rate(Rate input, Rate& normalized, const std::string& clock) {
    if (input.numerator < 0) {
        return make_error(TimeErrorCode::invalid_rate, clock, "Clock rate numerator must be non-negative");
    }
    if (input.denominator <= 0) {
        return make_error(TimeErrorCode::invalid_rate, clock, "Clock rate denominator must be positive");
    }
    const std::int64_t divisor = std::gcd(input.numerator, input.denominator);
    normalized = Rate{input.numerator / divisor, input.denominator / divisor};
    return std::nullopt;
}

// 两个规范化倍率相乘；交叉约分后再做带检查的非负整数乘法。
bool multiply_rate(const Rate& left, const Rate& right, Rate& product) noexcept {
    std::int64_t numerator_left = left.numerator;
    std::int64_t denominator_left = left.denominator;
    std::int64_t numerator_right = right.numerator;
    std::int64_t denominator_right = right.denominator;

    const std::int64_t cancel_left = std::gcd(numerator_left, denominator_right);
    numerator_left /= cancel_left;
    denominator_right /= cancel_left;
    const std::int64_t cancel_right = std::gcd(numerator_right, denominator_left);
    numerator_right /= cancel_right;
    denominator_left /= cancel_right;

    constexpr std::int64_t largest = std::numeric_limits<std::int64_t>::max();
    if (numerator_left != 0 && numerator_right > largest / numerator_left) return false;
    if (denominator_left > largest / denominator_right) return false;
    product = Rate{numerator_left * numerator_right, denominator_left * denominator_right};
    return true;
}

std::size_t find_index(const std::vector<Clock>& clocks, const std::string& id) noexcept {
    for (std::size_t index = 0; index < clocks.size(); ++index) {
        if (clocks[index].id() == id) return index;
    }
    return static_cast<std::size_t>(-1);
}

}  // namespace

ClockTree::ClockTree(TimeBase base) : base_(base) {
    if (base_.step_microseconds <= 0) {
        throw std::invalid_argument("time step must be positive");
    }
}

std::optional<TimeError> ClockTree::fail(TimeErrorCode code, const std::string& id,
                                         std::string message) const {
    return make_error(code, id, std::move(message));
}

std::optional<TimeError> ClockTree::add(std::string id, std::string parent, Rate relative,
                                        bool paused) {
    if (id.empty()) {
        return fail(TimeErrorCode::invalid_identifier, id,
                    "Clock identifier must not be empty");
    }
    if (find_index(clocks_, id) != static_cast<std::size_t>(-1)) {
        return fail(TimeErrorCode::duplicate_identifier, id,
                    "Clock identifier is already registered");
    }
    std::size_t parent_index = Clock::no_parent;
    if (clocks_.empty()) {
        if (!parent.empty()) {
            return fail(TimeErrorCode::missing_parent, id,
                        "The first clock must be a root with no parent");
        }
    } else {
        if (parent.empty()) {
            return fail(TimeErrorCode::duplicate_root, id,
                        "Only one root clock is allowed");
        }
        parent_index = find_index(clocks_, parent);
        if (parent_index == static_cast<std::size_t>(-1)) {
            return fail(TimeErrorCode::missing_parent, id,
                        "Clock parent is not registered");
        }
    }

    Rate normalized;
    if (const auto error = normalize_rate(relative, normalized, id)) return error;
    Rate effective = normalized;
    if (parent_index != Clock::no_parent) {
        if (!multiply_rate(clocks_[parent_index].effective_rate(), normalized, effective)) {
            return fail(TimeErrorCode::arithmetic_overflow, id,
                        "Effective rate exceeds the representable range");
        }
    }

    Clock clock(std::move(id), std::move(parent));
    clock.parent_index_ = parent_index;
    clock.relative_rate_ = normalized;
    clock.effective_rate_ = effective;
    clock.paused_ = paused;
    clocks_.push_back(std::move(clock));
    return std::nullopt;
}

const Clock* ClockTree::find(const std::string& id) const noexcept {
    const std::size_t index = find_index(clocks_, id);
    return index == static_cast<std::size_t>(-1) ? nullptr : &clocks_[index];
}

bool ClockTree::effectively_paused(const std::string& id) const noexcept {
    std::size_t index = find_index(clocks_, id);
    if (index == static_cast<std::size_t>(-1)) return false;
    while (true) {
        if (clocks_[index].paused()) return true;
        if (clocks_[index].parent_index_ == Clock::no_parent) return false;
        index = clocks_[index].parent_index_;
    }
}

std::optional<TimeError> ClockTree::rebuild_effective_rates(const std::string& changed, Rate rate) {
    std::vector<Rate> effective;
    effective.reserve(clocks_.size());
    for (const auto& clock : clocks_) {
        const Rate relative = clock.id() == changed ? rate : clock.relative_rate();
        if (clock.parent_index_ == Clock::no_parent) {
            effective.push_back(relative);
            continue;
        }
        Rate product;
        if (!multiply_rate(effective[clock.parent_index_], relative, product)) {
            return fail(TimeErrorCode::arithmetic_overflow, changed,
                        "Effective rate exceeds the representable range");
        }
        effective.push_back(product);
    }
    for (std::size_t index = 0; index < clocks_.size(); ++index) {
        if (clocks_[index].id() == changed) clocks_[index].relative_rate_ = rate;
        clocks_[index].effective_rate_ = effective[index];
    }
    return std::nullopt;
}

std::optional<TimeError> ClockTree::set_relative_rate(const std::string& id, Rate rate) {
    if (find_index(clocks_, id) == static_cast<std::size_t>(-1)) {
        return fail(TimeErrorCode::unknown_clock, id, "Clock is not registered");
    }
    Rate normalized;
    if (const auto error = normalize_rate(rate, normalized, id)) return error;
    return rebuild_effective_rates(id, normalized);
}

bool ClockTree::set_paused(const std::string& id, bool paused) noexcept {
    const std::size_t index = find_index(clocks_, id);
    if (index == static_cast<std::size_t>(-1)) return false;
    clocks_[index].paused_ = paused;
    return true;
}

AdvanceResult ClockTree::advance(const std::string& id) noexcept {
    const std::size_t index = find_index(clocks_, id);
    if (index == static_cast<std::size_t>(-1)) return AdvanceResult::missing;
    if (effectively_paused(id)) return AdvanceResult::paused;

    Clock& clock = clocks_[index];
    constexpr TimeTick largest_time = std::numeric_limits<TimeTick>::max();
    constexpr std::int64_t largest_frame = std::numeric_limits<std::int64_t>::max();
    if (clock.local_time_ > largest_time - base_.step_microseconds) return AdvanceResult::overflow;
    if (clock.frame_ == largest_frame) return AdvanceResult::overflow;
    clock.local_time_ += base_.step_microseconds;
    ++clock.frame_;
    return AdvanceResult::advanced;
}

ClockTreeSnapshot ClockTree::snapshot() const {
    ClockTreeSnapshot result;
    result.step_microseconds = base_.step_microseconds;
    result.clocks.reserve(clocks_.size());
    for (const auto& clock : clocks_) {
        result.clocks.push_back(ClockRecord{clock.id(), clock.parent(), clock.local_time(),
                                           clock.frame(), clock.relative_rate(), clock.paused()});
    }
    return result;
}

std::optional<TimeError> ClockTree::restore(const ClockTreeSnapshot& snapshot) {
    if (snapshot.step_microseconds != base_.step_microseconds) {
        const std::string id = snapshot.clocks.empty() ? std::string{} : snapshot.clocks.front().id;
        return fail(TimeErrorCode::step_mismatch, id,
                    "Snapshot time step does not match the clock tree base");
    }
    ClockTree rebuilt(base_);
    for (const auto& record : snapshot.clocks) {
        if (record.local_time < 0 || record.frame < 0) {
            return fail(TimeErrorCode::invalid_snapshot, record.id,
                        "Clock local time and frame must be non-negative");
        }
        if (const auto error = rebuilt.add(record.id, record.parent, record.relative_rate,
                                           record.paused)) {
            return error;
        }
        // add 只追加到末尾，因此刚加入的条目就是最后一个。
        rebuilt.clocks_.back().local_time_ = record.local_time;
        rebuilt.clocks_.back().frame_ = record.frame;
    }
    *this = std::move(rebuilt);
    return std::nullopt;
}

}  // namespace olam
