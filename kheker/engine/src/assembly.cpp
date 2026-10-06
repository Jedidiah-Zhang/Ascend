#include <ascend/assembly.hpp>

#include "engine_internal.hpp"
#include "json.hpp"

#include <ascend/text.hpp>

#include <algorithm>
#include <fstream>

namespace ascend {
namespace {

std::string scope_join(const std::string& parent, const std::string& name) {
    return parent.empty() ? name : parent + '/' + name;
}

std::string record_join(const std::string& parent, const std::string& segment) {
    return detail::json_pointer_join(parent, segment);
}

[[noreturn]] void fail_assembly(ErrorCode code, const Reference& target, TextRef message,
                             const std::string& source, const std::string& record) {
    throw EngineError({code, target, std::move(message), source, record});
}

bool valid_scope_name(const std::string& name) {
    return !name.empty() && name.find('/') == std::string::npos;
}

}  // namespace

void ModuleFactoryDirectory::add_definition(std::string definition, Factory factory,
                                           std::vector<I18nResource> resources, Config preset) {
    const Reference target{definition, {}};
    if (definition.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                         {{engine_text_domain, "assembly.factory.empty_definition"},
                          "Definition identifier must not be empty"});
    }
    if (!factory) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                         {{engine_text_domain, "assembly.factory.empty_factory"}, "Definition constructor must be set"});
    }
    for (const auto& resource : resources) {
        if (resource.domain.empty() || resource.path.empty()) {
            detail::fail_text(ErrorCode::invalid_declaration, target,
                {{engine_text_domain, "i18n.resource.invalid"}, "Resource domain and path must be non-empty"});
        }
    }
    if (!factories_
             .emplace(std::move(definition),
                      Definition{std::move(factory), std::move(resources), std::move(preset)})
             .second) {
        detail::fail_text(ErrorCode::duplicate_definition, target,
                         {{engine_text_domain, "assembly.factory.duplicate_definition"},
                          "Definition is already registered"});
    }
}

bool ModuleFactoryDirectory::contains(const std::string& definition) const {
    return factories_.count(definition) != 0;
}

Config ModuleFactoryDirectory::default_config(const std::string& definition) const {
    const auto found = factories_.find(definition);
    if (found == factories_.end()) {
        detail::fail_text(ErrorCode::unknown_definition, {definition, {}},
                          {{engine_text_domain, "assembly.factory.unknown_definition"},
                           "Module definition '{definition}' is not registered",
                           {{"definition", definition}}});
    }
    return found->second.preset;
}

void ModuleFactoryDirectory::merge_from(const ModuleFactoryDirectory& other) {
    for (const auto& item : other.factories_) {
        factories_.emplace(item.first, item.second);  // 已存在的定义保持不变
    }
}

const std::vector<I18nResource>& ModuleFactoryDirectory::i18n_resources(const std::string& definition) const {
    const auto found = factories_.find(definition);
    if (found == factories_.end()) detail::fail_text(ErrorCode::unknown_definition, {definition, {}},
        {{engine_text_domain, "assembly.factory.unknown_definition"}, "Module definition '{definition}' is not registered",
         {{"definition", definition}}});
    return found->second.resources;
}

std::vector<std::string> ModuleFactoryDirectory::definitions() const {
    std::vector<std::string> result;
    result.reserve(factories_.size());
    for (const auto& item : factories_) result.push_back(item.first);
    return result;
}

