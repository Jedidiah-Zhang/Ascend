#include <olam/time.hpp>

#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>

namespace {

using namespace olam;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

ClockTree tree(TimeTick step = 1000) { return ClockTree(TimeBase{step}); }

TimeErrorCode add_fails(ClockTree& clocks, std::string id, std::string parent = {},
                        Rate rate = {}) {
    const auto error = clocks.add(std::move(id), std::move(parent), rate);
    CHECK(error.has_value());
    CHECK(!error->message.empty());
    return error->code;
}

void create() {
    for (const TimeTick step : {TimeTick{0}, TimeTick{-1}}) {
        bool invalid_step = false;
        try {
            ClockTree invalid(TimeBase{step});
        } catch (const std::invalid_argument&) {
            invalid_step = true;
        }
        CHECK(invalid_step);
    }

    auto clocks = tree();
    CHECK(clocks.empty());
    CHECK(clocks.base() == TimeBase{1000});
    CHECK(!clocks.add("root"));
    CHECK(clocks.size() == 1);
    const Clock& root = *clocks.find("root");
    CHECK(root.id() == "root");
    CHECK(root.parent().empty());
    CHECK(root.local_time() == 0);
    CHECK(root.frame() == 0);
    CHECK(root.relative_rate() == Rate{1, 1});
    CHECK(root.effective_rate() == Rate{1, 1});
    CHECK(!root.paused());

    CHECK(add_fails(clocks, "") == TimeErrorCode::invalid_identifier);
    CHECK(add_fails(clocks, "root") == TimeErrorCode::duplicate_identifier);
    CHECK(add_fails(clocks, "second-root") == TimeErrorCode::duplicate_root);
    CHECK(add_fails(clocks, "orphan", "missing") == TimeErrorCode::missing_parent);
    CHECK(!clocks.add("child", "root", Rate{2, 4}));
    const Clock& child = *clocks.find("child");
    CHECK(child.relative_rate() == Rate{1, 2});
    CHECK(child.effective_rate() == Rate{1, 2});
    CHECK(!clocks.add("grandchild", "child", Rate{3, 2}));
    CHECK(clocks.find("grandchild")->effective_rate() == Rate{3, 4});
    CHECK(!clocks.add("zero", "root", Rate{0, 5}));
    CHECK(clocks.find("zero")->effective_rate() == Rate{0, 1});
    CHECK(clocks.find("missing") == nullptr);
    CHECK(clocks.effectively_paused("root") == false);

    CHECK(add_fails(clocks, "negative", "root", Rate{-1, 1}) == TimeErrorCode::invalid_rate);
    CHECK(add_fails(clocks, "zero-denominator", "root", Rate{1, 0}) == TimeErrorCode::invalid_rate);
}

void advance() {
    auto clocks = tree();
    CHECK(!clocks.add("root"));
    CHECK(!clocks.add("child", "root"));
    CHECK(clocks.advance("root") == AdvanceResult::advanced);
    CHECK(clocks.find("root")->local_time() == 1000);
    CHECK(clocks.find("root")->frame() == 1);
    CHECK(clocks.find("child")->local_time() == 0);
    CHECK(clocks.advance("child") == AdvanceResult::advanced);
    CHECK(clocks.find("child")->local_time() == 1000);
    CHECK(clocks.advance("missing") == AdvanceResult::missing);

    // 祖先暂停阻止后代推进；父时钟自身也停止。
    CHECK(clocks.set_paused("root", true));
    CHECK(clocks.effectively_paused("root"));
    CHECK(clocks.effectively_paused("child"));
    CHECK(clocks.advance("root") == AdvanceResult::paused);
    CHECK(clocks.advance("child") == AdvanceResult::paused);
    CHECK(clocks.find("root")->frame() == 1);
    CHECK(clocks.find("child")->frame() == 1);
    CHECK(clocks.set_paused("root", false));
    CHECK(clocks.set_paused("child", true));
    CHECK(clocks.advance("root") == AdvanceResult::advanced);
    CHECK(clocks.advance("child") == AdvanceResult::paused);
    CHECK(clocks.find("root")->frame() == 2);
    CHECK(clocks.find("child")->frame() == 1);
    CHECK(!clocks.set_paused("missing", true));

    // 流速只影响调度节奏，显式本地步仍由宿主推进。
    CHECK(!clocks.set_relative_rate("root", Rate{0, 1}).has_value());
    CHECK(clocks.advance("root") == AdvanceResult::advanced);
    CHECK(clocks.find("root")->frame() == 3);
}

void rates() {
    auto clocks = tree();
    CHECK(!clocks.add("root", {}, Rate{1, 2}));
    CHECK(!clocks.add("child", "root", Rate{2, 3}));
    CHECK(clocks.find("root")->effective_rate() == Rate{1, 2});
    CHECK(clocks.find("child")->effective_rate() == Rate{1, 3});

    CHECK(!clocks.set_relative_rate("root", Rate{2, 1}).has_value());
    CHECK(clocks.find("root")->effective_rate() == Rate{2, 1});
    CHECK(clocks.find("child")->effective_rate() == Rate{4, 3});

    const auto before = clocks.find("child")->effective_rate();
    CHECK(clocks.set_relative_rate("child", Rate{-1, 2})->code == TimeErrorCode::invalid_rate);
    CHECK(clocks.set_relative_rate("child", Rate{1, 0})->code == TimeErrorCode::invalid_rate);
    CHECK(clocks.find("child")->effective_rate() == before);
    CHECK(clocks.set_relative_rate("missing", Rate{1, 1})->code == TimeErrorCode::unknown_clock);

    // 组合倍率溢出时拒绝修改，已有树保持不变。
    auto overflow = tree();
    CHECK(!overflow.add("root", {}, Rate{std::numeric_limits<std::int64_t>::max(), 1}));
    CHECK(add_fails(overflow, "child", "root", Rate{2, 1}) == TimeErrorCode::arithmetic_overflow);
    CHECK(overflow.size() == 1);

    auto propagated = tree();
    CHECK(!propagated.add("root"));
    CHECK(!propagated.add("child", "root", Rate{std::numeric_limits<std::int64_t>::max(), 1}));
    CHECK(propagated.set_relative_rate("root", Rate{2, 1})->code ==
          TimeErrorCode::arithmetic_overflow);
    CHECK(propagated.find("child")->effective_rate() ==
          Rate{std::numeric_limits<std::int64_t>::max(), 1});
}

void state() {
    auto clocks = tree(250);
    CHECK(!clocks.add("root"));
    CHECK(!clocks.add("child", "root", Rate{3, 2}));
    CHECK(clocks.advance("root") == AdvanceResult::advanced);
    CHECK(clocks.advance("child") == AdvanceResult::advanced);
    CHECK(clocks.advance("child") == AdvanceResult::advanced);
    CHECK(clocks.set_paused("root", true));

    const auto snapshot = clocks.snapshot();
    CHECK(snapshot.step_microseconds == 250);
    CHECK(snapshot.clocks.size() == 2);
    CHECK(snapshot.clocks[1].parent == "root");
    CHECK(snapshot.clocks[1].local_time == 500);
    CHECK(snapshot.clocks[1].frame == 2);

    auto restored = tree(250);
    CHECK(!restored.restore(snapshot).has_value());
    CHECK(restored.find("root")->paused());
    CHECK(restored.find("child")->local_time() == 500);
    CHECK(restored.find("child")->frame() == 2);
    CHECK(restored.find("child")->effective_rate() == Rate{3, 2});
    CHECK(restored.advance("child") == AdvanceResult::paused);
    CHECK(restored.set_paused("root", false));
    CHECK(restored.advance("child") == AdvanceResult::advanced);
    CHECK(restored.find("child")->local_time() == 750);

    // 步长不一致、负进度、缺失父域和非法倍率均拒绝，目标树保持原状。
    auto mismatched = snapshot;
    mismatched.step_microseconds = 500;
    CHECK(restored.restore(mismatched)->code == TimeErrorCode::step_mismatch);
    CHECK(restored.size() == 2);

    auto negative = snapshot;
    negative.clocks[1].local_time = -1;
    CHECK(restored.restore(negative)->code == TimeErrorCode::invalid_snapshot);
    CHECK(restored.find("child")->local_time() == 750);

    auto missing_parent = snapshot;
    missing_parent.clocks[1].parent = "missing";
    CHECK(restored.restore(missing_parent)->code == TimeErrorCode::missing_parent);
    CHECK(restored.size() == 2);

    auto invalid_rate = snapshot;
    invalid_rate.clocks[1].relative_rate = Rate{1, 0};
    CHECK(restored.restore(invalid_rate)->code == TimeErrorCode::invalid_rate);
    CHECK(restored.find("child")->effective_rate() == Rate{3, 2});
}

void overflow() {
    constexpr TimeTick largest = std::numeric_limits<TimeTick>::max();
    auto clocks = tree(1000);
    CHECK(!clocks.add("root"));
    ClockTreeSnapshot snapshot;
    snapshot.step_microseconds = 1000;
    snapshot.clocks.push_back(ClockRecord{"root", "", largest - 999, 0, Rate{}, false});
    CHECK(!clocks.restore(snapshot).has_value());
    CHECK(clocks.advance("root") == AdvanceResult::overflow);
    CHECK(clocks.find("root")->local_time() == largest - 999);
    CHECK(clocks.find("root")->frame() == 0);

    snapshot.clocks.front().local_time = 0;
    snapshot.clocks.front().frame = std::numeric_limits<std::int64_t>::max();
    CHECK(!clocks.restore(snapshot).has_value());
    CHECK(clocks.advance("root") == AdvanceResult::overflow);
    CHECK(clocks.find("root")->frame() == std::numeric_limits<std::int64_t>::max());
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"create", create}, {"advance", advance}, {"rates", rates},
        {"state", state},   {"overflow", overflow},
    };
    if (argc != 2 || tests.count(argv[1]) == 0) return 2;
    try {
        tests.at(argv[1])();
        std::cout << argv[1] << ": passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << argv[1] << ": " << error.what() << '\n';
        return 1;
    }
}
