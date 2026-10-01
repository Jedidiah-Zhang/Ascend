#include <ascend/engine.hpp>
#include <ascend/text.hpp>

#include "engine_internal.hpp"

#include <algorithm>
#include <optional>
#include <set>

namespace ascend {
namespace {

std::string path_join(const std::string& parent, const std::string& name) {
    return parent.empty() ? name : parent + '/' + name;
}

std::string parent_path(const std::string& path) {
    const auto slash = path.rfind('/');
    return slash == std::string::npos ? std::string{} : path.substr(0, slash);
}

std::string local_name(const std::string& path) {
    const auto slash = path.rfind('/');
    return slash == std::string::npos ? path : path.substr(slash + 1);
}

std::string label(const Reference& reference) {
    return path_join(reference.module, reference.symbol);
}

void local_reference(const Reference& reference) {
    if (reference.module.empty() || reference.module.find('/') != std::string::npos ||
        reference.symbol.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, reference,
                     {{engine_text_domain, "engine.reference.local_only"},
                      "Reference must name a direct child and a non-empty interface", {}});
    }
}

using Mismatch = std::optional<std::pair<ErrorCode, TextRef>>;

Mismatch signature_mismatch(const Declaration& declaration, SymbolKind kind,
                            std::type_index result, const std::vector<std::type_index>& parameters) {
    if (declaration.kind != kind) {
        return {{ErrorCode::wrong_kind, {{engine_text_domain, "engine.symbol.kind_mismatch"}, "Symbol kind does not match", {}}}};
    }
    if (declaration.result_type != result) {
        return {{ErrorCode::type_mismatch,
                 {{engine_text_domain, "engine.symbol.result_type_mismatch"}, "Result type does not match", {}}}};
    }
    if (declaration.parameters.size() != parameters.size()) {
        return {{ErrorCode::argument_count,
                 {{engine_text_domain, "engine.symbol.parameter_count_mismatch"}, "Parameter count does not match", {}}}};
    }
    for (std::size_t i = 0; i < parameters.size(); ++i) {
        if (declaration.parameters[i].type != parameters[i]) {
            return {{ErrorCode::type_mismatch,
                     {{engine_text_domain, "engine.symbol.parameter_type_mismatch"},
                      "Type mismatch for parameter '{parameter}'",
                      {{"parameter", declaration.parameters[i].name}}}}};
        }
    }
    return {};
}

Mismatch requirement_mismatch(const Declaration& declaration, const Requirement& requirement) {
    if (auto mismatch = signature_mismatch(declaration, requirement.kind, requirement.result_type,
                                           requirement.parameters)) return mismatch;
    if (declaration.contract != requirement.contract) {
        return {{ErrorCode::contract_mismatch,
                 {{engine_text_domain, "engine.symbol.contract_mismatch"}, "Expected contract '{expected}', received '{received}'",
                  {{"expected", requirement.contract}, {"received", declaration.contract}}}}};
    }
    return {};
}

// 公开项登记前的声明校验；重复符号由登记入口单独核对。
void validate_declaration(const Declaration& declaration) {
    if (declaration.reference.symbol.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, declaration.reference,
                     {{engine_text_domain, "engine.declaration.empty_symbol"}, "Symbol name must not be empty", {}});
    }
    std::set<std::string> names;
    for (const auto& parameter : declaration.parameters) {
        if (parameter.name.empty() || !names.insert(parameter.name).second) {
            detail::fail_text(ErrorCode::invalid_declaration, declaration.reference,
                         {{engine_text_domain, "engine.declaration.parameter_names"},
                          "Parameter names must be non-empty and unique", {}});
        }
    }
    for (const auto* dependencies : {&declaration.reads, &declaration.writes}) {
        for (const auto& reference : *dependencies) {
            if (reference.module.empty() || reference.symbol.empty()) {
                detail::fail_text(ErrorCode::invalid_declaration, declaration.reference,
                             {{engine_text_domain, "engine.declaration.dependency_reference"},
                              "Dependency references must name a module and a symbol", {}});
            }
        }
    }
}

}  // namespace

// 来源、记录内路径与引擎目标均可出现；空部分不产生多余分隔。
std::string render_diagnostic(const Diagnostic& diagnostic, const TextCatalog* catalog,
                              const std::string& locale) {
    std::string result;
    for (const Diagnostic* current = &diagnostic; current; current = current->cause.get()) {
        if (current != &diagnostic) result += render_text(
            {{engine_text_domain, "engine.diagnostic.caused_by"}, "\nCaused by: "}, catalog, locale);
        std::string location = current->source;
        if (!current->path.empty()) {
            if (!location.empty()) location += ' ';
            location += current->path;
        }
        const std::string target = label(current->target);
        if (!target.empty()) {
            if (!location.empty()) location += ' ';
            location += target;
        }
        if (!location.empty()) result += location + ": ";
        result += render_text(current->text, catalog, locale);
    }
    return result;
}

EngineError::EngineError(Diagnostic diagnostic)
    : std::runtime_error(render_diagnostic(diagnostic)), diagnostic_(std::move(diagnostic)) {}