Module ModuleFactoryDirectory::create(const std::string& definition, const std::string& instance,
                                      const Config& config) const {
    const auto found = factories_.find(definition);
    if (found == factories_.end()) {
        detail::fail_text(ErrorCode::unknown_definition, {instance, {}},
                          {{engine_text_domain, "assembly.factory.unknown_definition"},
                           "Module definition '{definition}' is not registered",
                           {{"definition", definition}}});
    }
    Module module = [&] {
        try {
            return found->second.factory(instance, config);
        } catch (const EngineError& error) {
            // 构造期尚无完整模块树，实例与内部位置分别保存，不按名称猜测归属。
            throw EngineError(detail::wrap_diagnostic(
                error.diagnostic().code, {instance, {}},
                {{engine_text_domain, "assembly.factory.construction_failed"},
                 "Factory '{definition}' failed during construction",
                 {{"definition", definition}}}, error.diagnostic()));
        } catch (const std::exception& error) {
            detail::fail(ErrorCode::invalid_config, {instance, {}}, error.what());
        } catch (...) {
            detail::fail_text(ErrorCode::invalid_config, {instance, {}},
                              {{engine_text_domain, "assembly.factory.non_standard_exception"},
                               "Factory threw a non-standard exception"});
        }
    }();
    if (module.name() != instance) {
        detail::fail_text(ErrorCode::invalid_config, {instance, {}},
                          {{engine_text_domain, "assembly.factory.name_mismatch"},
                           "Factory returned module '{returned}' for instance '{instance}'",
                           {{"returned", module.name()}, {"instance", instance}}});
    }
    return module;
}

AssemblyDefinition::AssemblyDefinition() {
    Scope root;
    root.path.clear();
    scopes_.emplace("", std::move(root));
}

const AssemblyDefinition::Scope& AssemblyDefinition::require_scope(const std::string& path) const {
    const auto found = scopes_.find(path);
    if (found == scopes_.end()) {
        fail_assembly(ErrorCode::invalid_assembly, {path, {}},
                      {{engine_text_domain, "assembly.scope.missing"}, "Assembly scope not found"}, source_, {});
    }
    return found->second;
}

AssemblyDefinition::Scope& AssemblyDefinition::require_scope(const std::string& path) {
    return const_cast<Scope&>(std::as_const(*this).require_scope(path));
}

void AssemblyDefinition::add_scope(const std::string& name, const std::string& scope) {
    Scope& parent = require_scope(scope);
    if (!valid_scope_name(name)) {
        fail_assembly(ErrorCode::invalid_assembly, {scope_join(scope, name), {}},
                      {{engine_text_domain, "assembly.scope.invalid_name"},
                       "Scope name must be non-empty and must not contain '/'"},
                      source_, {});
    }
    const std::string path = scope_join(scope, name);
    if (scopes_.count(path) != 0) {
        fail_assembly(ErrorCode::invalid_assembly, {path, {}},
                      {{engine_text_domain, "assembly.scope.duplicate"}, "Duplicate scope name '{name}'", {{"name", name}}},
                      source_, {});
    }
    parent.children.push_back(name);
    Scope child;
    child.name = name;
    child.path = path;
    scopes_.emplace(path, std::move(child));
}

void AssemblyDefinition::add_instance(std::string definition, std::string name, Config config,
                                      const std::string& scope) {
    require_scope(scope).instances.push_back({std::move(definition), std::move(name), std::move(config)});
}

void AssemblyDefinition::remove_instance(const std::string& name, const std::string& scope) {
    Scope& target = require_scope(scope);
    const auto found = std::find_if(target.instances.begin(), target.instances.end(),
                                    [&](const AssemblyInstance& instance) { return instance.name == name; });
    if (found == target.instances.end()) {
        fail_assembly(ErrorCode::invalid_assembly, {scope_join(scope, name), {}},
                      {{engine_text_domain, "assembly.instance.missing"},
                       "Instance not found in scope: {name}", {{"name", name}}},
                      source_, {});
    }
    target.instances.erase(found);
    // 同一作用域内引用该实例的连接、转接与导出随实例一并移除；子作用域与其余实例不变。
    const auto references_instance = [&](const Reference& reference) { return reference.module == name; };
    target.connections.erase(
        std::remove_if(target.connections.begin(), target.connections.end(),
                       [&](const AssemblyConnection& connection) {
                           return references_instance(connection.requirement) ||
                                  references_instance(connection.provider);
                       }),
        target.connections.end());
    target.forwards.erase(std::remove_if(target.forwards.begin(), target.forwards.end(),
                                         [&](const AssemblyForward& forward) {
                                             return references_instance(forward.target);
                                         }),
                          target.forwards.end());
    target.exports.erase(std::remove_if(target.exports.begin(), target.exports.end(),
                                        [&](const AssemblyExport& symbol) {
                                            return references_instance(symbol.target);
                                        }),
                         target.exports.end());
}

