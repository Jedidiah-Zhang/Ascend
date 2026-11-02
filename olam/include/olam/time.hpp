#pragma once

#include <cstddef>
#include <cstdint>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace olam {

// 首版时间精度暂定为整数微秒；精度与步长定值仍按设计复核。
using TimeTick = std::int64_t;
inline constexpr TimeTick time_ticks_per_second = 1'000'000;

// 世界基础：所有本地步共用的固定步长（微秒）。步长必须在创建 ClockTree 时给出。
struct TimeBase {
    TimeTick step_microseconds = 0;

    bool operator==(const TimeBase& other) const noexcept {
        return step_microseconds == other.step_microseconds;
    }
    bool operator!=(const TimeBase& other) const noexcept { return !(*this == other); }
};

// 相对倍率：分子不小于零、分母为正；保存时按最大公约数规范化。
struct Rate {
    std::int64_t numerator = 1;
    std::int64_t denominator = 1;

    bool operator==(const Rate& other) const noexcept {
        return numerator == other.numerator && denominator == other.denominator;
    }
    bool operator!=(const Rate& other) const noexcept { return !(*this == other); }
};

enum class TimeErrorCode {
    invalid_identifier,
    duplicate_identifier,
    missing_parent,
    duplicate_root,
    unknown_clock,
    invalid_rate,
    arithmetic_overflow,
    step_mismatch,
    invalid_snapshot,
};

struct TimeError {
    TimeErrorCode code = TimeErrorCode::invalid_snapshot;
    std::string clock;
    std::string message;
};

// 显式推进一个本地步的结果；暂停、未知时钟与算术溢出都不改变时钟状态。
enum class AdvanceResult {
    advanced,
    paused,
    missing,
    overflow,
};

// 创建后固定的时钟域：身份、父域与相对倍率在创建时确定；本地时间从零开始。
class Clock {
public:
    const std::string& id() const noexcept { return id_; }
    // 根时钟为空字符串。
    const std::string& parent() const noexcept { return parent_; }
    TimeTick local_time() const noexcept { return local_time_; }
    std::int64_t frame() const noexcept { return frame_; }
    const Rate& relative_rate() const noexcept { return relative_rate_; }
    // 相对根时钟的组合倍率；根时钟等于自身相对倍率。
    const Rate& effective_rate() const noexcept { return effective_rate_; }
    bool paused() const noexcept { return paused_; }

private:
    friend class ClockTree;
    static constexpr std::size_t no_parent = static_cast<std::size_t>(-1);

    explicit Clock(std::string id, std::string parent) noexcept
        : id_(std::move(id)), parent_(std::move(parent)) {}

    std::string id_;
    std::string parent_;
    TimeTick local_time_ = 0;
    std::int64_t frame_ = 0;
    Rate relative_rate_;
    Rate effective_rate_;
    bool paused_ = false;
    std::size_t parent_index_ = no_parent;
};

// 保存时钟树的完整运行状态；条目按父先于子的顺序保存。
struct ClockRecord {
    std::string id;
    std::string parent;
    TimeTick local_time = 0;
    std::int64_t frame = 0;
    Rate relative_rate;
    bool paused = false;
};

struct ClockTreeSnapshot {
    TimeTick step_microseconds = 0;
    std::vector<ClockRecord> clocks;
};

// 单根时钟树：根时钟协调关系，子时钟保留独立本地时间、帧与相对倍率。
// 父关系创建后不可变；推进一步只改变目标时钟，不自动推进祖先或后代。
class ClockTree {
public:
    // 步长不大于零时抛出 std::invalid_argument。
    explicit ClockTree(TimeBase base);

    // 加入时钟域；父域为空时作为根，树中只允许一个根。首项加入时允许父域为空。
    std::optional<TimeError> add(std::string id, std::string parent = {}, Rate relative = {},
                                 bool paused = false);

    // 返回的指针在下次 add 或 restore 前有效。
    const Clock* find(const std::string& id) const noexcept;
    // 自身或任一祖先暂停时返回 true。
    bool effectively_paused(const std::string& id) const noexcept;

    // 修改相对倍率并重算整棵树的组合倍率；失败不改变任何时钟。
    std::optional<TimeError> set_relative_rate(const std::string& id, Rate rate);
    // 暂停或恢复单个时钟；未找到时返回 false。
    bool set_paused(const std::string& id, bool paused) noexcept;

    // 由宿主显式推进一个本地步。暂停（含祖先暂停）、未知时钟或溢出时不改变状态。
    // 流速只影响调度节奏，不阻止显式本地步推进。
    AdvanceResult advance(const std::string& id) noexcept;

    const TimeBase& base() const noexcept { return base_; }
    // 登记顺序的只读列表；父时钟先于子时钟。
    const std::vector<Clock>& clocks() const noexcept { return clocks_; }
    std::size_t size() const noexcept { return clocks_.size(); }
    bool empty() const noexcept { return clocks_.empty(); }

    ClockTreeSnapshot snapshot() const;
    // 按快照重建时钟树；步长必须与当前基础一致，失败不改变现有树。
    std::optional<TimeError> restore(const ClockTreeSnapshot& snapshot);

private:
    std::optional<TimeError> rebuild_effective_rates(const std::string& changed, Rate rate);
    std::optional<TimeError> fail(TimeErrorCode code, const std::string& id, std::string message) const;

    TimeBase base_;
    std::vector<Clock> clocks_;
};

}  // namespace olam