namespace detail {

void fail(ErrorCode code, const Reference& target, std::string message) {
    throw EngineError({code, target, std::move(message)});
}

void fail_text(ErrorCode code, const Reference& target, TextRef text) {
    throw EngineError({code, target, std::move(text)});
}

Diagnostic wrap_diagnostic(ErrorCode code, const Reference& target, TextRef text, const Diagnostic& cause) {
    return {code, target, std::move(text), {}, {}, std::make_shared<const Diagnostic>(cause)};
}

void rethrow_boundary(FailureBoundary boundary, const Reference& target) {
    ErrorCode code = ErrorCode::execution_failed;
    const char* key = nullptr;
    const char* failed = nullptr;
    const char* non_standard_key = nullptr;
    const char* non_standard = nullptr;
    switch (boundary) {
        case FailureBoundary::getter:
            key = "engine.execution.getter_failed"; failed = "Value getter failed";
            non_standard_key = "engine.execution.getter_non_standard";
            non_standard = "Getter threw a non-standard exception"; break;
        case FailureBoundary::method:
            key = "engine.execution.method_failed"; failed = "Method execution failed";
            non_standard_key = "engine.execution.method_non_standard";
            non_standard = "Method threw a non-standard exception"; break;
        case FailureBoundary::validation:
            code = ErrorCode::validation_failed;
            key = "engine.validation.failed"; failed = "Configuration validation failed";
            non_standard_key = "engine.validation.non_standard";
            non_standard = "Validator threw a non-standard exception"; break;
        case FailureBoundary::transport:
            key = "engine.transport.failed"; failed = "Value transport failed";
            non_standard_key = "engine.transport.non_standard";
            non_standard = "Value transport threw a non-standard exception"; break;
        case FailureBoundary::state_capture:
            code = ErrorCode::invalid_state;
            key = "engine.state.capture_failed"; failed = "State capture failed";
            non_standard_key = "engine.state.capture_non_standard";
            non_standard = "State capture threw a non-standard exception"; break;
        case FailureBoundary::state_restore:
            code = ErrorCode::invalid_state;
            key = "engine.state.restore_failed"; failed = "State restore failed";
            non_standard_key = "engine.state.restore_non_standard";
            non_standard = "State restore threw a non-standard exception"; break;
    }
    try { throw; }
    catch (const EngineError& error) {
        throw EngineError(wrap_diagnostic(code, target, {{engine_text_domain, key}, failed}, error.diagnostic()));
    } catch (const std::exception& error) {
        fail(code, target, error.what());
    } catch (...) {
        fail_text(code, target, {{engine_text_domain, non_standard_key}, non_standard});
    }
}

}  // namespace detail

// 构建局部结果，验证完成前不发布任何绑定。不执行模块回调或激活逻辑。
// 只遍历模块结构；Entry 以擦除形式存储，这里在内部还原。
class AssemblyBuilder {
public:
    explicit AssemblyBuilder(const Module& root) { collect(root, {}); }

    std::shared_ptr<detail::Runtime> build() {
        for (const auto& node : nodes_) {
            for (const auto& item : node.second->entries_) {
                auto entry = std::make_shared<detail::Entry>(
                    *static_cast<const detail::Entry*>(item.second.get()));
                entry->declaration.reference.module = node.first;
                concrete_.emplace(entry->declaration.reference, entry);
                runtime_->outputs.emplace(entry->declaration.reference, entry);
            }
        }
        for (const auto& node : nodes_) {
            // 合成根不承载用户状态；其余模块逐一登记声明信息，缺失留待捕获时报告。
            if (node.first.empty()) continue;
            auto entry = std::make_shared<detail::StateEntry>();
            if (node.second->state_) {
                entry->declared = true;
                entry->contract = node.second->state_->contract;
                entry->stateless = node.second->state_->stateless;
                entry->capture = node.second->state_->capture;
                entry->restore = node.second->state_->restore;
            }
            runtime_->states.emplace(node.first, std::move(entry));
        }
        for (const auto& node : nodes_) {
            for (const auto& item : node.second->exports_) {
                const Reference target{node.first, item.first};
                if (!output(target)) {
                    report(ErrorCode::missing_symbol, target,
                           {{engine_text_domain, "engine.export.target_missing"}, "Export target not found: {target}",
                            {{"target", label({path_join(node.first, item.second.module), item.second.symbol})}}},
                           source(node.first, node.second->export_sources_, item.first));
                }
            }
            for (const auto& item : node.second->connections_) {
                const Reference target{path_join(node.first, item.first.module), item.first.symbol};
                if (!requirement(target)) {
                    report(ErrorCode::missing_requirement, target,
                           {{engine_text_domain, "engine.connection.requirement_missing"},
                            "Connection names an undeclared requirement", {}},
                           source(node.first, node.second->connection_sources_, item.first));
                }
            }
        }
        for (const auto& node : nodes_) {
            for (const auto& item : node.second->requirements_) {
                input({node.first, item.first});
            }
        }
        for (const auto& item : concrete_) {
            auto& declaration = item.second->declaration;
            for (auto* dependencies : {&declaration.reads, &declaration.writes}) {
                for (auto& reference : *dependencies) {
                    // 声明读写只可指向所在父组合的直接子模块公开量。
                    const Reference absolute{path_join(parent_path(item.first.module), reference.module), reference.symbol};
                    const auto target = reference.module.find('/') == std::string::npos ? output(absolute) : nullptr;
                    if (!target) {
                        report(ErrorCode::missing_symbol, item.first,
                               {{engine_text_domain, "engine.dependency.unresolved"}, "Unresolved dependency: {target}",
                                {{"target", label(absolute)}}},
                               source(item.first.module));
                    } else if (target->declaration.kind != SymbolKind::value) {
                        report(ErrorCode::wrong_kind, item.first,
                               {{engine_text_domain, "engine.dependency.not_a_value"},
                                "Read/write dependency must be a value: {target}",
                                {{"target", label(absolute)}}},
                               source(item.first.module));
                    }
                    reference = absolute;
                }
            }
        }
        std::stable_sort(diagnostics.begin(), diagnostics.end(), [](const Diagnostic& a, const Diagnostic& b) {
            return a.target < b.target;
        });
        return runtime_;
    }