void AssemblyDefinition::connect(Reference requirement, Reference provider, const std::string& scope) {
    require_scope(scope).connections.push_back({std::move(requirement), std::move(provider)});
}

bool AssemblyDefinition::disconnect(const Reference& requirement, const std::string& scope) {
    Scope& target = require_scope(scope);
    const auto found = std::find_if(target.connections.begin(), target.connections.end(),
                                    [&](const AssemblyConnection& connection) {
                                        return connection.requirement == requirement;
                                    });
    if (found == target.connections.end()) return false;
    target.connections.erase(found);
    return true;
}

void AssemblyDefinition::forward_inherited(std::string requirement, Reference child_requirement,
                                           const std::string& scope) {
    if (scope.empty()) {
        fail_assembly(ErrorCode::invalid_assembly, {requirement, {}},
                      {{engine_text_domain, "assembly.root.forward"}, "The root scope cannot forward requirements"},
                      source_, {});
    }
    require_scope(scope).forwards.push_back({std::move(requirement), std::move(child_requirement)});
}

void AssemblyDefinition::export_symbol(std::string name, Reference child_symbol, const std::string& scope) {
    if (scope.empty()) {
        fail_assembly(ErrorCode::invalid_assembly, {name, {}},
                      {{engine_text_domain, "assembly.root.export"}, "The root scope cannot export symbols"},
                      source_, {});
    }
    require_scope(scope).exports.push_back({std::move(name), std::move(child_symbol)});
}

std::vector<std::string> AssemblyDefinition::scopes() const {
    std::vector<std::string> result;
    result.reserve(scopes_.size());
    for (const auto& item : scopes_) result.push_back(item.first);
    return result;
}

std::vector<AssemblyInstance> AssemblyDefinition::instances(const std::string& scope) const {
    return require_scope(scope).instances;
}

void AssemblyDefinition::set_instance_config(const std::string& name, Config config, const std::string& scope) {
    Scope& target = require_scope(scope);
    const auto found = std::find_if(target.instances.begin(), target.instances.end(),
                                    [&](const AssemblyInstance& instance) { return instance.name == name; });
    if (found == target.instances.end()) {
        fail_assembly(ErrorCode::invalid_assembly, {scope_join(scope, name), {}},
                      {{engine_text_domain, "assembly.instance.missing"},
                       "Instance not found in scope: {name}", {{"name", name}}},
                      source_, {});
    }
    found->config = std::move(config);
}

std::string AssemblyDefinition::to_json() const {
    return detail::write_json(scope_config(require_scope(""), true), source_);
}

