#include <ascend/assembly.hpp>
#include <ascend/engine.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>
#include <olam/module/terrain_module.hpp>

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

Config terrain_config(std::string id = {}, Integer columns = 16, Integer rows = 8,
                      Integer origin_x = 100, Integer origin_y = 200, Integer layer = 3,
                      Integer seed = 42, Integer octaves = 3, Integer feature_cells = 4,
                      Integer base_elevation = -1000, Integer amplitude = 5000,
                      Config edits = Config::object({})) {
    std::vector<std::pair<std::string, Config>> fields = {
        {"origin_x", Config::integer(origin_x)},
        {"origin_y", Config::integer(origin_y)},
        {"layer", Config::integer(layer)},
        {"columns", Config::integer(columns)},
        {"rows", Config::integer(rows)},
        {"cell_ticks", Config::integer(1000)},
        {"seed", Config::integer(seed)},
        {"octaves", Config::integer(octaves)},
        {"feature_cells", Config::integer(feature_cells)},
        {"base_elevation", Config::integer(base_elevation)},
        {"amplitude", Config::integer(amplitude)},
        {"generator_version", Config::integer(1)},
        {"edits", std::move(edits)},
    };
    if (!id.empty()) fields.emplace_back("id", Config::string(std::move(id)));
    return Config::object(std::move(fields));
}

Config edit(Integer index, Integer elevation) {
    return Config::object(
        {{std::to_string(index), Config::object({{"elevation", Config::integer(elevation)}})}});
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

std::string config_error_text(const std::function<void()>& action) {
    try {
        action();
    } catch (const EngineError& error) {
        return render_diagnostic(error.diagnostic());
    }
    return {};
}

std::string read_resource() {
#ifdef OLAM_TERRAIN_I18N
    std::ifstream stream(std::filesystem::u8path(OLAM_TERRAIN_I18N), std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read terrain i18n resource");
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
#else
    return {};
#endif
}

void values() {
    Engine defaults;
    defaults.add(olam::module::make_terrain_module("yard", Config{}));
    defaults.seal();
    CHECK(read_integer(defaults, "columns") == 512);
    CHECK(read_integer(defaults, "rows") == 512);
    CHECK(read_integer(defaults, "cell_ticks") == 1000);
    CHECK(read_integer(defaults, "width") == 512000);
    CHECK(read_integer(defaults, "height") == 512000);
    CHECK(read_integer(defaults, "generator_version") == 1);
    CHECK(read_integer(defaults, "exact") == 1);
    CHECK(read_integer(defaults, "edit_count") == 0);
    CHECK(read_integer(defaults, "min_elevation") <= read_integer(defaults, "max_elevation"));
    CHECK(read_integer(defaults, "base_checksum") > 0);
    CHECK(read_integer(defaults, "elevation_sum_saturated") == 0);
    CHECK(read_integer(defaults, "checksum_limit_cells") == 0);

    // 空对象与 null 使用同一份预填默认配置。
    Engine empty_object;
    empty_object.add(olam::module::make_terrain_module("yard", Config::object({})));
    empty_object.seal();
    CHECK(read_integer(empty_object, "columns") == 512);
    CHECK(read_integer(empty_object, "rows") == 512);
    CHECK(read_integer(empty_object, "seed") == 0);
    CHECK(read_integer(empty_object, "base_checksum") == read_integer(defaults, "base_checksum"));

    Engine engine;
    engine.add(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               edit(5, 1234))));
    engine.seal();
    CHECK(read_integer(engine, "columns") == 16);
    CHECK(read_integer(engine, "rows") == 8);
    CHECK(read_integer(engine, "origin_x") == 100);
    CHECK(read_integer(engine, "origin_y") == 200);
    CHECK(read_integer(engine, "width") == 16000);
    CHECK(read_integer(engine, "height") == 8000);
    CHECK(read_integer(engine, "layer") == 3);
    CHECK(read_integer(engine, "seed") == 42);
    CHECK(read_integer(engine, "octaves") == 3);
    CHECK(read_integer(engine, "feature_cells") == 4);
    CHECK(read_integer(engine, "base_elevation") == -1000);
    CHECK(read_integer(engine, "amplitude") == 5000);
    CHECK(read_integer(engine, "base_checksum") > 0);
    CHECK(read_integer(engine, "edit_count") == 1);
    CHECK(read_integer(engine, "edit_elevation_sum") == 1234);
    CHECK(read_integer(engine, "edit_elevation_sum_saturated") == 0);
    CHECK(read_integer(engine, "elevation_sum_saturated") == 0);
    CHECK(read_integer(engine, "checksum_limit_cells") == 0);
    const Integer cells = 16 * 8;
    CHECK(read_integer(engine, "elevation_sum") >=
          read_integer(engine, "min_elevation") * cells);
    CHECK(read_integer(engine, "elevation_sum") <=
          read_integer(engine, "max_elevation") * cells);
    CHECK(read_integer(engine, "exact") == 1);
    CHECK(call(engine, "elevation", {Integer{5}, Integer{0}}) == 1234);
    CHECK(call(engine, "cell_base_elevation", {Integer{5}, Integer{0}}) != 1234);
}