    std::vector<Diagnostic> diagnostics;

private:
    Module::Location source(std::string path) const {
        // 工厂内部声明没有记录字段时，回退到最近的实例／作用域。
        while (true) {
            const auto node = nodes_.find(path);
            if (node != nodes_.end()) {
                const auto& location = node->second->source_;
                if (!location.source.empty() || !location.path.empty()) return location;
            }
            if (path.empty()) return {};
            path = parent_path(path);
        }
    }

    template <class Key>
    Module::Location source(const std::string& path, const std::map<Key, Module::Location>& locations,
                            const Key& key) const {
        const auto found = locations.find(key);
        return found == locations.end() ? source(path) : found->second;
    }

    void report(ErrorCode code, const Reference& target, TextRef message, const Module::Location& location) {
        diagnostics.push_back({code, target, std::move(message), location.source, location.path});
    }

    void collect(const Module& module, const std::string& path) {
        nodes_.emplace(path, &module);
        for (const auto& child : module.children_) collect(child, path_join(path, child.name_));
    }

    std::shared_ptr<const detail::Entry> output(const Reference& reference) {
        if (auto found = runtime_->outputs.find(reference); found != runtime_->outputs.end()) return found->second;
        if (!visited_outputs_.insert(reference).second) return {};
        const auto node = nodes_.find(reference.module);
        if (node == nodes_.end()) return {};
        const auto alias = node->second->exports_.find(reference.symbol);
        if (alias == node->second->exports_.end()) return {};
        // export_symbol 只接受直接子模块，因此解析始终向组合树下方前进。
        const auto target = output({path_join(reference.module, alias->second.module), alias->second.symbol});
        if (target) runtime_->outputs.emplace(reference, target);
        return target;
    }

    std::shared_ptr<const Requirement> requirement(const Reference& reference) const {
        const auto node = nodes_.find(reference.module);
        if (node == nodes_.end()) return {};
        const auto found = node->second->requirements_.find(reference.symbol);
        return found == node->second->requirements_.end() ? nullptr : found->second;
    }

    std::shared_ptr<const detail::Entry> input(const Reference& reference) {
        if (auto found = runtime_->inputs.find(reference); found != runtime_->inputs.end()) return found->second.provider;
        if (!visited_inputs_.insert(reference).second) return {};
        const auto needed = requirement(reference);
        if (!needed) return {};
        const auto parent = parent_path(reference.module);
        const auto& connections = nodes_.at(parent)->connections_;
        const auto found = connections.find({local_name(reference.module), reference.symbol});
        if (found == connections.end()) {
            report(ErrorCode::unconnected_requirement, reference,
                   {{engine_text_domain, "engine.requirement.unconnected"}, "Required interface is not connected", {}},
                   source(reference.module, nodes_.at(reference.module)->requirement_sources_, reference.symbol));
            return {};
        }
        const auto location = source(parent, nodes_.at(parent)->connection_sources_, found->first);
        const auto& connection = found->second;
        const Reference provider{connection.forwarded ? parent : path_join(parent, connection.provider.module),
                                 connection.provider.symbol};
        std::shared_ptr<const detail::Entry> resolved;
        if (connection.forwarded) {
            const auto parent_requirement = requirement(provider);
            if (!parent_requirement) {
                report(ErrorCode::missing_requirement, reference,
                       {{engine_text_domain, "engine.forward.target_missing"}, "Forwarded requirement not declared: {target}",
                        {{"target", label(provider)}}},
                       location);
                return {};
            }
            // 核对声明本身，避免实际提供方缺失时掩盖转接契约错误。
            Declaration signature{provider, parent_requirement->kind, parent_requirement->result_type,
                                  {}, {}, {}, {}, parent_requirement->contract};
            for (const auto& type : parent_requirement->parameters) signature.parameters.push_back({{}, type});
            if (auto mismatch = requirement_mismatch(signature, *needed)) {
                report(mismatch->first, reference,
                       {{engine_text_domain, "engine.forward.mismatch"}, "Forward from {provider}: {reason}",
                        {{"provider", label(provider)}, {"reason", mismatch->second}}},
                       location);
                return {};
            }
            resolved = input(provider);
            if (!resolved) return {};
        } else {
            resolved = output(provider);
            if (!resolved) {
                report(ErrorCode::missing_symbol, reference,
                       {{engine_text_domain, "engine.connection.provider_missing"}, "Provider not found: {target}",
                        {{"target", label(provider)}}},
                       location);
                return {};
            }
        }
        if (auto mismatch = requirement_mismatch(resolved->declaration, *needed)) {
            report(mismatch->first, reference,
                   {{engine_text_domain, "engine.connection.provider_mismatch"}, "Provider {provider}: {reason}",
                    {{"provider", label(provider)}, {"reason", mismatch->second}}},
                   location);
            return {};
        }
        runtime_->inputs.emplace(reference, detail::Input{needed, resolved});
        return resolved;
    }