Config AssemblyDefinition::scope_config(const Scope& scope, bool root, const std::string& record,
                                        std::size_t depth) const {
    if (depth >= assembly_json_max_depth) {
        fail_record(ErrorCode::invalid_json, record, {{engine_text_domain, "json.depth.limit"}, "JSON container nesting limit exceeded"});
    }
    std::vector<std::pair<std::string, Config>> members;
    if (root) {
        members.emplace_back("format", Config::string(assembly_format));
        members.emplace_back("version", Config::integer(assembly_format_version));
    } else {
        members.emplace_back("name", Config::string(scope.name));
    }
    std::vector<Config> instances;
    instances.reserve(scope.instances.size());
    for (std::size_t index = 0; index < scope.instances.size(); ++index) {
        const auto& instance = scope.instances[index];
        const auto item = record_join(record_join(record, "instances"), std::to_string(index));
        // 先检查，再复制配置，避免过深配置在编解码检查之前递归复制。
        detail::validate_json(instance.config, source_, record_join(item, "config"), depth + 3);
        instances.push_back(Config::object({{"definition", Config::string(instance.definition)},
                                            {"name", Config::string(instance.name)},
                                            {"config", instance.config}}));
    }
    members.emplace_back("instances", Config::array(std::move(instances)));
    std::vector<Config> scopes;
    scopes.reserve(scope.children.size());
    for (std::size_t index = 0; index < scope.children.size(); ++index) {
        const auto item = record_join(record_join(record, "scopes"), std::to_string(index));
        scopes.push_back(scope_config(require_scope(scope_join(scope.path, scope.children[index])), false,
                                      item, depth + 2));
    }
    members.emplace_back("scopes", Config::array(std::move(scopes)));
    std::vector<Config> connections;
    connections.reserve(scope.connections.size());
    for (const auto& connection : scope.connections) {
        connections.push_back(Config::object({{"requirement", reference_config(connection.requirement, "symbol")},
                                              {"provider", reference_config(connection.provider, "symbol")}}));
    }
    members.emplace_back("connections", Config::array(std::move(connections)));
    std::vector<Config> forwards;
    forwards.reserve(scope.forwards.size());
    for (const auto& forward : scope.forwards) {
        forwards.push_back(Config::object({{"requirement", Config::string(forward.requirement)},
                                           {"target", reference_config(forward.target, "requirement")}}));
    }
    members.emplace_back("forwards", Config::array(std::move(forwards)));
    std::vector<Config> exports;
    exports.reserve(scope.exports.size());
    for (const auto& item : scope.exports) {
        exports.push_back(Config::object({{"name", Config::string(item.name)},
                                          {"target", reference_config(item.target, "symbol")}}));
    }
    members.emplace_back("exports", Config::array(std::move(exports)));
    return Config::object(std::move(members));
}

Config AssemblyDefinition::reference_config(const Reference& reference, const std::string& second) {
    return Config::object({{"module", Config::string(reference.module)}, {second, Config::string(reference.symbol)}});
}

void AssemblyDefinition::save(const std::string& path) const {
    std::string text;
    try {
        text = to_json();
    } catch (const EngineError& error) {
        auto diagnostic = error.diagnostic();
        if (diagnostic.source.empty()) diagnostic.source = path;
        throw EngineError(std::move(diagnostic));
    }
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    if (!stream.is_open()) {
        fail_assembly(ErrorCode::io_failure, {path, {}},
                      {{engine_text_domain, "assembly.io.open_write"}, "Cannot open assembly record for writing"}, path, {});
    }
    stream << text << '\n';
    stream.close();
    if (!stream) {
        fail_assembly(ErrorCode::io_failure, {path, {}},
                      {{engine_text_domain, "assembly.io.write"}, "Cannot write assembly record"}, path, {});
    }
}

AssemblyDefinition AssemblyDefinition::load(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream.is_open()) {
        fail_assembly(ErrorCode::io_failure, {path, {}},
                      {{engine_text_domain, "assembly.io.open_read"}, "Cannot open assembly record for reading"}, path, {});
    }
    std::string text;
    char chunk[4096];
    while (stream) {
        stream.read(chunk, sizeof(chunk));
        text.append(chunk, static_cast<std::size_t>(stream.gcount()));
    }
    // 读取中途失败时流未到达文件结尾；残缺内容不送入解析，与格式错误分开报告。
    if (!stream.eof()) {
        fail_assembly(ErrorCode::io_failure, {path, {}},
                      {{engine_text_domain, "assembly.io.read"}, "Cannot read assembly record"}, path, {});
    }
    return parse(text, path);
}

