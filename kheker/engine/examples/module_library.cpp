// 模块库示例（ENV-18）：打包三个测试模块为 .amod，登记内置实现，装入、列表、卸载与重装。
// 用法：ascend_module_library [输出目录]；输出目录必须存在，默认当前目录。
#include <ascend/module_library.hpp>

#include <fstream>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

using Integer = std::int64_t;
using ascend::Config;
using ascend::Context;
using ascend::Engine;
using ascend::MethodOptions;
using ascend::Module;
using ascend::ModuleLibrary;
using ascend::ModulePackage;
using ascend::ModuleResource;
using ascend::TextCatalog;
using ascend::TextKey;
using ascend::TextRef;

Module make_source(const std::string& instance, const Config&) {
    Module module(instance);
    module.declare_stateless();
    module.add_value<Integer>("value", [] { return Integer{7}; }, {}, "library.scalar.v1");
    return module;
}

Module make_accumulator(const std::string& instance, const Config&) {
    Module module(instance);
    auto total = std::make_shared<Integer>(0);
    module.add_value<Integer>("total", [total] { return *total; }, {}, "library.scalar.v1");
    MethodOptions add_options;
    add_options.contract = "library.accumulator.add.v1";
    module.add_method<void, Integer>("add", {"amount"},
                                     [total](Integer amount) { *total += amount; }, add_options);
    module.add_state("library.accumulator.state.v1", [total] { return Config::integer(*total); },
                     [total](const Config& state) { *total = state.integer(); });
    return module;
}

Module make_relay(const std::string& instance, const Config&) {
    Module module(instance);
    auto input = module.require_value<Integer>("in", "library.scalar.v1");
    module.declare_stateless();
    module.add_value<Integer>("out", [input](const Context& context) { return input.read(context); },
                              {}, "library.scalar.v1");
    return module;
}

template <typename Assemble>
ModulePackage pack(Assemble&& assemble, const std::string& definition,
                   const std::string& implementation, std::vector<ModuleResource> resources = {}) {
    Engine engine;
    assemble(engine);
    ModulePackage package;
    package.manifest = ascend::export_module_manifest(engine, "", "probe");
    package.manifest.definition = definition;
    package.manifest.version = "1.0";
    package.manifest.implementation = implementation;
    package.resources = std::move(resources);
    return package;
}

bool write_file(const std::string& path, const std::string& bytes) {
    std::ofstream stream(path, std::ios::binary);
    if (!stream.is_open()) return false;
    stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
    return stream.good();
}

bool read_file(const std::string& path, std::string& bytes) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream.is_open()) return false;
    bytes.assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    return !stream.bad();
}

}  // namespace

int main(int argc, char** argv) {
    try {
    const std::string directory = argc > 1 ? argv[1] : ".";
    const auto path = [&](const std::string& name) { return directory + "/library." + name + ".amod"; };

    // 1. 打包：三个测试模块（无状态值、带状态方法、带需求与资源）编码为容器文件。
    const auto source_bytes = ascend::encode_module_package(pack(
        [](Engine& engine) { engine.add(make_source("probe", {})); }, "library.source",
        "library.source.int.v1"));
    const auto accumulator_bytes = ascend::encode_module_package(pack(
        [](Engine& engine) { engine.add(make_accumulator("probe", {})); }, "library.accumulator",
        "library.accumulator.int.v1"));
    const auto relay_bytes = ascend::encode_module_package(pack(
        [](Engine& engine) {
            Module provider("provider");
            provider.declare_stateless();
            provider.add_value<Integer>("value", [] { return Integer{1}; }, {}, "library.scalar.v1");
            engine.add(std::move(provider));
            engine.add(make_relay("probe", {}));
            engine.connect({"probe", "in"}, {"provider", "value"});
        },
        "library.relay", "library.relay.int.v1",
        {{"library.relay", "zh-CN",
          R"({"format":"ascend.i18n","version":1,"domain":"library.relay","locale":"zh-CN","entries":{"out":"\u4e2d\u7ee7\u8f93\u51fa"}})"}}));
    const bool packed = write_file(path("source"), source_bytes) &&
                        write_file(path("accumulator"), accumulator_bytes) &&
                        write_file(path("relay"), relay_bytes);

    // 2. 宿主登记内置实现。
    ModuleLibrary library;
    library.register_implementation("library.source", "library.source.int.v1", make_source);
    library.register_implementation("library.accumulator", "library.accumulator.int.v1",
                                    make_accumulator);
    library.register_implementation("library.relay", "library.relay.int.v1", make_relay);

    // 3. 从磁盘装入。
    std::string bytes;
    const bool loaded = read_file(path("source"), bytes) &&
                        library.load(bytes) == "library.source" &&
                        read_file(path("accumulator"), bytes) &&
                        library.load(bytes) == "library.accumulator" &&
                        read_file(path("relay"), bytes) && library.load(bytes) == "library.relay";

    // 4. 列表、资源登记、卸载与重装。
    const auto entries = library.entries();
    const bool listed = entries.size() == 3 && library.contains("library.relay") &&
                        entries[1].definition == "library.relay" && entries[1].resources == 1;
    TextCatalog catalog;
    for (const auto& resource : library.resources("library.relay")) catalog.load_text(resource);
    const bool rendered =
        catalog.resolve("zh-CN", TextRef(TextKey{"library.relay", "out"})) == "\u4e2d\u7ee7\u8f93\u51fa";
    const bool unloaded = library.unload("library.source") && !library.contains("library.source");
    const bool reloaded = !library.unload("library.source") && library.load(source_bytes) == "library.source";

    if (!(packed && loaded && listed && rendered && unloaded && reloaded)) {
        std::cerr << "module library example failed\n";
        return 1;
    }
    std::cout << "library=ok loaded=3 unloaded=1 reloaded=1\n";
    return 0;
    } catch (const std::exception& error) {
        std::cerr << "module library example failed: " << error.what() << '\n';
        return 1;
    }
}