    std::map<std::string, const Module*> nodes_;
    std::map<Reference, std::shared_ptr<detail::Entry>> concrete_;
    std::set<Reference> visited_outputs_;
    std::set<Reference> visited_inputs_;
    std::shared_ptr<detail::Runtime> runtime_ = std::make_shared<detail::Runtime>();
};

BindingHandle::BindingHandle(std::shared_ptr<const void> entry, std::shared_ptr<const void> runtime,
                             Reference reference)
    : entry_(std::move(entry)), runtime_(std::move(runtime)), reference_(std::move(reference)) {}

std::any BindingHandle::read() const {
    const auto* entry = static_cast<const detail::Entry*>(entry_.get());
    const auto& declaration = entry->declaration;
    if (declaration.kind != SymbolKind::value) {
        detail::fail_text(ErrorCode::wrong_kind, declaration.reference,
             {{engine_text_domain, "engine.execution.expected_value"}, "Expected a public value", {}});
    }
    const auto runtime = std::static_pointer_cast<const detail::Runtime>(runtime_);
    try {
        return entry->getter(Context(runtime, declaration.reference.module));
    } catch (...) {
        detail::rethrow_boundary(detail::FailureBoundary::getter, declaration.reference);
    }
}

std::any BindingHandle::call(const std::vector<std::any>& arguments) const {
    const auto* entry = static_cast<const detail::Entry*>(entry_.get());
    const auto& declaration = entry->declaration;
    if (declaration.kind != SymbolKind::method) {
        detail::fail_text(ErrorCode::wrong_kind, declaration.reference,
             {{engine_text_domain, "engine.execution.expected_method"}, "Expected a public method", {}});
    }
    if (arguments.size() != declaration.parameters.size()) {
        detail::fail_text(ErrorCode::argument_count, declaration.reference,
             {{engine_text_domain, "engine.execution.argument_count"}, "Expected {expected} arguments, received {received}",
              {{"expected", std::to_string(declaration.parameters.size())},
               {"received", std::to_string(arguments.size())}}});
    }
    for (std::size_t index = 0; index < arguments.size(); ++index) {
        if (std::type_index(arguments[index].type()) != declaration.parameters[index].type) {
            detail::fail_text(ErrorCode::type_mismatch, declaration.reference,
                 {{engine_text_domain, "engine.execution.parameter_type_mismatch"}, "Type mismatch for parameter '{parameter}'",
                  {{"parameter", declaration.parameters[index].name}}});
        }
    }
    const auto runtime = std::static_pointer_cast<const detail::Runtime>(runtime_);
    try {
        return entry->method(Context(runtime, declaration.reference.module), arguments);
    } catch (...) {
        detail::rethrow_boundary(detail::FailureBoundary::method, declaration.reference);
    }
}

void BindingHandle::report_transport_failure() const {
    detail::rethrow_boundary(detail::FailureBoundary::transport, reference_);
}

BindingHandle Context::resolve(const std::shared_ptr<const Requirement>& requirement) const {
    const Reference target{module_, requirement->reference.symbol};
    const auto runtime = runtime_.lock();
    if (!runtime) {
        detail::fail_text(ErrorCode::execution_failed, target,
                     {{engine_text_domain, "engine.context.expired"}, "Assembly runtime has expired", {}});
    }
    const auto* state = static_cast<const detail::Runtime*>(runtime.get());
    const auto found = state->inputs.find(target);
    if (found == state->inputs.end() || found->second.requirement != requirement) {
        detail::fail_text(ErrorCode::missing_requirement, target,
                     {{engine_text_domain, "engine.context.foreign_requirement"},
                      "Requirement does not belong to this module context", {}});
    }
    return BindingHandle{found->second.provider, runtime, target};
}

Module::Module(std::string name, std::function<void()> validate)
    : name_(std::move(name)), validate_(std::move(validate)) {
    if (name_.empty() || name_.find('/') != std::string::npos) {
        detail::fail_text(ErrorCode::invalid_declaration, {name_, {}},
                     {{engine_text_domain, "engine.declaration.module_name"},
                      "Module name must be non-empty and must not contain '/'", {}});
    }
}

void Module::add_value_entry(Declaration declaration, std::function<std::any(const Context&)> getter) {
    if (!getter) {
        detail::fail_text(ErrorCode::invalid_declaration, declaration.reference,
                     {{engine_text_domain, "engine.declaration.missing_getter"}, "Missing value getter", {}});
    }
    validate_declaration(declaration);
    auto entry = std::make_shared<const detail::Entry>(
        detail::Entry{std::move(declaration), std::move(getter), {}});
    const auto& reference = entry->declaration.reference;
    if (exports_.count(reference.symbol) || !entries_.emplace(reference.symbol, entry).second) {
        detail::fail_text(ErrorCode::duplicate_symbol, reference,
                     {{engine_text_domain, "engine.declaration.duplicate_symbol"}, "Duplicate public symbol", {}});
    }
}