AssemblyDefinition AssemblyDefinition::parse(const std::string& text, std::string source) {
    Config document = detail::parse_json(text, source);
    AssemblyDefinition definition;
    definition.source_ = std::move(source);
    definition.scopes_.clear();
    if (document.kind() != Config::Kind::object) {
        definition.fail_record(ErrorCode::invalid_assembly, "", {{engine_text_domain, "assembly.record.object_required"}, "Assembly record must be a JSON object"});
    }
    const Config* format = document.find("format");
    if (!format || format->kind() != Config::Kind::string) {
        definition.fail_record(ErrorCode::invalid_assembly, "/format", {{engine_text_domain, "assembly.record.format_type"}, "Record format identifier must be a string"});
    }
    if (format->string() != assembly_format) {
        definition.fail_record(ErrorCode::invalid_assembly, "/format",
                               {{engine_text_domain, "assembly.record.format_unknown"}, "Unknown record format '{format}'",
                                {{"format", format->string()}}});
    }
    const Config* version = document.find("version");
    if (!version || version->kind() != Config::Kind::integer) {
        definition.fail_record(ErrorCode::invalid_assembly, "/version", {{engine_text_domain, "assembly.record.version_type"}, "Record format version must be an integer"});
    }
    if (version->integer() != assembly_format_version) {
        definition.fail_record(ErrorCode::unsupported_format_version, "/version",
                               {{engine_text_domain, "assembly.record.version_unsupported"},
                                "Unsupported assembly record version {found}; this build reads version {expected}",
                                {{"found", std::to_string(version->integer())},
                                 {"expected", std::to_string(assembly_format_version)}}});
    }
    definition.read_scope(document, "", "", true);
    return definition;
}

