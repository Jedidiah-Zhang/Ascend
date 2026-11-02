#include <ascend/assembly.hpp>
#include <ascend/engine.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>
#include <olam/module/space_module.hpp>

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using Integer = std::int64_t;
using namespace ascend;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

Config single_space(std::string id = {}, Integer origin_x = 100, Integer origin_y = 200,
                    Integer width = 1000, Integer height = 500, Integer layer = 3) {
    std::vector<std::pair<std::string, Config>> fields = {
        {"origin_x", Config::integer(origin_x)},
        {"origin_y", Config::integer(origin_y)},
        {"width", Config::integer(width)},
        {"height", Config::integer(height)},
        {"layer", Config::integer(layer)},
    };
    if (!id.empty()) fields.emplace_back("id", Config::string(std::move(id)));
    return Config::object(std::move(fields));
}

Config multi_space() {
    return Config::object(
        {{"spaces", Config::array({single_space("low", 0, 0, 100, 100, 1),
                                   single_space("high", 0, 0, 100, 100, 2),
                                   single_space("aside", 200, 0, 100, 100, 1)})}});
}

Integer read_integer(const Engine& engine, const char* symbol) {
    return std::any_cast<Integer>(engine.read({"yard", symbol}));
}

Integer call(const Engine& engine, const char* symbol, std::vector<std::any> arguments) {
    return std::any_cast<Integer>(engine.call({"yard", symbol}, arguments));
}

void expect_config_error(const std::function<void()>& action, int line) {
    bool failed = false;
    try {
        action();
    } catch (const EngineError& error) {
        failed = error.diagnostic().code == ErrorCode::invalid_config;
    }
    require(failed, line);
}
#define EXPECT_CONFIG_ERROR(...) expect_config_error([&] { __VA_ARGS__; }, __LINE__)

