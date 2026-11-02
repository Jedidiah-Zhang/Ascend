#include "test_types_module.hpp"

#include <ascend/engine.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>

#include <cstdint>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>
#include <string>

namespace {

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

int run(const std::string& plugin_path) {
    Engine probe;
    probe.add(test_types::module("probe", Config{}));
    ModulePackage package;
    package.manifest = export_module_manifest(probe, "", "probe");
    package.manifest.definition = test_types::definition;
    package.manifest.version = test_types::version;
    package.manifest.implementation = test_types::implementation;
    package.binary = ModuleBinary{"shared-library", PLUGIN_PLATFORM, "ascend_plugin_callbacks",
                                  read_binary(plugin_path)};
    const auto bytes = encode_module_package(package);

    ModuleLibrary library;
    CHECK(library.load(bytes) == test_types::definition);
    Engine engine;
    engine.add(library.factories().create(test_types::definition, "typed",
                                          test_types::default_config()));
    engine.seal();
    CHECK(std::any_cast<bool>(engine.read({"typed", "flag"})) == false);
    CHECK(std::any_cast<double>(engine.read({"typed", "ratio"})) == 1.5);
    CHECK(std::any_cast<std::string>(engine.read({"typed", "label"})) == "initial");
    CHECK(std::any_cast<std::int64_t>(engine.read({"typed", "count"})) == 0);

    engine.call({"typed", "set_flag"}, {true});
    engine.call({"typed", "set_ratio"}, {2.5});
    engine.call({"typed", "set_label"}, {std::string("changed")});
    engine.call({"typed", "advance"}, {});
    CHECK(std::any_cast<bool>(engine.read({"typed", "flag"})) == true);
    CHECK(std::any_cast<double>(engine.read({"typed", "ratio"})) == 2.5);
    CHECK(std::any_cast<std::string>(engine.read({"typed", "label"})) == "changed");
    CHECK(std::any_cast<std::int64_t>(engine.read({"typed", "count"})) == 1);
    CHECK(std::any_cast<double>(engine.call({"typed", "add"}, {2.0, 3.5})) == 5.5);
    CHECK(std::any_cast<std::string>(engine.call({"typed", "echo"}, {std::string("ok")})) == "ok");

    const auto snapshot = engine.capture_state();
    Engine restored;
    restored.add(library.factories().create(test_types::definition, "typed",
                                            test_types::default_config()));
    restored.seal();
    restored.restore_state(snapshot);
    CHECK(std::any_cast<std::string>(restored.read({"typed", "label"})) == "changed");
    CHECK(std::any_cast<std::int64_t>(restored.read({"typed", "count"})) == 1);
    return 0;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    try {
        run(argv[1]);
        std::cout << "plugin types: passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "plugin types: " << error.what() << '\n';
        return 1;
    }
}
