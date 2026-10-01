#include <ascend/module_package.hpp>

#include "container.hpp"
#include "json.hpp"

#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <typeindex>
#include <typeinfo>
#include <utility>
#include <vector>

namespace ascend {
namespace {

constexpr std::uint64_t kSectionManifest = 1;
constexpr std::uint64_t kSectionResources = 2;

[[noreturn]] void fail(ErrorCode code, const char* key, const char* fallback,
                       const Reference& target = {}) {
    throw EngineError({code, target, TextRef(TextKey{engine_text_domain, key}, std::string(fallback))});
}

// 首版稳定类型名；其他类型在导出前拒绝（ENV-17）。
std::string require_type_name(std::type_index type, const Reference& target) {
    if (type == typeid(void)) return "void";
    if (type == typeid(bool)) return "bool";
    if (type == typeid(std::int64_t)) return "int64";
    if (type == typeid(double)) return "double";
    if (type == typeid(std::string)) return "string";
    fail(ErrorCode::type_mismatch, "module_package.unsupported_type",
         "Module manifests support only void, bool, int64, double and string types", target);
}

// 把以实例为根的绝对引用相对化；越出模块时返回 nullopt。
std::optional<std::string> relative_module(const std::string& module, const std::string& instance) {
    if (module == instance) return std::string{};
    if (module.size() > instance.size() &&
        module.compare(0, instance.size(), instance) == 0 && module[instance.size()] == '/') {
        return module.substr(instance.size() + 1);
    }
    return std::nullopt;
}

Reference relativize(const Reference& reference, const std::string& instance) {
    const auto relative = relative_module(reference.module, instance);
    if (!relative.has_value()) {
        fail(ErrorCode::invalid_config, "module_package.reference",
             "Module manifest reference leaves the module", reference);
    }
    return Reference{*relative, reference.symbol};
}

std::string kind_name(SymbolKind kind) { return kind == SymbolKind::method ? "method" : "value"; }

SymbolKind kind_from(const std::string& name, const char* key, const char* fallback) {
    if (name == "value") return SymbolKind::value;
    if (name == "method") return SymbolKind::method;
    fail(ErrorCode::invalid_json, key, fallback);
}

Config text_config(const TextRef& text) {
    std::vector<std::pair<std::string, Config>> members;
    if (text.is_literal()) {
        members.emplace_back("literal", Config::string(text.literal()));
    } else {
        members.emplace_back("domain", Config::string(text.key().domain));
        members.emplace_back("key", Config::string(text.key().key));
        if (text.fallback().has_value()) members.emplace_back("fallback", Config::string(*text.fallback()));
    }
    std::vector<Config> arguments;
    for (const auto& [name, value] : text.arguments()) {
        arguments.push_back(Config::object(
            {{"name", Config::string(name)}, {"text", text_config(value)}}));
    }
    if (!arguments.empty()) members.emplace_back("arguments", Config::array(std::move(arguments)));
    return Config::object(std::move(members));
}

const Config& require_member(const Config& object, const char* name) {
    const auto* member = object.find(name);
    if (member == nullptr) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest is missing a required field");
    }
    return *member;
}

std::string require_string(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::string) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest field has the wrong type");
    }
    return member.string();
}

std::int64_t require_integer(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::integer) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest field has the wrong type");
    }
    return member.integer();
}

const Config& require_object(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest field has the wrong type");
    }
    return member;
}

const Config& require_array(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::array) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest field has the wrong type");
    }
    return member;
}

std::optional<std::string> optional_string(const Config& object, const char* name) {
    const auto* member = object.find(name);
    if (member == nullptr) return std::nullopt;
    if (member->kind() != Config::Kind::string) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest field has the wrong type");
    }
    return member->string();
}

TextRef text_from(const Config& value) {
    if (value.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest text is not an object");
    }
    if (const auto* literal = value.find("literal"); literal != nullptr) {
        if (literal->kind() != Config::Kind::string) {
            fail(ErrorCode::invalid_json, "module_package.manifest",
                 "Module manifest text field has the wrong type");
        }
        return TextRef(literal->string());
    }
    const auto domain = require_string(value, "domain");
    const auto key = require_string(value, "key");
    std::optional<std::string> fallback;
    if (const auto* item = value.find("fallback"); item != nullptr) {
        if (item->kind() != Config::Kind::string) {
            fail(ErrorCode::invalid_json, "module_package.manifest",
                 "Module manifest text field has the wrong type");
        }
        fallback = item->string();
    }
    TextRef::Arguments arguments;
    if (const auto* list = value.find("arguments"); list != nullptr) {
        if (list->kind() != Config::Kind::array) {
            fail(ErrorCode::invalid_json, "module_package.manifest",
                 "Module manifest text field has the wrong type");
        }
        for (const auto& entry : list->elements()) {
            const auto name = require_string(entry, "name");
            arguments.emplace_back(name, text_from(require_object(entry, "text")));
        }
    }
    try {
        return TextRef(TextKey{domain, key}, fallback, std::move(arguments));
    } catch (const std::invalid_argument&) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest text definition is not valid");
    }
}