void AssemblyDefinition::read_scope(const Config& object, const std::string& record,
                                    const std::string& parent_path, bool root) {
    const std::vector<std::string> allowed =
        root ? std::vector<std::string>{"format", "version", "instances", "scopes", "connections", "forwards", "exports"}
             : std::vector<std::string>{"name", "instances", "scopes", "connections", "forwards", "exports"};
    check_keys(object, record, allowed);
    std::string name;
    std::string path = parent_path;
    if (!root) {
        const Config* field = object.find("name");
        if (!field || field->kind() != Config::Kind::string || !valid_scope_name(field->string())) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "name"),
                        {{engine_text_domain, "assembly.scope.invalid_name"},
                         "Scope name must be non-empty and must not contain '/'"});
        }
        name = field->string();
        path = scope_join(parent_path, name);
        if (scopes_.count(path) != 0) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "name"),
                        {{engine_text_domain, "assembly.scope.duplicate"}, "Duplicate scope name '{name}'", {{"name", name}}});
        }
    }
    Scope scope;
    scope.name = name;
    scope.path = path;
    if (const Config* instances = object.find("instances")) {
        if (instances->kind() != Config::Kind::array) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "instances"),
                        {{engine_text_domain, "assembly.field.instances_type"}, "Field 'instances' must be an array"});
        }
        const auto& elements = instances->elements();
        for (std::size_t index = 0; index < elements.size(); ++index) {
            const std::string item = record_join(record_join(record, "instances"), std::to_string(index));
            const Config& element = elements[index];
            if (element.kind() != Config::Kind::object) {
                fail_record(ErrorCode::invalid_assembly, item, {{engine_text_domain, "assembly.instance.object_required"}, "Instance must be an object"});
            }
            check_keys(element, item, {"definition", "name", "config"});
            const Config* definition = element.find("definition");
            if (!definition || definition->kind() != Config::Kind::string) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "definition"),
                            {{engine_text_domain, "assembly.field.definition_type"}, "Field 'definition' must be a string"});
            }
            const Config* instance_name = element.find("name");
            if (!instance_name || instance_name->kind() != Config::Kind::string) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "name"),
                            {{engine_text_domain, "assembly.field.name_type"}, "Field 'name' must be a string"});
            }
            const Config* config = element.find("config");
            scope.instances.push_back({definition->string(), instance_name->string(), config ? *config : Config{}});
        }
    }
    if (const Config* scopes = object.find("scopes")) {
        if (scopes->kind() != Config::Kind::array) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "scopes"), {{engine_text_domain, "assembly.field.scopes_type"}, "Field 'scopes' must be an array"});
        }
        const auto& elements = scopes->elements();
        for (std::size_t index = 0; index < elements.size(); ++index) {
            const std::string item = record_join(record_join(record, "scopes"), std::to_string(index));
            const Config& element = elements[index];
            if (element.kind() != Config::Kind::object) {
                fail_record(ErrorCode::invalid_assembly, item, {{engine_text_domain, "assembly.scope.object_required"}, "Scope must be an object"});
            }
            const Config* child_name = element.find("name");
            if (!child_name || child_name->kind() != Config::Kind::string || !valid_scope_name(child_name->string())) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "name"),
                            {{engine_text_domain, "assembly.scope.invalid_name"}, "Scope name must be non-empty and must not contain '/'"});
            }
            scope.children.push_back(child_name->string());
            read_scope(element, item, path, false);
        }
    }
    if (const Config* connections = object.find("connections")) {
        if (connections->kind() != Config::Kind::array) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "connections"),
                        {{engine_text_domain, "assembly.field.connections_type"}, "Field 'connections' must be an array"});
        }
        const auto& elements = connections->elements();
        for (std::size_t index = 0; index < elements.size(); ++index) {
            const std::string item = record_join(record_join(record, "connections"), std::to_string(index));
            const Config& element = elements[index];
            if (element.kind() != Config::Kind::object) {
                fail_record(ErrorCode::invalid_assembly, item, {{engine_text_domain, "assembly.connection.object_required"}, "Connection must be an object"});
            }
            check_keys(element, item, {"requirement", "provider"});
            const Config* requirement = element.find("requirement");
            if (!requirement) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "requirement"),
                            {{engine_text_domain, "assembly.field.requirement_required"}, "Field 'requirement' is required"});
            }
            const Config* provider = element.find("provider");
            if (!provider) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "provider"),
                            {{engine_text_domain, "assembly.field.provider_required"}, "Field 'provider' is required"});
            }
            scope.connections.push_back({read_reference(*requirement, record_join(item, "requirement"), "symbol"),
                                         read_reference(*provider, record_join(item, "provider"), "symbol")});
        }
    }
    if (const Config* forwards = object.find("forwards")) {
        if (forwards->kind() != Config::Kind::array) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "forwards"),
                        {{engine_text_domain, "assembly.field.forwards_type"}, "Field 'forwards' must be an array"});
        }
        const auto& elements = forwards->elements();
        if (root && !elements.empty()) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "forwards"),
                        {{engine_text_domain, "assembly.root.forward"}, "The root scope cannot forward requirements"});
        }
        for (std::size_t index = 0; index < elements.size(); ++index) {
            const std::string item = record_join(record_join(record, "forwards"), std::to_string(index));
            const Config& element = elements[index];
            if (element.kind() != Config::Kind::object) {
                fail_record(ErrorCode::invalid_assembly, item, {{engine_text_domain, "assembly.forward.object_required"}, "Forward must be an object"});
            }
            check_keys(element, item, {"requirement", "target"});
            const Config* requirement = element.find("requirement");
            if (!requirement || requirement->kind() != Config::Kind::string) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "requirement"),
                            {{engine_text_domain, "assembly.field.requirement_type"}, "Field 'requirement' must be a string"});
            }
            const Config* target = element.find("target");
            if (!target) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "target"), {{engine_text_domain, "assembly.field.target_required"}, "Field 'target' is required"});
            }
            scope.forwards.push_back(
                {requirement->string(), read_reference(*target, record_join(item, "target"), "requirement")});
        }
    }
    if (const Config* exports = object.find("exports")) {
        if (exports->kind() != Config::Kind::array) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "exports"),
                        {{engine_text_domain, "assembly.field.exports_type"}, "Field 'exports' must be an array"});
        }
        const auto& elements = exports->elements();
        if (root && !elements.empty()) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, "exports"),
                        {{engine_text_domain, "assembly.root.export"}, "The root scope cannot export symbols"});
        }
        for (std::size_t index = 0; index < elements.size(); ++index) {
            const std::string item = record_join(record_join(record, "exports"), std::to_string(index));
            const Config& element = elements[index];
            if (element.kind() != Config::Kind::object) {
                fail_record(ErrorCode::invalid_assembly, item, {{engine_text_domain, "assembly.export.object_required"}, "Export must be an object"});
            }
            check_keys(element, item, {"name", "target"});
            const Config* name_field = element.find("name");
            if (!name_field || name_field->kind() != Config::Kind::string) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "name"), {{engine_text_domain, "assembly.field.name_type"}, "Field 'name' must be a string"});
            }
            const Config* target = element.find("target");
            if (!target) {
                fail_record(ErrorCode::invalid_assembly, record_join(item, "target"), {{engine_text_domain, "assembly.field.target_required"}, "Field 'target' is required"});
            }
            scope.exports.push_back({name_field->string(), read_reference(*target, record_join(item, "target"), "symbol")});
        }
    }
    scopes_.emplace(path, std::move(scope));
}

