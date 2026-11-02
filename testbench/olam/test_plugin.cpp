#include <ascend/engine.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>
#include <olam/module/space_module.hpp>

#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {

using Integer = std::int64_t;
using namespace ascend;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

std::string read_binary(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream.is_open()) throw std::runtime_error("cannot read plugin binary");
    return std::string(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
}

#ifndef PLUGIN_PLATFORM
#error "PLUGIN_PLATFORM must be defined by the build"
#endif

int run_package(const std::string& package_path) {
    const std::string bytes = read_binary(package_path);
    ModuleLibrary library;
    CHECK(library.load(bytes) == olam::module::space_definition);
    Engine engine;
    engine.add(library.factories().create(olam::module::space_definition, "yard", Config{}));
    engine.seal();
    CHECK(std::any_cast<Integer>(engine.read({"yard", "width"})) == 1000);
    return 0;
}

int run(const std::string& plugin_path) {
    auto package = olam::module::make_space_package();
    package.binary = ModuleBinary{"shared-library", PLUGIN_PLATFORM, "ascend_plugin_callbacks",
                                  read_binary(plugin_path)};
    const std::string bytes = encode_module_package(package);

    ModuleLibrary library;
    CHECK(library.load(bytes) == olam::module::space_definition);
    CHECK(library.contains(olam::module::space_definition));
    CHECK(library.entries().size() == 1);
    CHECK(library.entries().front().definition == olam::module::space_definition);

    Engine engine;
    engine.add(library.factories().create(olam::module::space_definition, "yard", Config{}));
    engine.seal();
    CHECK(std::any_cast<Integer>(engine.read({"yard", "width"})) == 1000);
    CHECK(std::any_cast<Integer>(engine.call({"yard", "contains"}, {Integer{0}, Integer{0}, Integer{0}})) == 1);
    CHECK(std::any_cast<Integer>(engine.call({"yard", "locate"}, {Integer{0}, Integer{999}, Integer{999}})) == 0);
    CHECK(std::any_cast<Integer>(engine.call({"yard", "local_x"}, {Integer{0}, Integer{12}, Integer{34}})) == 12);

    const auto snapshot = engine.capture_state();
    CHECK(snapshot.modules.size() == 1);
    CHECK(snapshot.modules.front().path == "yard");
    CHECK(!snapshot.modules.front().stateless);
    CHECK(snapshot.modules.front().state.find("spaces") != nullptr);

    Engine restored;
    restored.add(library.factories().create(olam::module::space_definition, "yard", Config{}));
    restored.seal();
    restored.restore_state(snapshot);
    CHECK(std::any_cast<Integer>(restored.read({"yard", "upper_x"})) == 1000);

    // 插件载荷与内置载体相同：两个方向都可以恢复。
    Engine builtin;
    builtin.add(olam::module::make_space_module("yard", Config{}));
    builtin.seal();
    builtin.restore_state(snapshot);
    CHECK(std::any_cast<Integer>(builtin.read({"yard", "width"})) == 1000);
    Engine from_builtin;
    from_builtin.add(library.factories().create(olam::module::space_definition, "yard", Config{}));
    from_builtin.seal();
    from_builtin.restore_state(builtin.capture_state());
    CHECK(std::any_cast<Integer>(from_builtin.read({"yard", "upper_y"})) == 1000);

    CHECK(library.unload(olam::module::space_definition));
    CHECK(!library.contains(olam::module::space_definition));
    // 活动实例仍持有插件句柄，卸载登记不提前释放共享库。
    CHECK(std::any_cast<Integer>(restored.read({"yard", "upper_y"})) == 1000);

    // 平台不匹配的包在登记前拒绝。
    auto wrong_platform = package;
    wrong_platform.binary->platform = "not-a-platform";
    bool rejected = false;
    try {
        ModuleLibrary other;
        other.load(encode_module_package(wrong_platform));
    } catch (const EngineError& error) {
        rejected = error.diagnostic().code == ErrorCode::invalid_config;
    }
    CHECK(rejected);

    CHECK(library.load(bytes) == olam::module::space_definition);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc == 3 && std::string(argv[1]) == "package_file") {
        try {
            run_package(argv[2]);
            std::cout << "plugin package: passed\n";
            return 0;
        } catch (const std::exception& error) {
            std::cerr << "plugin package: " << error.what() << '\n';
            return 1;
        }
    }
    if (argc != 2) return 2;
    try {
        run(argv[1]);
        std::cout << "plugin: passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "plugin: " << error.what() << '\n';
        return 1;
    }
}