bool same_text(const TextRef& left, const TextRef& right) {
    if (left.is_literal() != right.is_literal()) return false;
    if (left.is_literal()) return left.literal() == right.literal();
    if (!(left.key() == right.key())) return false;
    if (left.fallback().has_value() != right.fallback().has_value()) return false;
    if (left.fallback().has_value() && *left.fallback() != *right.fallback()) return false;
    if (left.arguments().size() != right.arguments().size()) return false;
    for (std::size_t index = 0; index < left.arguments().size(); ++index) {
        if (left.arguments()[index].first != right.arguments()[index].first) return false;
        if (!same_text(left.arguments()[index].second, right.arguments()[index].second)) return false;
    }
    return true;
}

Config reference_config(const Reference& reference) {
    return Config::object({{"module", Config::string(reference.module)},
                           {"symbol", Config::string(reference.symbol)}});
}

Reference reference_from(const Config& value) {
    if (value.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest reference is not an object");
    }
    return Reference{require_string(value, "module"), require_string(value, "symbol")};
}

Config declaration_config(const ManifestDeclaration& item) {
    std::vector<Config> parameters;
    for (const auto& parameter : item.parameters) {
        parameters.push_back(Config::object({{"name", Config::string(parameter.name)},
                                             {"type", Config::string(parameter.type)}}));
    }
    std::vector<Config> reads;
    for (const auto& reference : item.reads) reads.push_back(reference_config(reference));
    std::vector<Config> writes;
    for (const auto& reference : item.writes) writes.push_back(reference_config(reference));
    return Config::object({{"module", Config::string(item.module)},
                           {"symbol", Config::string(item.symbol)},
                           {"kind", Config::string(kind_name(item.kind))},
                           {"result_type", Config::string(item.result_type)},
                           {"parameters", Config::array(std::move(parameters))},
                           {"description", text_config(item.description)},
                           {"reads", Config::array(std::move(reads))},
                           {"writes", Config::array(std::move(writes))},
                           {"contract", Config::string(item.contract)}});
}

ManifestDeclaration declaration_from(const Config& value) {
    if (value.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest declaration is not an object");
    }
    ManifestDeclaration item;
    item.module = require_string(value, "module");
    item.symbol = require_string(value, "symbol");
    item.kind = kind_from(require_string(value, "kind"), "module_package.manifest",
                          "Module manifest declaration kind is not recognized");
    item.result_type = require_string(value, "result_type");
    for (const auto& parameter : require_array(value, "parameters").elements()) {
        item.parameters.push_back(
            ManifestParameter{require_string(parameter, "name"), require_string(parameter, "type")});
    }
    item.description = text_from(require_object(value, "description"));
    for (const auto& reference : require_array(value, "reads").elements()) {
        item.reads.push_back(reference_from(reference));
    }
    for (const auto& reference : require_array(value, "writes").elements()) {
        item.writes.push_back(reference_from(reference));
    }
    item.contract = require_string(value, "contract");
    return item;
}

Config requirement_config(const ManifestRequirement& item) {
    std::vector<Config> parameters;
    for (const auto& parameter : item.parameters) parameters.push_back(Config::string(parameter));
    return Config::object({{"module", Config::string(item.module)},
                           {"symbol", Config::string(item.symbol)},
                           {"kind", Config::string(kind_name(item.kind))},
                           {"result_type", Config::string(item.result_type)},
                           {"parameters", Config::array(std::move(parameters))},
                           {"description", text_config(item.description)},
                           {"contract", Config::string(item.contract)}});
}

ManifestRequirement requirement_from(const Config& value) {
    if (value.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest requirement is not an object");
    }
    ManifestRequirement item;
    item.module = require_string(value, "module");
    item.symbol = require_string(value, "symbol");
    item.kind = kind_from(require_string(value, "kind"), "module_package.manifest",
                          "Module manifest requirement kind is not recognized");
    item.result_type = require_string(value, "result_type");
    for (const auto& parameter : require_array(value, "parameters").elements()) {
        if (parameter.kind() != Config::Kind::string) {
            fail(ErrorCode::invalid_json, "module_package.manifest",
                 "Module manifest field has the wrong type");
        }
        item.parameters.push_back(parameter.string());
    }
    item.description = text_from(require_object(value, "description"));
    item.contract = require_string(value, "contract");
    return item;
}