Reference AssemblyDefinition::read_reference(const Config& value, const std::string& record,
                                             const std::string& second) const {
    if (value.kind() != Config::Kind::object) {
        fail_record(ErrorCode::invalid_assembly, record, {{engine_text_domain, "assembly.reference.object_required"}, "Reference must be an object"});
    }
    check_keys(value, record, {"module", second});
    const Config* module = value.find("module");
    if (!module || module->kind() != Config::Kind::string) {
        fail_record(ErrorCode::invalid_assembly, record_join(record, "module"), {{engine_text_domain, "assembly.field.module_type"}, "Field 'module' must be a string"});
    }
    const Config* symbol = value.find(second);
    if (!symbol || symbol->kind() != Config::Kind::string) {
        fail_record(ErrorCode::invalid_assembly, record_join(record, second),
                    {{engine_text_domain, "assembly.field.reference_symbol_type"}, "Field '{field}' must be a string",
                     {{"field", second}}});
    }
    return {module->string(), symbol->string()};
}

void AssemblyDefinition::check_keys(const Config& object, const std::string& record,
                                    const std::vector<std::string>& allowed) const {
    for (const auto& member : object.members()) {
        if (std::find(allowed.begin(), allowed.end(), member.first) == allowed.end()) {
            fail_record(ErrorCode::invalid_assembly, record_join(record, member.first),
                        {{engine_text_domain, "assembly.field.unknown"}, "Unknown field '{field}'", {{"field", member.first}}});
        }
    }
}

void AssemblyDefinition::fail_record(ErrorCode code, const std::string& record, TextRef message) const {
    fail_assembly(code, {}, std::move(message), source_, record);
}

EngineError AssemblyDefinition::attach(const EngineError& error, const std::string& record,
                                       const std::string& scope) const {
    Diagnostic diagnostic = error.diagnostic();
    if (diagnostic.source.empty()) diagnostic.source = source_;
    if (diagnostic.path.empty()) diagnostic.path = record;
    if (!scope.empty()) {
        diagnostic.target.module = diagnostic.target.module.empty()
                                       ? scope
                                       : scope_join(scope, diagnostic.target.module);
    }
    return EngineError(std::move(diagnostic));
}

Module AssemblyDefinition::create_module(const ModuleFactoryDirectory& factories, const AssemblyInstance& instance,
                                         const std::string& record, const std::string& engine_path) const {
    try {
        Module module = factories.create(instance.definition, instance.name, instance.config);
        module.source_ = {source_, record};
        return module;
    } catch (const EngineError& error) {
        Diagnostic diagnostic = error.diagnostic();
        if (diagnostic.path.empty()) {
            if (diagnostic.cause) {
                // 作者提供的来源信息保留在 cause，外层关联本次装配的工厂实例。
                diagnostic.path = record;
            } else if (diagnostic.code == ErrorCode::unknown_definition) {
                diagnostic.path = record_join(record, "definition");
            } else if (diagnostic.code == ErrorCode::invalid_config) {
                diagnostic.path = record_join(record, "config");
            } else {
                diagnostic.path = record;
            }
        }
        throw attach(EngineError(std::move(diagnostic)), record, engine_path);
    }
}

