#include <olam/space.hpp>

#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>

namespace {
using namespace olam;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

// 查询结果先判空，回归时给出可读断言而不是直接解引用空指针。
const Space& found_space(const Space* space, int line) {
    require(space != nullptr, line);
    return *space;
}
#define FOUND(pointer) found_space((pointer), __LINE__)

SpaceDescription description(std::string id, Layer layer, Tick x, Tick y, Tick width, Tick height) {
    return {SpaceId{std::move(id)}, layer, AbsolutePosition{x, y}, Size{width, height}};
}

SpaceErrorCode add_fails(SpaceSet& set, SpaceDescription value) {
    const auto error = set.add(std::move(value));
    CHECK(error.has_value());
    CHECK(!error->message.empty());
    return error->code;
}

void create() {
    SpaceSet set;
    CHECK(set.empty());
    CHECK(!set.add(description("field", 0, 0, 0, 1000, 2000)));
    CHECK(set.size() == 1);
    const Space& space = FOUND(set.find({"field"}));
    CHECK(space.id() == SpaceId{"field"});
    CHECK(space.layer() == 0);
    CHECK(space.origin() == AbsolutePosition{0, 0});
    CHECK(space.size() == Size{1000, 2000});
    CHECK(space.upper_bound() == AbsolutePosition{1000, 2000});
    CHECK(set.find({"missing"}) == nullptr);

    CHECK(add_fails(set, description("", 1, 0, 0, 10, 10)) == SpaceErrorCode::invalid_identifier);
    CHECK(add_fails(set, description("zero-width", 1, 0, 0, 0, 10)) == SpaceErrorCode::invalid_size);
    CHECK(add_fails(set, description("zero-height", 1, 0, 0, 10, 0)) == SpaceErrorCode::invalid_size);
    CHECK(add_fails(set, description("negative-width", 1, 0, 0, -1, 10)) == SpaceErrorCode::invalid_size);
    CHECK(add_fails(set, description("negative-height", 1, 0, 0, 10, -1)) == SpaceErrorCode::invalid_size);
    CHECK(add_fails(set, description("negative-origin-x", 1, -1, 0, 10, 10)) == SpaceErrorCode::negative_origin);
    CHECK(add_fails(set, description("negative-origin-y", 1, 0, -1, 10, 10)) == SpaceErrorCode::negative_origin);
    constexpr Tick largest = std::numeric_limits<Tick>::max();
    CHECK(add_fails(set, description("overflow-x", 1, largest - 5, 0, 10, 10)) == SpaceErrorCode::arithmetic_overflow);
    CHECK(add_fails(set, description("overflow-y", 1, 0, largest - 5, 10, 10)) == SpaceErrorCode::arithmetic_overflow);
    CHECK(add_fails(set, description("field", 1, 0, 0, 10, 10)) == SpaceErrorCode::duplicate_identifier);
    // 失败的登记不改变集合。
    CHECK(set.size() == 1);
    CHECK(set.find({"overflow-x"}) == nullptr);
}

void overlap() {
    SpaceSet set;
    CHECK(!set.add(description("base", 3, 100, 100, 1000, 1000)));
    CHECK(add_fails(set, description("inside", 3, 200, 200, 100, 100)) == SpaceErrorCode::same_layer_overlap);
    CHECK(add_fails(set, description("identical", 3, 100, 100, 1000, 1000)) == SpaceErrorCode::same_layer_overlap);
    CHECK(add_fails(set, description("containing", 3, 0, 0, 2000, 2000)) == SpaceErrorCode::same_layer_overlap);
    CHECK(add_fails(set, description("one-tick-x", 3, 1099, 100, 100, 1000)) == SpaceErrorCode::same_layer_overlap);
    CHECK(add_fails(set, description("one-tick-y", 3, 100, 1099, 1000, 100)) == SpaceErrorCode::same_layer_overlap);
    // 边缘相接没有共同内部位置，允许登记。
    CHECK(!set.add(description("right", 3, 1100, 100, 100, 1000)));
    CHECK(!set.add(description("above", 3, 100, 1100, 1000, 100)));
    CHECK(!set.add(description("corner", 3, 1100, 1100, 10, 10)));
    // 异层可以重叠。
    CHECK(!set.add(description("layer-four", 4, 100, 100, 1000, 1000)));
    CHECK(set.size() == 5);
}

void locate() {
    SpaceSet set;
    CHECK(!set.add(description("low", 1, 0, 0, 100, 100)));
    CHECK(!set.add(description("high", 2, 0, 0, 100, 100)));
    CHECK(!set.add(description("aside", 1, 200, 0, 100, 100)));
    CHECK(FOUND(set.locate(1, {0, 0})).id() == SpaceId{"low"});
    CHECK(FOUND(set.locate(1, {99, 99})).id() == SpaceId{"low"});
    // 上界不是合法内部位置，也不自动换到相邻空间。
    CHECK(set.locate(1, {100, 0}) == nullptr);
    CHECK(set.locate(1, {0, 100}) == nullptr);
    CHECK(set.locate(1, {150, 50}) == nullptr);
    CHECK(FOUND(set.locate(1, {200, 50})).id() == SpaceId{"aside"});
    // 层序区分归属，不跨层查找。
    CHECK(FOUND(set.locate(2, {50, 50})).id() == SpaceId{"high"});
    CHECK(set.locate(2, {150, 50}) == nullptr);
    CHECK(set.locate(3, {50, 50}) == nullptr);
    CHECK(set.locate(1, {-1, 0}) == nullptr);
}

void convert() {
    SpaceSet set;
    CHECK(!set.add(description("a", 0, 100, 200, 50, 40)));
    CHECK(!set.add(description("b", 0, 150, 200, 50, 40)));
    const Space& a = FOUND(set.find({"a"}));
    CHECK(a.to_local({100, 200}) == LocalPosition{0, 0});
    CHECK(a.to_local({149, 239}) == LocalPosition{49, 39});
    CHECK(a.to_absolute({0, 0}) == AbsolutePosition{100, 200});
    CHECK(a.to_absolute({49, 39}) == AbsolutePosition{149, 239});
    // 上界与越界输入被拒绝，不夹取、不换到 b。
    CHECK(!a.to_local({150, 200}));
    CHECK(!a.to_local({99, 200}));
    CHECK(!a.to_local({100, 240}));
    CHECK(!a.to_absolute({50, 0}));
    CHECK(!a.to_absolute({0, 40}));
    CHECK(!a.to_absolute({-1, 0}));
    // 局部与绝对位置往返一致。
    const auto local = a.to_local({123, 234});
    CHECK(local.has_value());
    CHECK(a.to_absolute(*local) == AbsolutePosition{123, 234});
    // 边缘相接处，边界点属于 b。
    const Space& b = FOUND(set.find({"b"}));
    CHECK(b.to_local({150, 200}) == LocalPosition{0, 0});
    CHECK(FOUND(set.locate(0, {150, 200})).id() == SpaceId{"b"});
}

void bounds() {
    SpaceSet set;
    CHECK(!set.add(description("dot", 0, 5, 5, 1, 1)));
    const Space& dot = FOUND(set.find({"dot"}));
    CHECK(dot.contains({5, 5}));
    CHECK(!dot.contains({6, 5}));
    CHECK(!dot.contains({5, 6}));
    CHECK(!dot.contains({4, 5}));
    CHECK(dot.contains_local({0, 0}));
    CHECK(!dot.contains_local({1, 0}));
    CHECK(!dot.contains_local({0, 1}));
    CHECK(!dot.contains_local({-1, 0}));
    CHECK(dot.upper_bound() == AbsolutePosition{6, 6});

    // 上界紧邻最后一个合法刻度，仍不构成溢出。
    constexpr Tick largest = std::numeric_limits<Tick>::max();
    CHECK(!set.add(description("edge", 0, 0, 0, largest, 1)));
    const Space& edge = FOUND(set.find({"edge"}));
    CHECK(edge.contains({largest - 1, 0}));
    CHECK(!edge.contains({largest, 0}));
    CHECK(edge.upper_bound() == AbsolutePosition{largest, 1});
}

void readonly() {
    SpaceSet set;
    CHECK(!set.add(description("one", 0, 0, 0, 10, 10)));
    CHECK(!set.add(description("two", 1, 20, 20, 10, 10)));
    const auto snapshot = [&set] {
        std::string result;
        for (const auto& space : set.spaces()) result += space.id().value + ";";
        return result;
    };
    const std::string before = snapshot();
    // 查询与失败的登记都不改变集合内容与登记顺序。
    CHECK(set.locate(0, {5, 5}) != nullptr);
    CHECK(!FOUND(set.find({"one"})).to_local({10, 10}));
    CHECK(add_fails(set, description("three", 0, 0, 0, 10, 10)) == SpaceErrorCode::same_layer_overlap);
    CHECK(set.locate(0, {5, 5}) != nullptr);
    CHECK(snapshot() == before);
    CHECK(set.spaces().front().id() == SpaceId{"one"});
    CHECK(set.spaces().back().id() == SpaceId{"two"});
    CHECK(set.size() == 2);
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"create", create}, {"overlap", overlap}, {"locate", locate},
        {"convert", convert}, {"bounds", bounds}, {"readonly", readonly},
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