void Module::add_method_entry(Declaration declaration, std::vector<std::string> names,
                              std::vector<std::type_index> types,
                              std::function<std::any(const Context&, const std::vector<std::any>&)> method) {
    if (!method) {
        detail::fail_text(ErrorCode::invalid_declaration, declaration.reference,
                     {{engine_text_domain, "engine.declaration.missing_method"}, "Missing method implementation", {}});
    }
    if (names.size() != types.size()) {
        detail::fail_text(ErrorCode::invalid_declaration, declaration.reference,
                     {{engine_text_domain, "engine.declaration.parameter_count"},
                      "Parameter names do not match the declared signature", {}});
    }
    declaration.parameters.clear();
    for (std::size_t index = 0; index < names.size(); ++index) {
        declaration.parameters.push_back({std::move(names[index]), types[index]});
    }
    validate_declaration(declaration);
    auto entry = std::make_shared<const detail::Entry>(
        detail::Entry{std::move(declaration), {}, std::move(method)});
    const auto& reference = entry->declaration.reference;
    if (exports_.count(reference.symbol) || !entries_.emplace(reference.symbol, entry).second) {
        detail::fail_text(ErrorCode::duplicate_symbol, reference,
                     {{engine_text_domain, "engine.declaration.duplicate_symbol"}, "Duplicate public symbol", {}});
    }
}

void Module::add_requirement(std::shared_ptr<const Requirement> requirement) {
    const auto& reference = requirement->reference;
    if (reference.symbol.empty() || requirement->contract.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, reference,
                     {{engine_text_domain, "engine.requirement.empty_name_or_contract"},
                      "Requirement name and contract must be non-empty", {}});
    }
    if (!requirements_.emplace(reference.symbol, requirement).second) {
        detail::fail_text(ErrorCode::duplicate_requirement, reference,
                     {{engine_text_domain, "engine.requirement.duplicate"}, "Duplicate requirement", {}});
    }
}

void Module::set_source(std::string source, std::string path) {
    source_ = {std::move(source), std::move(path)};
}

void Module::set_requirement_source(const std::string& symbol, std::string source, std::string path) {
    requirement_sources_[symbol] = {std::move(source), std::move(path)};
}

void Module::set_connection_source(const Reference& requirement, std::string source, std::string path) {
    connection_sources_[requirement] = {std::move(source), std::move(path)};
}

void Module::set_export_source(const std::string& name, std::string source, std::string path) {
    export_sources_[name] = {std::move(source), std::move(path)};
}

void Module::add(Module child) {
    if (child.name_.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, {child.name_, {}},
                     {{engine_text_domain, "engine.module.empty_child_name"}, "Empty child name", {}});
    }
    for (const auto& existing : children_) {
        if (existing.name_ == child.name_) {
            detail::fail_text(ErrorCode::duplicate_module, {child.name_, {}},
                         {{engine_text_domain, "engine.module.duplicate"}, "Duplicate module in this scope", {}});
        }
    }
    children_.push_back(std::move(child));
}

void Module::add_connection(Connection connection) {
    local_reference(connection.requirement);
    if (!connection.forwarded) local_reference(connection.provider);
    else if (connection.provider.symbol.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, connection.requirement,
                     {{engine_text_domain, "engine.connection.forward_empty_name"}, "Forwarded requirement name is empty", {}});
    }
    if (!connections_.emplace(connection.requirement, connection).second) {
        detail::fail_text(ErrorCode::duplicate_connection, connection.requirement,
                     {{engine_text_domain, "engine.connection.duplicate"}, "Requirement already has a connection", {}});
    }
    connection_sources_.erase(connection.requirement);
}

void Module::connect(Reference requirement, Reference provider) {
    add_connection({std::move(requirement), std::move(provider), false});
}

void Module::disconnect(const Reference& requirement) {
    local_reference(requirement);
    connections_.erase(requirement);
    connection_sources_.erase(requirement);
}

void Module::forward(std::string requirement, Reference child_requirement) {
    add_connection({std::move(child_requirement), {{}, std::move(requirement)}, true});
}

void Module::forward_inherited(std::string requirement, Reference child_requirement) {
    bool inserted = false;
    if (!requirement.empty() && requirements_.count(requirement) == 0) {
        const auto child = std::find_if(children_.begin(), children_.end(), [&](const Module& item) {
            return item.name_ == child_requirement.module;
        });
        if (child != children_.end()) {
            const auto found = child->requirements_.find(child_requirement.symbol);
            if (found != child->requirements_.end()) {
                Requirement inherited = *found->second;
                inherited.reference = {name_, requirement};
                add_requirement(std::make_shared<const Requirement>(std::move(inherited)));
                inserted = true;
            }
        }
    }
    try {
        forward(requirement, std::move(child_requirement));
    } catch (...) {
        if (inserted) requirements_.erase(requirement);
        throw;
    }
}