void queries() {
    Engine engine;
    engine.add(olam::module::make_terrain_module(
        "yard", terrain_config("field", 8, 8, 0, 0, 0, 1, 1, 1, 100, 0)));
    engine.seal();
    CHECK(call(engine, "contains", {Integer{0}, Integer{0}}) == 1);
    CHECK(call(engine, "contains", {Integer{7}, Integer{7}}) == 1);
    CHECK(call(engine, "contains", {Integer{8}, Integer{0}}) == 0);
    CHECK(call(engine, "contains", {Integer{-1}, Integer{0}}) == 0);
    CHECK(call(engine, "cell_base_elevation", {Integer{0}, Integer{0}}) == 100);
    CHECK(call(engine, "elevation", {Integer{0}, Integer{0}}) == 100);
    CHECK(call(engine, "slope", {Integer{0}, Integer{0}}) == 0);
    CHECK(call(engine, "cell_base_elevation", {Integer{8}, Integer{0}}) ==
          olam::module::terrain_missing);
    CHECK(call(engine, "elevation", {Integer{0}, Integer{8}}) ==
          olam::module::terrain_missing);
    CHECK(call(engine, "slope", {Integer{-1}, Integer{0}}) == olam::module::terrain_missing);
    CHECK(call(engine, "contains_position", {Integer{0}, Integer{0}}) == 1);
    CHECK(call(engine, "contains_position", {Integer{8000}, Integer{0}}) == 0);
    CHECK(call(engine, "cell_column", {Integer{0}, Integer{0}}) == 0);
    CHECK(call(engine, "cell_row", {Integer{0}, Integer{0}}) == 0);
    CHECK(call(engine, "cell_column", {Integer{8000}, Integer{0}}) ==
          olam::module::terrain_missing);
    CHECK(call(engine, "base_elevation_at", {Integer{0}, Integer{0}}) == 100);
    CHECK(call(engine, "elevation_at", {Integer{0}, Integer{0}}) == 100);
    CHECK(call(engine, "elevation_at", {Integer{8000}, Integer{0}}) ==
          olam::module::terrain_missing);

    engine.call({"yard", "set_elevation"}, {Integer{1}, Integer{1}, Integer{900}});
    CHECK(call(engine, "elevation", {Integer{1}, Integer{1}}) == 900);
    CHECK(call(engine, "cell_base_elevation", {Integer{1}, Integer{1}}) == 100);
    CHECK(call(engine, "slope", {Integer{1}, Integer{1}}) == 800);
    CHECK(read_integer(engine, "edit_count") == 1);
    CHECK(call(engine, "clear_elevation", {Integer{1}, Integer{1}}) == 1);
    CHECK(call(engine, "clear_elevation", {Integer{1}, Integer{1}}) == 0);
    CHECK(call(engine, "elevation", {Integer{1}, Integer{1}}) == 100);
    CHECK(read_integer(engine, "edit_count") == 0);

    // -1 是合法高程；缺失标记不能与格内 -1 混淆。
    Engine negative;
    negative.add(olam::module::make_terrain_module(
        "yard", terrain_config("field", 2, 2, 0, 0, 0, 1, 1, 1, -1, 0)));
    negative.seal();
    CHECK(call(negative, "elevation", {Integer{0}, Integer{0}}) == -1);
    CHECK(call(negative, "cell_base_elevation", {Integer{0}, Integer{0}}) == -1);
    CHECK(call(negative, "elevation_at", {Integer{0}, Integer{0}}) == -1);
    CHECK(call(negative, "base_elevation_at", {Integer{0}, Integer{0}}) == -1);
    CHECK(call(negative, "elevation", {Integer{2}, Integer{0}}) ==
          olam::module::terrain_missing);
    CHECK(call(negative, "elevation_at", {Integer{2000}, Integer{0}}) ==
          olam::module::terrain_missing);

    bool failed = false;
    try {
        engine.call({"yard", "clear_elevation"}, {Integer{8}, Integer{0}});
    } catch (const EngineError& error) {
        failed = error.diagnostic().code == ErrorCode::execution_failed;
    }
    CHECK(failed);

    failed = false;
    try {
        engine.call({"yard", "set_elevation"}, {Integer{8}, Integer{0}, Integer{1}});
    } catch (const EngineError& error) {
        failed = error.diagnostic().code == ErrorCode::execution_failed;
    }
    CHECK(failed);
}