Config manifest_config(const ModuleManifest& manifest) {
    std::vector<Config> declarations;
    for (const auto& item : manifest.declarations) declarations.push_back(declaration_config(item));
    std::vector<Config> requirements;
    for (const auto& item : manifest.requirements) requirements.push_back(requirement_config(item));
    std::vector<std::pair<std::string, Config>> members;
    members.emplace_back("format", Config::string("ascend.module"));
    members.emplace_back("version", Config::integer(1));
    members.emplace_back("definition", Config::string(manifest.definition));
    if (!manifest.version.empty()) members.emplace_back("module_version", Config::string(manifest.version));
    if (!manifest.implementation.empty()) {
        members.emplace_back("implementation", Config::string(manifest.implementation));
    }
    members.emplace_back("state", Config::object({{"stateless", Config::boolean(manifest.stateless)},
                                                  {"contract", Config::string(manifest.state_contract)}}));
    members.emplace_back("declarations", Config::array(std::move(declarations)));
    members.emplace_back("requirements", Config::array(std::move(requirements)));
    return Config::object(std::move(members));
}

ModuleManifest manifest_from(const Config& value) {
    if (value.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest is not an object");
    }
    if (require_string(value, "format") != "ascend.module") {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest format is not recognized");
    }
    if (require_integer(value, "version") != 1) {
        fail(ErrorCode::unsupported_format_version, "module_package.version",
             "Module manifest version is not supported");
    }
    ModuleManifest manifest;
    manifest.definition = require_string(value, "definition");
    manifest.version = optional_string(value, "module_version").value_or(std::string{});
    manifest.implementation = optional_string(value, "implementation").value_or(std::string{});
    const auto& state = require_object(value, "state");
    const auto& stateless = require_member(state, "stateless");
    if (stateless.kind() != Config::Kind::boolean) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module manifest field has the wrong type");
    }
    manifest.stateless = stateless.boolean();
    manifest.state_contract = require_string(state, "contract");
    for (const auto& entry : require_array(value, "declarations").elements()) {
        manifest.declarations.push_back(declaration_from(entry));
    }
    for (const auto& entry : require_array(value, "requirements").elements()) {
        manifest.requirements.push_back(requirement_from(entry));
    }
    return manifest;
}

Config resources_config(const std::vector<ModuleResource>& resources) {
    std::vector<Config> entries;
    for (const auto& resource : resources) {
        entries.push_back(Config::object({{"domain", Config::string(resource.domain)},
                                          {"locale", Config::string(resource.locale)},
                                          {"text", Config::string(resource.text)}}));
    }
    return Config::object({{"resources", Config::array(std::move(entries))}});
}

std::vector<ModuleResource> resources_from(const Config& value) {
    if (value.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "module_package.manifest",
             "Module resources are not an object");
    }
    std::vector<ModuleResource> resources;
    for (const auto& entry : require_array(value, "resources").elements()) {
        resources.push_back(ModuleResource{require_string(entry, "domain"),
                                           require_string(entry, "locale"),
                                           require_string(entry, "text")});
    }
    return resources;
}

}  // namespace

ModuleManifest export_module_manifest(const Engine& engine, const std::string& scope,
                                      const std::string& instance) {
    ModuleManifest manifest;
    for (const auto& declaration : engine.catalog(scope)) {
        const auto relative = relative_module(declaration.reference.module, instance);
        if (!relative.has_value() || !relative->empty()) continue;
        ManifestDeclaration item;
        item.module = *relative;
        item.symbol = declaration.reference.symbol;
        item.kind = declaration.kind;
        item.result_type = require_type_name(declaration.result_type, declaration.reference);
        for (const auto& parameter : declaration.parameters) {
            item.parameters.push_back(ManifestParameter{
                parameter.name, require_type_name(parameter.type, declaration.reference)});
        }
        item.description = declaration.description;
        for (const auto& reference : declaration.reads) {
            item.reads.push_back(relativize(reference, instance));
        }
        for (const auto& reference : declaration.writes) {
            item.writes.push_back(relativize(reference, instance));
        }
        item.contract = declaration.contract;
        manifest.declarations.push_back(std::move(item));
    }
    for (const auto& requirement : engine.requirements(scope)) {
        const auto relative = relative_module(requirement.reference.module, instance);
        if (!relative.has_value()) continue;
        ManifestRequirement item;
        item.module = *relative;
        item.symbol = requirement.reference.symbol;
        item.kind = requirement.kind;
        item.result_type = require_type_name(requirement.result_type, requirement.reference);
        for (const auto& type : requirement.parameters) {
            item.parameters.push_back(require_type_name(type, requirement.reference));
        }
        item.contract = requirement.contract;
        item.description = requirement.description;
        manifest.requirements.push_back(std::move(item));
    }
    const auto snapshot = engine.capture_state();
    bool found = false;
    for (const auto& module : snapshot.modules) {
        if (module.path != instance) continue;
        manifest.stateless = module.stateless;
        manifest.state_contract = module.contract;
        found = true;
        break;
    }
    if (!found) {
        fail(ErrorCode::state_incomplete, "module_package.state",
             "Module manifest export cannot find the instance state declaration",
             Reference{instance, {}});
    }
    return manifest;
}