void Module::export_symbol(std::string name, Reference child_symbol) {
    local_reference(child_symbol);
    const Reference target{name_, name};
    if (name.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                     {{engine_text_domain, "engine.export.empty_name"}, "Export name is empty", {}});
    }
    if (entries_.count(name) || !exports_.emplace(name, std::move(child_symbol)).second) {
        detail::fail_text(ErrorCode::duplicate_symbol, target,
                     {{engine_text_domain, "engine.export.duplicate_name"}, "Duplicate public symbol", {}});
    }
}

void Module::add_state(std::string contract, std::function<Config()> capture,
                       std::function<void(const Config&)> restore) {
    const Reference target{name_, {}};
    if (state_) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                     {{engine_text_domain, "engine.state.duplicate_declaration"},
                      "Module already declares a state capability", {}});
    }
    if (contract.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                     {{engine_text_domain, "engine.state.empty_contract"},
                      "State contract must not be empty", {}});
    }
    if (!capture || !restore) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                     {{engine_text_domain, "engine.state.missing_callbacks"},
                      "State capture and restore callbacks are required", {}});
    }
    state_ = StateDeclaration{std::move(contract), false, std::move(capture), std::move(restore)};
}

void Module::declare_stateless() {
    const Reference target{name_, {}};
    if (state_) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                     {{engine_text_domain, "engine.state.duplicate_declaration"},
                      "Module already declares a state capability", {}});
    }
    state_ = StateDeclaration{{}, true, {}, {}};
}

void Engine::invalidate_draft() {
    draft_ready_ = false;
    draft_runtime_.reset();
    draft_diagnostics_.clear();
}

void Engine::build_draft() const {
    if (draft_ready_) return;
    AssemblyBuilder builder(root_);
    draft_runtime_ = builder.build();
    draft_diagnostics_ = std::move(builder.diagnostics);
    draft_ready_ = true;
}

const Module& Engine::find_scope(const std::string& scope) const {
    const Module* current = &root_;
    if (scope.empty()) return *current;
    std::size_t begin = 0;
    while (true) {
        const auto end = scope.find('/', begin);
        const auto name = scope.substr(begin, end == std::string::npos ? end : end - begin);
        const auto child = std::find_if(current->children_.begin(), current->children_.end(),
                                       [&](const Module& item) { return item.name_ == name; });
        if (child == current->children_.end()) {
            detail::fail_text(ErrorCode::missing_module, {scope, {}},
                         {{engine_text_domain, "engine.scope.missing"}, "Assembly scope not found", {}});
        }
        current = &*child;
        if (end == std::string::npos) return *current;
        begin = end + 1;
    }
}

Module& Engine::find_scope(const std::string& scope) {
    return const_cast<Module&>(std::as_const(*this).find_scope(scope));
}

void Engine::add(Module module, const std::string& scope) {
    const Reference target{path_join(scope, module.name_), {}};
    if (runtime_) {
        detail::fail_text(ErrorCode::registration_closed, target,
                     {{engine_text_domain, "engine.registration.closed"}, "Registration is closed", {}});
    }
    invalidate_draft();
    if (module.name_.empty()) {
        detail::fail_text(ErrorCode::invalid_declaration, target,
                     {{engine_text_domain, "engine.module.name_empty"}, "Module name must not be empty", {}});
    }
    for (const auto& child : find_scope(scope).children_) {
        if (child.name_ == module.name_) {
            detail::fail_text(ErrorCode::duplicate_module, target,
                         {{engine_text_domain, "engine.module.duplicate_name"}, "Duplicate module name", {}});
        }
    }
    const auto validate = [&](const auto& self, const Module& node, const std::string& path) -> void {
        if (node.validate_) {
            try {
                node.validate_();
            } catch (...) {
                detail::rethrow_boundary(detail::FailureBoundary::validation, {path, {}});
            }
        }
        for (const auto& child : node.children_) self(self, child, path_join(path, child.name_));
    };
    validate(validate, module, target.module);
    // 校验允许调用宿主；重新取得父组合，避免重入扩容后使用旧引用。
    if (runtime_) {
        detail::fail_text(ErrorCode::registration_closed, target,
                     {{engine_text_domain, "engine.registration.closed_during_validation"},
                      "Registration was closed during validation", {}});
    }
    for (const auto& child : find_scope(scope).children_) {
        if (child.name_ == module.name_) {
            detail::fail_text(ErrorCode::duplicate_module, target,
                         {{engine_text_domain, "engine.validation.registered_during"},
                          "Module was registered during validation", {}});
        }
    }
    find_scope(scope).add(std::move(module));
    invalidate_draft();
}

void Engine::connect(Reference requirement, Reference provider, const std::string& scope) {
    if (runtime_) {
        detail::fail_text(ErrorCode::registration_closed,
                     {path_join(scope, requirement.module), requirement.symbol},
                     {{engine_text_domain, "engine.registration.closed"}, "Registration is closed", {}});
    }
    invalidate_draft();
    auto& parent = find_scope(scope);
    try {
        parent.connect(std::move(requirement), std::move(provider));
    } catch (const EngineError& error) {
        auto diagnostic = error.diagnostic();
        diagnostic.target.module = path_join(scope, diagnostic.target.module);
        throw EngineError(std::move(diagnostic));
    }
}