std::string read_resource() {
#ifdef OLAM_SPACE_I18N
    std::ifstream stream(std::filesystem::u8path(OLAM_SPACE_I18N), std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read space i18n resource");
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
#else
    return {};
#endif
}

void values() {
    Engine engine;
    engine.add(olam::module::make_space_module("yard", single_space()));
    engine.seal();
    CHECK(read_integer(engine, "space_count") == 1);
    CHECK(read_integer(engine, "layer_count") == 1);
    CHECK(read_integer(engine, "origin_x") == 100);
    CHECK(read_integer(engine, "origin_y") == 200);
    CHECK(read_integer(engine, "width") == 1000);
    CHECK(read_integer(engine, "height") == 500);
    CHECK(read_integer(engine, "layer") == 3);
    CHECK(read_integer(engine, "upper_x") == 1100);
    CHECK(read_integer(engine, "upper_y") == 700);

    // 空集合仍是合法状态：计数为零，首个空间相关值明确为 -1。
    Engine empty;
    empty.add(olam::module::make_space_module(
        "yard", Config::object({{"spaces", Config::array({})}})));
    empty.seal();
    CHECK(read_integer(empty, "space_count") == 0);
    CHECK(read_integer(empty, "layer_count") == 0);
    CHECK(read_integer(empty, "origin_x") == -1);
    CHECK(read_integer(empty, "width") == -1);
}

void queries() {
    Engine engine;
    engine.add(olam::module::make_space_module("yard", multi_space()));
    engine.seal();
    CHECK(read_integer(engine, "space_count") == 3);
    CHECK(read_integer(engine, "layer_count") == 2);

    CHECK(call(engine, "locate", {Integer{1}, Integer{0}, Integer{0}}) == 0);
    CHECK(call(engine, "locate", {Integer{1}, Integer{99}, Integer{99}}) == 0);
    CHECK(call(engine, "locate", {Integer{1}, Integer{100}, Integer{0}}) == -1);
    CHECK(call(engine, "locate", {Integer{2}, Integer{50}, Integer{50}}) == 1);
    CHECK(call(engine, "locate", {Integer{2}, Integer{150}, Integer{50}}) == -1);
    CHECK(call(engine, "locate", {Integer{3}, Integer{50}, Integer{50}}) == -1);
    CHECK(call(engine, "locate", {Integer{1}, Integer{250}, Integer{50}}) == 2);

    CHECK(call(engine, "contains", {Integer{1}, Integer{50}, Integer{50}}) == 1);
    CHECK(call(engine, "contains", {Integer{2}, Integer{150}, Integer{50}}) == 0);
    CHECK(call(engine, "local_x", {Integer{1}, Integer{250}, Integer{50}}) == 50);
    CHECK(call(engine, "local_y", {Integer{1}, Integer{250}, Integer{50}}) == 50);
    CHECK(call(engine, "local_x", {Integer{1}, Integer{150}, Integer{50}}) == -1);
    CHECK(call(engine, "local_y", {Integer{1}, Integer{250}, Integer{150}}) == -1);
}

void state() {
    // 状态契约保存空间集合；恢复到另一实例后查询结果一致。
    Engine source;
    source.add(olam::module::make_space_module("yard", multi_space()));
    source.seal();
    const StateSnapshot snapshot = source.capture_state();
    CHECK(snapshot.modules.size() == 1);
    CHECK(snapshot.modules.front().path == "yard");
    CHECK(!snapshot.modules.front().stateless);
    CHECK(snapshot.modules.front().contract == olam::module::space_state_contract);
    const auto* spaces = snapshot.modules.front().state.find("spaces");
    CHECK(spaces != nullptr);
    CHECK(spaces->kind() == Config::Kind::array);
    CHECK(spaces->elements().size() == 3);

    Engine target;
    target.add(olam::module::make_space_module("yard", single_space("other", 0, 0, 10, 10, 0)));
    target.seal();
    target.restore_state(snapshot);
    CHECK(read_integer(target, "space_count") == 3);
    CHECK(read_integer(target, "layer_count") == 2);
    CHECK(call(target, "locate", {Integer{1}, Integer{250}, Integer{50}}) == 2);

    // 恢复按契约校验结构；非法状态报告 invalid_state 并保留原集合。
    const auto expect_invalid = [&](Config state) {
        StateSnapshot broken = snapshot;
        broken.modules.front().state = std::move(state);
        bool failed = false;
        try {
            target.restore_state(broken);
        } catch (const EngineError& error) {
            failed = error.diagnostic().code == ErrorCode::invalid_state;
        }
        CHECK(failed);
        CHECK(read_integer(target, "space_count") == 3);
    };
    expect_invalid(Config::object({{"spaces", Config::integer(1)}}));
    expect_invalid(Config::object({{"spaces", Config::array({Config::object({})})}}));
    expect_invalid(Config::object(
        {{"spaces", Config::array({Config::object({{"id", Config::integer(1)},
                                                    {"layer", Config::integer(0)},
                                                    {"origin_x", Config::integer(0)},
                                                    {"origin_y", Config::integer(0)},
                                                    {"width", Config::integer(10)},
                                                    {"height", Config::integer(10)}})})}}));
    expect_invalid(Config::object({{"spaces", Config::array({single_space("same", 0, 0, 100, 100, 0),
                                                              single_space("same", 0, 0, 100, 100, 0)})}}));
    expect_invalid(Config::object({{"spaces", Config::array({single_space("two", 0, 0, 100, 100, 0),
                                                              single_space("three", 50, 50, 100, 100, 0)})}}));
    expect_invalid(Config::object({{"spaces", Config::array({single_space("negative", -1, 0, 100, 100, 0)})}}));
}

void config_errors() {
    EXPECT_CONFIG_ERROR(olam::module::make_space_module("yard", Config::integer(1)));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", Config::object({{"origin_x", Config::integer(0)}, {"unknown", Config::integer(1)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module("yard", single_space({}, 0, 0, 0, 10)));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", Config::object({{"spaces", Config::array(
                                     {single_space("same", 0, 0, 100, 100, 0),
                                      single_space("same", 0, 0, 100, 100, 0)})}})));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", Config::object({{"spaces", Config::array(
                                     {single_space("two", 0, 0, 100, 100, 0),
                                      single_space("three", 50, 50, 100, 100, 0)})}})));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", Config::object({{"layer", Config::integer(0)},
                                {"width", Config::integer(10)},
                                {"height", Config::integer(10)},
                                {"id", Config::integer(1)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", Config::object({{"width", Config::string("1000")},
                                {"height", Config::integer(10)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", Config::object({{"layer", Config::integer(2147483648)},
                                {"width", Config::integer(10)},
                                {"height", Config::integer(10)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module("yard", single_space({}, -1, 0, 10, 10)));
    EXPECT_CONFIG_ERROR(olam::module::make_space_module(
        "yard", single_space({}, 1, 0, std::numeric_limits<Integer>::max(), 10)));
}

void package() {
    ModuleLibrary library;
    olam::module::register_space_module(library);
    const auto resources = std::vector<ModuleResource>{
        {olam::module::space_text_domain, "zh-CN", read_resource()}};
    const auto bytes = olam::module::space_package_bytes(resources);

    CHECK(library.load(bytes) == olam::module::space_definition);
    const auto entries = library.entries();
    CHECK(entries.size() == 1);
    const auto& entry = entries.front();
    CHECK(entry.definition == olam::module::space_definition);
    CHECK(entry.version == olam::module::space_version);
    CHECK(entry.implementation == olam::module::space_implementation);
    CHECK(!entry.stateless);
    CHECK(entry.state_contract == olam::module::space_state_contract);
    CHECK(entry.declarations == 13);
    CHECK(entry.requirements == 0);
    CHECK(entry.resources == 1);

    // 包内清单与宿主空配置探测实例一致；工厂可直接实例化并读取默认空间。
    const auto manifest = library.manifest(olam::module::space_definition);
    CHECK(manifest.definition == olam::module::space_definition);
    CHECK(manifest.state_contract == olam::module::space_state_contract);
    CHECK(library.resources(olam::module::space_definition).size() == 1);

    Engine engine;
    engine.add(library.factories().create(olam::module::space_definition, "yard", Config{}));
    engine.seal();
    CHECK(read_integer(engine, "space_count") == 1);
    CHECK(read_integer(engine, "width") == 1000);

    CHECK(library.unload(olam::module::space_definition));
    CHECK(library.load(bytes) == olam::module::space_definition);

    // 包解码往返保留定义、版本、实现与状态契约。
    const auto decoded = decode_module_package(bytes);
    CHECK(decoded.manifest.definition == olam::module::space_definition);
    CHECK(decoded.manifest.version == olam::module::space_version);
    CHECK(decoded.manifest.implementation == olam::module::space_implementation);
    CHECK(decoded.manifest.state_contract == olam::module::space_state_contract);
    CHECK(decoded.resources.size() == 1);
}

void package_file(const std::string& path) {
    // 验证包工具的实际输出可由模块库载入，并保留清单与资源。
    std::ifstream stream(std::filesystem::u8path(path), std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read generated module package");
    const std::string bytes((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    ModuleLibrary library;
    olam::module::register_space_module(library);
    CHECK(library.load(bytes) == olam::module::space_definition);
    const auto manifest = library.manifest(olam::module::space_definition);
    CHECK(manifest.state_contract == olam::module::space_state_contract);
    CHECK(library.resources(olam::module::space_definition).size() == 1);

    Engine engine;
    engine.add(library.factories().create(olam::module::space_definition, "yard", Config{}));
    engine.seal();
    CHECK(read_integer(engine, "space_count") == 1);
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"values", values},         {"queries", queries}, {"state", state},
        {"config_errors", config_errors}, {"package", package},
    };
    if (argc == 3 && std::string(argv[1]) == "package_file") {
        try {
            package_file(argv[2]);
            std::cout << "package_file: passed\n";
            return 0;
        } catch (const std::exception& error) {
            std::cerr << "package_file: " << error.what() << '\n';
            return 1;
        }
    }
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
