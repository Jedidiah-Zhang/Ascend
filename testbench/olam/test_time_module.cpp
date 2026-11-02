#include <ascend/assembly.hpp>
#include <ascend/engine.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>
#include <olam/module/time_module.hpp>

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

Config clock_config(Integer numerator = 1, Integer denominator = 1, Integer paused = 0) {
    return Config::object({{"rate_numerator", Config::integer(numerator)},
                           {"rate_denominator", Config::integer(denominator)},
                           {"paused", Config::integer(paused)}});
}

Config clock_state(std::string id = "clock", Integer local_time = 0, Integer frame = 0,
                   Integer numerator = 1, Integer denominator = 1, Integer paused = 0) {
    return Config::object({{"id", Config::string(std::move(id))},
                           {"local_time", Config::integer(local_time)},
                           {"frame", Config::integer(frame)},
                           {"rate_numerator", Config::integer(numerator)},
                           {"rate_denominator", Config::integer(denominator)},
                           {"paused", Config::integer(paused)}});
}

Integer read_integer(const Engine& engine, const char* symbol) {
    return std::any_cast<Integer>(engine.read({"clock", symbol}));
}

void advance(const Engine& engine) { engine.call({"clock", "advance"}, {}); }

void expect_execution_error(const std::function<void()>& action, int line) {
    bool failed = false;
    try {
        action();
    } catch (const EngineError& error) {
        failed = error.diagnostic().code == ErrorCode::execution_failed;
    }
    require(failed, line);
}
#define EXPECT_EXECUTION_ERROR(...) expect_execution_error([&] { __VA_ARGS__; }, __LINE__)

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
#ifdef OLAM_TIME_I18N
    std::ifstream stream(std::filesystem::u8path(OLAM_TIME_I18N), std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read time i18n resource");
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
#else
    return {};
#endif
}

void values() {
    Engine engine;
    engine.add(olam::module::make_time_module("clock", clock_config()));
    engine.seal();
    CHECK(read_integer(engine, "local_microseconds") == 0);
    CHECK(read_integer(engine, "frame") == 0);
    CHECK(read_integer(engine, "rate_numerator") == 1);
    CHECK(read_integer(engine, "rate_denominator") == 1);
    CHECK(read_integer(engine, "effective_numerator") == 1);
    CHECK(read_integer(engine, "effective_denominator") == 1);
    CHECK(read_integer(engine, "paused") == 0);

    advance(engine);
    CHECK(read_integer(engine, "local_microseconds") == 1000);
    CHECK(read_integer(engine, "frame") == 1);
    engine.call({"clock", "set_paused"}, {Integer{1}});
    CHECK(read_integer(engine, "paused") == 1);
    advance(engine);
    CHECK(read_integer(engine, "frame") == 1);
    engine.call({"clock", "set_paused"}, {Integer{0}});
    advance(engine);
    CHECK(read_integer(engine, "frame") == 2);

    // 倍率不影响宿主显式推进的本地步；零倍率时钟仍可显式推进。
    engine.call({"clock", "set_rate"}, {Integer{1}, Integer{2}});
    CHECK(read_integer(engine, "rate_numerator") == 1);
    CHECK(read_integer(engine, "rate_denominator") == 2);
    CHECK(read_integer(engine, "effective_numerator") == 1);
    CHECK(read_integer(engine, "effective_denominator") == 2);
    engine.call({"clock", "set_rate"}, {Integer{0}, Integer{1}});
    advance(engine);
    CHECK(read_integer(engine, "frame") == 3);
    CHECK(read_integer(engine, "local_microseconds") == 3000);

    EXPECT_EXECUTION_ERROR(engine.call({"clock", "set_paused"}, {Integer{2}}));
    EXPECT_EXECUTION_ERROR(engine.call({"clock", "set_rate"}, {Integer{-1}, Integer{1}}));
    EXPECT_EXECUTION_ERROR(engine.call({"clock", "set_rate"}, {Integer{1}, Integer{0}}));
}