void Engine::disconnect(const Reference& requirement, const std::string& scope) {
    if (runtime_) {
        detail::fail_text(ErrorCode::registration_closed,
                     {path_join(scope, requirement.module), requirement.symbol},
                     {{engine_text_domain, "engine.registration.closed"}, "Registration is closed", {}});
    }
    invalidate_draft();
    auto& parent = find_scope(scope);
    try {
        parent.disconnect(requirement);
    } catch (const EngineError& error) {
        auto diagnostic = error.diagnostic();
        diagnostic.target.module = path_join(scope, diagnostic.target.module);
        throw EngineError(std::move(diagnostic));
    }
}

std::vector<Declaration> Engine::catalog(const std::string& scope) const {
    find_scope(scope);
    std::shared_ptr<const detail::Runtime> runtime;
    if (runtime_) {
        runtime = std::static_pointer_cast<const detail::Runtime>(runtime_);
    } else {
        build_draft();
        runtime = std::static_pointer_cast<const detail::Runtime>(draft_runtime_);
    }
    std::vector<Declaration> result;
    for (const auto& item : runtime->outputs) {
        if (parent_path(item.first.module) != scope) continue;
        auto declaration = item.second->declaration;
        declaration.reference = item.first;
        result.push_back(std::move(declaration));
    }
    return result;
}

std::vector<Requirement> Engine::requirements(const std::string& scope) const {
    std::map<Reference, Requirement> sorted;
    for (const auto& child : find_scope(scope).children_) {
        for (const auto& item : child.requirements_) {
            auto declaration = *item.second;
            declaration.reference.module = path_join(scope, child.name_);
            sorted.emplace(declaration.reference, declaration);
        }
    }
    std::vector<Requirement> result;
    for (const auto& item : sorted) result.push_back(item.second);
    return result;
}

std::vector<Connection> Engine::connections(const std::string& scope) const {
    std::vector<Connection> result;
    for (const auto& item : find_scope(scope).connections_) {
        auto connection = item.second;
        connection.requirement.module = path_join(scope, connection.requirement.module);
        connection.provider.module = connection.forwarded ? scope : path_join(scope, connection.provider.module);
        result.push_back(std::move(connection));
    }
    return result;
}

std::vector<std::string> Engine::scopes() const {
    std::vector<std::string> result;
    const auto collect = [&result](const auto& self, const Module& node, const std::string& path) -> void {
        result.push_back(path);
        for (const auto& child : node.children_) self(self, child, path_join(path, child.name_));
    };
    collect(collect, root_, {});
    std::sort(result.begin(), result.end());
    return result;
}

std::vector<Declaration> Engine::candidates(const Reference& reference, const std::string& scope) const {
    local_reference(reference);
    const Reference target{path_join(scope, reference.module), reference.symbol};
    const auto needs = requirements(scope);
    const auto needed = std::find_if(needs.begin(), needs.end(), [&](const Requirement& item) { return item.reference == target; });
    if (needed == needs.end()) {
        detail::fail_text(ErrorCode::missing_requirement, target,
                     {{engine_text_domain, "engine.requirement.not_found"}, "Requirement not found", {}});
    }
    std::vector<Declaration> result;
    for (const auto& declaration : catalog(scope)) {
        if (!requirement_mismatch(declaration, *needed)) result.push_back(declaration);
    }
    return result;
}

std::vector<Diagnostic> Engine::check() const {
    build_draft();
    return draft_diagnostics_;
}

void Engine::seal() {
    if (runtime_) return;
    build_draft();
    if (!draft_diagnostics_.empty()) throw EngineError(draft_diagnostics_.front());
    runtime_ = draft_runtime_;
    invalidate_draft();
}

BindingHandle Engine::require_entry(const Reference& reference) const {
    if (!runtime_) {
        detail::fail_text(ErrorCode::registration_open, reference,
                     {{engine_text_domain, "engine.lifecycle.seal_required"},
                      "Seal registration before execution or binding", {}});
    }
    const auto* state = static_cast<const detail::Runtime*>(runtime_.get());
    const auto found = state->outputs.find(reference);
    if (reference.module.find('/') != std::string::npos || found == state->outputs.end()) {
        detail::fail_text(ErrorCode::missing_symbol, reference,
                     {{engine_text_domain, "engine.reference.top_level_missing"}, "Top-level public symbol not found", {}});
    }
    return BindingHandle{found->second, runtime_, reference};
}

BindingHandle Engine::bind_entry(const Reference& reference, SymbolKind kind,
                                 std::type_index result, const std::vector<std::type_index>& parameters) const {
    BindingHandle handle = require_entry(reference);
    const auto* entry = static_cast<const detail::Entry*>(handle.entry_.get());
    if (auto mismatch = signature_mismatch(entry->declaration, kind, result, parameters)) {
        detail::fail_text(mismatch->first, reference, mismatch->second);
    }
    return handle;
}

std::any Engine::read(const Reference& reference) const {
    return require_entry(reference).read();
}

std::any Engine::call(const Reference& reference, const std::vector<std::any>& arguments) const {
    return require_entry(reference).call(arguments);
}