namespace {

// 清单匹配键为（相对模块路径、符号）；ENV-17 不要求清单顺序。
std::string manifest_key(const std::string& module, const std::string& symbol) {
    return module + '\x1f' + symbol;
}

template <typename Mismatch>
void compare_declaration(const ManifestDeclaration& expected, const ManifestDeclaration& actual,
                         const Mismatch& mismatch) {
    if (expected.kind != actual.kind || expected.result_type != actual.result_type ||
        expected.contract != actual.contract) {
        mismatch("declarations");
    }
    if (expected.parameters.size() != actual.parameters.size()) mismatch("parameters");
    for (std::size_t parameter = 0; parameter < expected.parameters.size(); ++parameter) {
        if (expected.parameters[parameter].name != actual.parameters[parameter].name ||
            expected.parameters[parameter].type != actual.parameters[parameter].type) {
            mismatch("parameters");
        }
    }
    if (expected.reads != actual.reads || expected.writes != actual.writes) mismatch("references");
    if (!same_text(expected.description, actual.description)) mismatch("description");
}

template <typename Mismatch>
void compare_requirement(const ManifestRequirement& expected, const ManifestRequirement& actual,
                         const Mismatch& mismatch) {
    if (expected.kind != actual.kind || expected.result_type != actual.result_type ||
        expected.contract != actual.contract || expected.parameters != actual.parameters) {
        mismatch("requirements");
    }
    if (!same_text(expected.description, actual.description)) mismatch("description");
}

}  // namespace

void check_module_manifest(const ModuleManifest& manifest, const Engine& engine,
                           const std::string& scope, const std::string& instance) {
    const auto runtime = export_module_manifest(engine, scope, instance);
    const auto mismatch = [&](const char* field) {
        fail(ErrorCode::state_mismatch, "module_package.mismatch",
             "Module manifest does not match the runtime declaration",
             Reference{instance, field});
    };
    if (manifest.stateless != runtime.stateless ||
        manifest.state_contract != runtime.state_contract) {
        mismatch("state");
    }
    std::map<std::string, const ManifestDeclaration*> declarations;
    for (const auto& item : runtime.declarations) {
        declarations.emplace(manifest_key(item.module, item.symbol), &item);
    }
    if (manifest.declarations.size() != declarations.size()) mismatch("declarations");
    for (const auto& expected : manifest.declarations) {
        const auto found = declarations.find(manifest_key(expected.module, expected.symbol));
        if (found == declarations.end()) mismatch("declarations");
        compare_declaration(expected, *found->second, mismatch);
    }
    std::map<std::string, const ManifestRequirement*> requirements;
    for (const auto& item : runtime.requirements) {
        requirements.emplace(manifest_key(item.module, item.symbol), &item);
    }
    if (manifest.requirements.size() != requirements.size()) mismatch("requirements");
    for (const auto& expected : manifest.requirements) {
        const auto found = requirements.find(manifest_key(expected.module, expected.symbol));
        if (found == requirements.end()) mismatch("requirements");
        compare_requirement(expected, *found->second, mismatch);
    }
}

std::string encode_module_package(const ModulePackage& package) {
    const auto manifest_text = detail::write_json(manifest_config(package.manifest), "manifest");
    const auto resources_text = detail::write_json(resources_config(package.resources), "resources");
    return detail::write_container(detail::container_kind_module,
                                   {{kSectionManifest, detail::container_section_required, manifest_text},
                                    {kSectionResources, detail::container_section_required, resources_text}});
}

ModulePackage decode_module_package(const std::string& bytes) {
    std::optional<std::string> manifest_text;
    std::optional<std::string> resources_text;
    for (auto& section : detail::read_container(bytes, detail::container_kind_module, "module_package")) {
        if (section.id == kSectionManifest) {
            manifest_text = std::move(section.data);
        } else if (section.id == kSectionResources) {
            resources_text = std::move(section.data);
        } else if ((section.flags & detail::container_section_required) != 0) {
            fail(ErrorCode::unsupported_format_version, "module_package.section",
                 "Module package has a required section this reader does not know");
        }
    }
    if (!manifest_text.has_value() || !resources_text.has_value()) {
        fail(ErrorCode::invalid_json, "module_package.section",
             "Module package is missing a required section");
    }
    ModulePackage package;
    package.manifest = manifest_from(detail::parse_json(*manifest_text, "module manifest"));
    package.resources = resources_from(detail::parse_json(*resources_text, "module resources"));
    return package;
}

}  // namespace ascend