void state() {
    Engine source;
    source.add(olam::module::make_time_module("clock", clock_config(3, 2, 0)));
    source.seal();
    advance(source);
    advance(source);
    source.call({"clock", "set_paused"}, {Integer{1}});
    const StateSnapshot snapshot = source.capture_state();
    CHECK(snapshot.modules.size() == 1);
    CHECK(snapshot.modules.front().path == "clock");
    CHECK(!snapshot.modules.front().stateless);
    CHECK(snapshot.modules.front().contract == olam::module::time_state_contract);

    Engine target;
    target.add(olam::module::make_time_module("clock", clock_config()));
    target.seal();
    target.restore_state(snapshot);
    CHECK(read_integer(target, "local_microseconds") == 2000);
    CHECK(read_integer(target, "frame") == 2);
    CHECK(read_integer(target, "rate_numerator") == 3);
    CHECK(read_integer(target, "rate_denominator") == 2);
    CHECK(read_integer(target, "paused") == 1);
    advance(target);
    CHECK(read_integer(target, "frame") == 2);  // 恢复后的暂停状态生效

    // 非对象、缺字段、身份不一致、负进度、非法倍率／暂停值与未知字段均拒绝。
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
    };
    expect_invalid(Config::integer(1));
    expect_invalid(Config::object({{"id", Config::string("clock")}}));
    expect_invalid(clock_state("other"));
    expect_invalid(clock_state("clock", -1));
    expect_invalid(clock_state("clock", 0, 0, -1, 1));
    expect_invalid(clock_state("clock", 0, 0, 1, 0));
    expect_invalid(clock_state("clock", 0, 0, 1, 1, 2));
    expect_invalid(Config::object({{"id", Config::string("clock")},
                                   {"local_time", Config::integer(0)},
                                   {"frame", Config::integer(0)},
                                   {"rate_numerator", Config::integer(1)},
                                   {"rate_denominator", Config::integer(1)},
                                   {"paused", Config::integer(0)},
                                   {"step_microseconds", Config::integer(1000)}}));
    CHECK(read_integer(target, "frame") == 2);  // 失败不改运行状态
}

void config_errors() {
    EXPECT_CONFIG_ERROR(olam::module::make_time_module("clock", Config::integer(1)));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module("clock", clock_config(-1, 1)));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module("clock", clock_config(1, 0)));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module("clock", clock_config(1, 1, 2)));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module(
        "clock", Config::object({{"unknown", Config::integer(1)}})));
    // 步长不是实例配置字段，不能成为覆盖世界基础的入口。
    EXPECT_CONFIG_ERROR(olam::module::make_time_module(
        "clock", Config::object({{"step_microseconds", Config::integer(1000)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module(
        "clock", Config::object({{"rate_numerator", Config::string("1")}})));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module(
        "clock", Config::object({{"id", Config::integer(1)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_time_module(
        "clock", Config::object({{"id", Config::string("")}})));
}

void package() {
    ModuleLibrary library;
    olam::module::register_time_module(library);
    const auto resources = std::vector<ModuleResource>{
        {olam::module::time_text_domain, "zh-CN", read_resource()}};
    const auto bytes = olam::module::time_package_bytes(resources);

    CHECK(library.load(bytes) == olam::module::time_definition);
    const auto entries = library.entries();
    CHECK(entries.size() == 1);
    const auto& entry = entries.front();
    CHECK(entry.definition == olam::module::time_definition);
    CHECK(entry.version == olam::module::time_version);
    CHECK(entry.implementation == olam::module::time_implementation);
    CHECK(!entry.stateless);
    CHECK(entry.state_contract == olam::module::time_state_contract);
    CHECK(entry.declarations == 10);
    CHECK(entry.requirements == 0);
    CHECK(entry.resources == 1);

    const auto manifest = library.manifest(olam::module::time_definition);
    CHECK(manifest.state_contract == olam::module::time_state_contract);
    CHECK(library.resources(olam::module::time_definition).size() == 1);

    Engine engine;
    engine.add(library.factories().create(olam::module::time_definition, "clock", Config{}));
    engine.seal();
    CHECK(read_integer(engine, "local_microseconds") == 0);
    CHECK(read_integer(engine, "frame") == 0);

    CHECK(library.unload(olam::module::time_definition));
    CHECK(library.load(bytes) == olam::module::time_definition);

    const auto decoded = decode_module_package(bytes);
    CHECK(decoded.manifest.definition == olam::module::time_definition);
    CHECK(decoded.manifest.version == olam::module::time_version);
    CHECK(decoded.manifest.implementation == olam::module::time_implementation);
    CHECK(decoded.manifest.state_contract == olam::module::time_state_contract);
    CHECK(decoded.resources.size() == 1);
}

void package_file(const std::string& path) {
    std::ifstream stream(std::filesystem::u8path(path), std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read generated module package");
    const std::string bytes((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
    ModuleLibrary library;
    olam::module::register_time_module(library);
    CHECK(library.load(bytes) == olam::module::time_definition);
    CHECK(library.manifest(olam::module::time_definition).state_contract ==
          olam::module::time_state_contract);
    CHECK(library.resources(olam::module::time_definition).size() == 1);

    Engine engine;
    engine.add(library.factories().create(olam::module::time_definition, "clock", Config{}));
    engine.seal();
    CHECK(read_integer(engine, "frame") == 0);
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"values", values}, {"state", state}, {"config_errors", config_errors},
        {"package", package},
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