void state() {
    Engine source;
    source.add(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               edit(5, 1234))));
    source.seal();
    const StateSnapshot snapshot = source.capture_state();
    CHECK(snapshot.modules.size() == 1);
    CHECK(snapshot.modules.front().path == "yard");
    CHECK(!snapshot.modules.front().stateless);
    CHECK(snapshot.modules.front().contract == olam::module::terrain_state_contract);
    const auto* checksum = snapshot.modules.front().state.find("base_checksum");
    CHECK(checksum != nullptr);
    const auto* edits = snapshot.modules.front().state.find("edits");
    CHECK(edits != nullptr && edits->kind() == Config::Kind::object);
    CHECK(edits->members().size() == 1);
    CHECK(snapshot.modules.front().state.find("checksum_limit_cells") != nullptr);

    Engine target;
    target.add(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000)));
    target.seal();
    CHECK(read_integer(target, "edit_count") == 0);
    target.restore_state(snapshot);
    CHECK(read_integer(target, "edit_count") == 1);
    CHECK(call(target, "elevation", {Integer{5}, Integer{0}}) == 1234);

    // 状态中的空间身份/原点/层序是权威值：恢复到不同构造配置的实例后以状态为准。
    Engine relocated;
    relocated.add(olam::module::make_terrain_module(
        "yard", terrain_config("different", 16, 8, 999, 888, 7, 42, 3, 4, -1000, 5000)));
    relocated.seal();
    CHECK(read_integer(relocated, "origin_x") == 999);
    CHECK(read_integer(relocated, "layer") == 7);
    relocated.restore_state(snapshot);
    CHECK(read_integer(relocated, "origin_x") == 100);
    CHECK(read_integer(relocated, "origin_y") == 200);
    CHECK(read_integer(relocated, "layer") == 3);
    CHECK(call(relocated, "elevation", {Integer{5}, Integer{0}}) == 1234);

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
        CHECK(read_integer(target, "edit_count") == 1);
        CHECK(call(target, "elevation", {Integer{5}, Integer{0}}) == 1234);
    };

    expect_invalid(Config::integer(1));
    expect_invalid(Config::object({{"base_checksum", Config::integer(0)}}));
    expect_invalid(Config::object(
        {{"id", Config::string("field")},
         {"layer", Config::integer(3)},
         {"origin_x", Config::integer(100)},
         {"origin_y", Config::integer(200)},
         {"columns", Config::integer(16)},
         {"rows", Config::integer(8)},
         {"cell_ticks", Config::integer(1000)},
         {"seed", Config::integer(42)},
         {"octaves", Config::integer(3)},
         {"feature_cells", Config::integer(4)},
         {"base_elevation", Config::integer(-1000)},
         {"amplitude", Config::integer(5000)},
         {"generator_version", Config::integer(2)},
         {"checksum_limit_cells", Config::integer(0)},
         {"base_checksum", Config::integer(123)},
         {"edits", Config::object({})}}));

    const auto replace = [](Config state, const std::string& name, Config value) {
        auto members = state.members();
        for (auto& entry : members) {
            if (entry.first == name) entry.second = std::move(value);
        }
        return Config::object(std::move(members));
    };

    // 修改种子会让基础校验和不匹配。
    expect_invalid(replace(snapshot.modules.front().state, "seed", Config::integer(43)));

    // 修改编辑索引超出网格。
    expect_invalid(replace(
        snapshot.modules.front().state, "edits",
        Config::object({{"999", Config::object({{"elevation", Config::integer(1)}})}})));

    // 状态中的校验和格数上限必须与模块配置一致；1 还会先触发成本上限。
    expect_invalid(replace(snapshot.modules.front().state, "checksum_limit_cells",
                           Config::integer(1)));
    expect_invalid(replace(snapshot.modules.front().state, "checksum_limit_cells",
                           Config::integer(1000)));
    expect_invalid(replace(snapshot.modules.front().state, "base_checksum",
                           Config::integer(-1)));
}

