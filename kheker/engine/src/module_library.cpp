#include <ascend/module_library.hpp>

#include <ascend/engine.hpp>

#include <string>
#include <utility>
#include <vector>

namespace ascend {
namespace {

[[noreturn]] void fail(ErrorCode code, const char* key, const char* fallback,
                       const Reference& target = {}) {
    throw EngineError({code, target, TextRef(TextKey{engine_text_domain, key}, std::string(fallback))});
}

}  // namespace

void ModuleLibrary::register_implementation(std::string definition, std::string implementation,
                                            Factory factory) {
    if (implementation.empty()) {
        fail(ErrorCode::invalid_declaration, "module_library.implementation.empty",
             "A built-in implementation identifier must not be empty");
    }
    // 空定义标识与重复定义由工厂目录报告，失败时不记录实现标识。
    factories_.add_definition(definition, std::move(factory));
    implementations_.emplace(std::move(definition), std::move(implementation));
}

std::string ModuleLibrary::load(const std::string& bytes) {
    ModulePackage package = decode_module_package(bytes);
    const auto& manifest = package.manifest;
    if (manifest.definition.empty()) {
        fail(ErrorCode::invalid_config, "module_library.definition",
             "Module package does not declare a definition identifier");
    }
    if (loaded_.count(manifest.definition) > 0) {
        fail(ErrorCode::duplicate_definition, "module_library.duplicate",
             "The module is already loaded; unload it first",
             Reference{manifest.definition, {}});
    }
    const auto implementation = implementations_.find(manifest.definition);
    if (implementation == implementations_.end()) {
        fail(ErrorCode::invalid_declaration, "module_library.unknown_definition",
             "Module package definition has no built-in implementation",
             Reference{manifest.definition, {}});
    }
    if (manifest.implementation != implementation->second) {
        fail(ErrorCode::invalid_config, "module_library.implementation",
             "Module package implementation does not match the built-in implementation",
             Reference{manifest.definition, {}});
    }
    // 探测实例使用固定实例名与空配置；清单核对只读取实例自身的公开面。
    Module probe = factories_.create(manifest.definition, "probe", Config{});
    Engine engine;
    engine.add(std::move(probe));
    check_module_manifest(manifest, engine, "", "probe");
    // 语言资源在临时目录上预校验格式与域一致性；与宿主目录的冲突在登记时报告。
    TextCatalog probe_catalog;
    std::vector<I18nText> resources;
    resources.reserve(package.resources.size());
    for (const auto& resource : package.resources) {
        I18nText text{resource.domain, resource.text, manifest.definition + "/" + resource.locale};
        probe_catalog.load_text(text);
        resources.push_back(std::move(text));
    }
    const std::string definition = manifest.definition;
    loaded_.emplace(definition, Loaded{std::move(package.manifest), std::move(resources)});
    return definition;
}

bool ModuleLibrary::unload(const std::string& definition) {
    return loaded_.erase(definition) > 0;
}

bool ModuleLibrary::contains(const std::string& definition) const {
    return loaded_.count(definition) > 0;
}

std::vector<std::string> ModuleLibrary::definitions() const {
    std::vector<std::string> result;
    result.reserve(loaded_.size());
    for (const auto& entry : loaded_) result.push_back(entry.first);
    return result;
}

std::vector<ModuleEntry> ModuleLibrary::entries() const {
    std::vector<ModuleEntry> result;
    result.reserve(loaded_.size());
    for (const auto& entry : loaded_) {
        const auto& manifest = entry.second.manifest;
        ModuleEntry summary;
        summary.definition = manifest.definition;
        summary.version = manifest.version;
        summary.implementation = manifest.implementation;
        summary.stateless = manifest.stateless;
        summary.state_contract = manifest.state_contract;
        summary.declarations = manifest.declarations.size();
        summary.requirements = manifest.requirements.size();
        summary.resources = entry.second.resources.size();
        result.push_back(std::move(summary));
    }
    return result;
}

const ModuleManifest& ModuleLibrary::manifest(const std::string& definition) const {
    const auto found = loaded_.find(definition);
    if (found == loaded_.end()) {
        fail(ErrorCode::invalid_config, "module_library.not_loaded", "The module is not loaded",
             Reference{definition, {}});
    }
    return found->second.manifest;
}

std::vector<I18nText> ModuleLibrary::resources(const std::string& definition) const {
    const auto found = loaded_.find(definition);
    if (found == loaded_.end()) {
        fail(ErrorCode::invalid_config, "module_library.not_loaded", "The module is not loaded",
             Reference{definition, {}});
    }
    return found->second.resources;
}

}  // namespace ascend