Module AssemblyDefinition::build_scope(const ModuleFactoryDirectory& factories, const Scope& scope,
                                       const std::string& record, const std::string& engine_path) const {
    Module module(scope.name);
    // 记录作用域只组织子模块，本身没有运行状态；子模块仍独立声明状态能力。
    module.declare_stateless();
    module.set_source(source_, record);
    for (std::size_t index = 0; index < scope.instances.size(); ++index) {
        const std::string item = record_join(record_join(record, "instances"), std::to_string(index));
        Module child = create_module(factories, scope.instances[index], item, engine_path);
        try {
            module.add(std::move(child));
        } catch (const EngineError& error) {
            throw attach(error, item, engine_path);
        }
    }
    for (std::size_t index = 0; index < scope.children.size(); ++index) {
        const std::string item = record_join(record_join(record, "scopes"), std::to_string(index));
        const std::string child_path = scope_join(engine_path, scope.children[index]);
        Module child = build_scope(factories, require_scope(scope_join(scope.path, scope.children[index])), item,
                                   child_path);
        try {
            module.add(std::move(child));
        } catch (const EngineError& error) {
            throw attach(error, item, engine_path);
        }
    }
    for (std::size_t index = 0; index < scope.connections.size(); ++index) {
        const std::string item = record_join(record_join(record, "connections"), std::to_string(index));
        try {
            module.connect(scope.connections[index].requirement, scope.connections[index].provider);
            module.set_connection_source(scope.connections[index].requirement, source_, item);
        } catch (const EngineError& error) {
            throw attach(error, item, engine_path);
        }
    }
    for (std::size_t index = 0; index < scope.forwards.size(); ++index) {
        const std::string item = record_join(record_join(record, "forwards"), std::to_string(index));
        try {
            module.forward_inherited(scope.forwards[index].requirement, scope.forwards[index].target);
            module.set_connection_source(scope.forwards[index].target, source_, item);
            module.set_requirement_source(scope.forwards[index].requirement, source_, item);
        } catch (const EngineError& error) {
            throw attach(error, item, engine_path);
        }
    }
    for (std::size_t index = 0; index < scope.exports.size(); ++index) {
        const std::string item = record_join(record_join(record, "exports"), std::to_string(index));
        try {
            module.export_symbol(scope.exports[index].name, scope.exports[index].target);
            module.set_export_source(scope.exports[index].name, source_, item);
        } catch (const EngineError& error) {
            Diagnostic diagnostic = error.diagnostic();
            if (diagnostic.target.module == module.name()) {
                // 导出名错误以作用域自身为目标；子符号错误保留直接子模块定位。
                diagnostic.target = {engine_path, diagnostic.target.symbol};
                throw attach(EngineError(std::move(diagnostic)), item, "");
            }
            throw attach(error, item, engine_path);
        }
    }
    return module;
}

Engine AssemblyDefinition::instantiate(const ModuleFactoryDirectory& factories) const {
    const Scope& root = require_scope("");
    Engine engine;
    engine.root_.set_source(source_, "");
    for (std::size_t index = 0; index < root.instances.size(); ++index) {
        const std::string record = record_join("/instances", std::to_string(index));
        Module module = create_module(factories, root.instances[index], record, "");
        try {
            engine.add(std::move(module));
        } catch (const EngineError& error) {
            throw attach(error, record, "");
        }
    }
    for (std::size_t index = 0; index < root.children.size(); ++index) {
        const std::string record = record_join("/scopes", std::to_string(index));
        const Scope& scope = require_scope(root.children[index]);
        Module module = build_scope(factories, scope, record, scope.name);
        try {
            engine.add(std::move(module));
        } catch (const EngineError& error) {
            throw attach(error, record, "");
        }
    }
    for (std::size_t index = 0; index < root.connections.size(); ++index) {
        const std::string record = record_join("/connections", std::to_string(index));
        try {
            engine.connect(root.connections[index].requirement, root.connections[index].provider);
            engine.root_.set_connection_source(root.connections[index].requirement, source_, record);
        } catch (const EngineError& error) {
            throw attach(error, record, "");
        }
    }
    return engine;
}

}  // namespace ascend