void config_errors() {
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module("yard", Config::integer(1)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", Config::object({{"unknown", Config::integer(1)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", Config::object({{"columns", Config::string("16")}})));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 0)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 0)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 0)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               Config::object({{"0", Config::object({})}}))));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               Config::object({{"0", Config::integer(1)}}))));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", Config::object({{"columns", Config::integer(16)},
                                {"rows", Config::integer(8)},
                                {"checksum_limit_cells", Config::integer(10)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", Config::object({{"checksum_limit_cells", Config::integer(-1)}})));
    CHECK(config_error_text([&] {
              olam::module::make_terrain_module(
                  "yard", Config::object({{"checksum_limit_cells", Config::integer(-1)}}));
          }).find("non-negative") != std::string::npos);
    CHECK(config_error_text([&] {
              olam::module::make_terrain_module(
                  "yard", Config::object({{"columns", Config::integer(16)},
                                          {"rows", Config::integer(8)},
                                          {"checksum_limit_cells", Config::integer(10)}}));
          }).find("10") != std::string::npos);
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 0)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, -1)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, 0, -1)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, -1, 200)));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", Config::object({{"generator_version", Config::integer(2)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", Config::object({{"cell_ticks", Config::integer(500)}})));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               Config::object({{"bad", Config::object(
                                                  {{"elevation", Config::integer(1)}})}}))));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               edit(9999, 1))));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               edit(0, std::numeric_limits<Integer>::max()))));
    EXPECT_CONFIG_ERROR(olam::module::make_terrain_module(
        "yard", terrain_config("field", 16, 8, 100, 200, 3, 42, 3, 4, -1000, 5000,
                               Config::integer(1))));
}

void package() {
    std::vector<ModuleResource> resources;
    const std::string text = read_resource();
    if (!text.empty()) {
        resources.push_back({olam::module::terrain_text_domain, "zh-CN", text});
    }
    const std::string bytes = olam::module::terrain_package_bytes(std::move(resources));
    ModuleLibrary library;
    olam::module::register_terrain_module(library);
    CHECK(library.load(bytes) == olam::module::terrain_definition);
    CHECK(library.contains(olam::module::terrain_definition));
    const auto entries = library.entries();
    CHECK(entries.size() == 1);
    const auto& entry = entries.front();
    CHECK(entry.definition == olam::module::terrain_definition);
    CHECK(entry.version == olam::module::terrain_version);
    CHECK(entry.implementation == olam::module::terrain_implementation);
    CHECK(!entry.stateless);
    CHECK(entry.state_contract == olam::module::terrain_state_contract);
    CHECK(entry.resources == (text.empty() ? 0U : 1U));
    CHECK(entry.dynamic == false);

    Engine engine;
    engine.add(library.factories().create(olam::module::terrain_definition, "yard", Config{}));
    engine.seal();
    CHECK(read_integer(engine, "columns") == 512);
    CHECK(read_integer(engine, "exact") == 1);

    CHECK(library.unload(olam::module::terrain_definition));
    CHECK(!library.contains(olam::module::terrain_definition));
    CHECK(library.load(bytes) == olam::module::terrain_definition);
}

int package_file(const std::string& path) {
    std::ifstream stream(std::filesystem::u8path(path), std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read terrain package");
    const std::string bytes{std::istreambuf_iterator<char>(stream),
                            std::istreambuf_iterator<char>()};
    ModuleLibrary library;
    olam::module::register_terrain_module(library);
    CHECK(library.load(bytes) == olam::module::terrain_definition);
    Engine engine;
    engine.add(library.factories().create(olam::module::terrain_definition, "yard", Config{}));
    engine.seal();
    CHECK(read_integer(engine, "width") == 512000);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
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
    const std::map<std::string, std::function<void()>> tests = {
        {"values", values}, {"queries", queries}, {"state", state},
        {"config_errors", config_errors}, {"package", package},
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
