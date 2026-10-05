// 模块库示例（ENV-18）：打包三个示例模块为 .amod，登记内置实现，载入、列表、卸载与重装。
// 用法：ascend_module_library [输出目录]；输出目录必须存在，默认当前目录。
#include <ascend/example/experiment_model.hpp>
#include <ascend/module_library.hpp>

#include <fstream>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {

using Integer = std::int64_t;
using ascend::Engine;
using ascend::ModulePackage;
using ascend::ModuleResource;
using ascend::TextCatalog;
using ascend::TextKey;
using ascend::TextRef;

// 把模块（可含提供方）装配成探测引擎，导出清单并打包成模块包。
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

    // 1. 打包：三个示例模块（无状态值、带状态方法、带需求与资源）编码为容器文件。
    const auto source_bytes = ascend::encode_module_package(pack(
        [](Engine& engine) { engine.add(ascend::example::module_library_source("probe", {})); },
        ascend::example::library_source_definition, ascend::example::library_source_implementation));
    const auto accumulator_bytes = ascend::encode_module_package(pack(
        [](Engine& engine) { engine.add(ascend::example::module_library_accumulator("probe", {})); },
        ascend::example::library_accumulator_definition,
        ascend::example::library_accumulator_implementation));
    const auto relay_bytes = ascend::encode_module_package(pack(
        [](Engine& engine) {
            ascend::Module provider("provider");
            provider.declare_stateless();
            provider.add_value<Integer>("value", [] { return Integer{1}; }, {}, "library.scalar.v1");
            engine.add(std::move(provider));
            engine.add(ascend::example::module_library_relay("probe", {}));
            engine.connect({"probe", "in"}, {"provider", "value"});
        },
        ascend::example::library_relay_definition, ascend::example::library_relay_implementation,
        {{ascend::example::library_relay_definition, "zh-CN",
          R"({"format":"ascend.i18n","version":1,"domain":"library.relay","locale":"zh-CN","entries":{"out":"\u4e2d\u7ee7\u8f93\u51fa"}})"}}));
    const bool packed = write_file(path("source"), source_bytes) &&
                        write_file(path("accumulator"), accumulator_bytes) &&
                        write_file(path("relay"), relay_bytes);

    // 2. 宿主登记内置实现。
    ascend::ModuleLibrary library;
    ascend::example::register_module_library(library);

    // 3. 从磁盘载入。
    std::string bytes;
    const bool loaded = read_file(path("source"), bytes) &&
                        library.load(bytes) == ascend::example::library_source_definition &&
                        read_file(path("accumulator"), bytes) &&
                        library.load(bytes) == ascend::example::library_accumulator_definition &&
                        read_file(path("relay"), bytes) &&
                        library.load(bytes) == ascend::example::library_relay_definition;

    // 4. 列表、资源登记、卸载与重装。
    const auto entries = library.entries();
    const bool listed = entries.size() == 3 && library.contains(ascend::example::library_relay_definition) &&
                        entries[1].definition == ascend::example::library_relay_definition &&
                        entries[1].resources == 1;
    TextCatalog catalog;
    for (const auto& resource : library.resources(ascend::example::library_relay_definition)) {
        catalog.load_text(resource);
    }
    const bool rendered =
        catalog.resolve("zh-CN", TextRef(TextKey{ascend::example::library_relay_definition, "out"})) ==
        "\u4e2d\u7ee7\u8f93\u51fa";
    const bool unloaded = library.unload(ascend::example::library_source_definition) &&
                          !library.contains(ascend::example::library_source_definition);
    const bool reloaded = !library.unload(ascend::example::library_source_definition) &&
                          library.load(source_bytes) == ascend::example::library_source_definition;

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