StateCapability Engine::state_capability(const Reference& reference) const {
    const Module& module = find_scope(reference.module);
    if (!module.state_) {
        detail::fail_text(ErrorCode::state_incomplete, {reference.module, {}},
                     {{engine_text_domain, "engine.state.incomplete"},
                      "Module '{module}' declares neither run state nor statelessness",
                      {{"module", reference.module}}});
    }
    return StateCapability{module.state_->stateless, module.state_->contract};
}

StateSnapshot Engine::capture_state() const {
    if (!runtime_) {
        detail::fail_text(ErrorCode::registration_open, {},
                     {{engine_text_domain, "engine.lifecycle.seal_required"},
                      "Seal registration before execution or binding", {}});
    }
    const auto* state = static_cast<const detail::Runtime*>(runtime_.get());
    // 先整体核对声明，再执行任何捕获回调：不产生部分快照，也不在有缺失时调用作者代码。
    for (const auto& item : state->states) {
        if (!item.second->declared) {
            detail::fail_text(ErrorCode::state_incomplete, {item.first, {}},
                         {{engine_text_domain, "engine.state.incomplete"},
                          "Module '{module}' declares neither run state nor statelessness",
                          {{"module", item.first}}});
        }
    }
    StateSnapshot snapshot;
    snapshot.modules.reserve(state->states.size());
    for (const auto& item : state->states) {
        ModuleState module;
        module.path = item.first;
        module.contract = item.second->contract;
        module.stateless = item.second->stateless;
        if (!module.stateless) {
            try {
                module.state = item.second->capture();
            } catch (...) {
                detail::rethrow_boundary(detail::FailureBoundary::state_capture, {item.first, {}});
            }
        }
        snapshot.modules.push_back(std::move(module));
    }
    return snapshot;
}

void Engine::restore_state(const StateSnapshot& snapshot) {
    if (!runtime_) {
        detail::fail_text(ErrorCode::registration_open, {},
                     {{engine_text_domain, "engine.lifecycle.seal_required"},
                      "Seal registration before execution or binding", {}});
    }
    const auto* state = static_cast<const detail::Runtime*>(runtime_.get());
    // 快照按模块路径建立索引：集合匹配与顺序无关，重复与空路径分别拒绝。
    std::map<std::string, const ModuleState*> provided;
    for (const auto& module : snapshot.modules) {
        if (module.path.empty()) {
            detail::fail_text(ErrorCode::state_mismatch, {},
                         {{engine_text_domain, "engine.state.empty_module_path"},
                          "Snapshot module path must not be empty", {}});
        }
        if (!provided.emplace(module.path, &module).second) {
            detail::fail_text(ErrorCode::state_mismatch, {module.path, {}},
                         {{engine_text_domain, "engine.state.snapshot_duplicate"},
                          "Snapshot module '{module}' is duplicated", {{"module", module.path}}});
        }
    }
    if (provided.size() != state->states.size()) {
        detail::fail_text(ErrorCode::state_mismatch, {},
                     {{engine_text_domain, "engine.state.count_mismatch"},
                      "Snapshot has {snapshot} modules; assembly has {assembly}",
                      {{"snapshot", std::to_string(provided.size())},
                       {"assembly", std::to_string(state->states.size())}}});
    }
    for (const auto& entry : state->states) {
        const auto found = provided.find(entry.first);
        if (found == provided.end()) {
            detail::fail_text(ErrorCode::state_mismatch, {entry.first, {}},
                         {{engine_text_domain, "engine.state.snapshot_missing"},
                          "Snapshot does not contain module '{module}'", {{"module", entry.first}}});
        }
        const ModuleState& module = *found->second;
        if (!entry.second->declared) {
            detail::fail_text(ErrorCode::state_mismatch, {entry.first, {}},
                         {{engine_text_domain, "engine.state.undeclared_target"},
                          "Module '{module}' does not declare a state capability",
                          {{"module", entry.first}}});
        }
        if (entry.second->stateless != module.stateless) {
            detail::fail_text(ErrorCode::state_mismatch, {entry.first, {}},
                         {{engine_text_domain, "engine.state.stateless_mismatch"},
                          "Module '{module}' statelessness does not match the snapshot",
                          {{"module", entry.first}}});
        }
        if (module.stateless && !module.state.is_null()) {
            detail::fail_text(ErrorCode::state_mismatch, {entry.first, {}},
                         {{engine_text_domain, "engine.state.stateless_value"},
                          "Stateless module '{module}' must carry a null state value",
                          {{"module", entry.first}}});
        }
        if (entry.second->contract != module.contract) {
            detail::fail_text(ErrorCode::state_mismatch, {entry.first, {}},
                         {{engine_text_domain, "engine.state.contract_mismatch"},
                          "State contract mismatch for module '{module}': expected '{expected}', received '{received}'",
                          {{"module", entry.first},
                           {"expected", entry.second->contract},
                           {"received", module.contract}}});
        }
    }
    // 逐模块写回；模块回调失败不回滚已经恢复的模块。
    for (const auto& entry : state->states) {
        if (!entry.second->declared || entry.second->stateless) continue;
        const ModuleState& module = *provided.find(entry.first)->second;
        try {
            entry.second->restore(module.state);
        } catch (...) {
            detail::rethrow_boundary(detail::FailureBoundary::state_restore, {entry.first, {}});
        }
    }
}

}  // namespace ascend
