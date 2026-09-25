#include <ascend/engine.hpp>

#include <set>

namespace ascend {

EngineError::EngineError(Diagnostic diagnostic)
    : std::runtime_error(diagnostic.message), diagnostic_(std::move(diagnostic)) {}

namespace detail {

void fail(ErrorCode code, const Reference& target, std::string message) {
    throw EngineError({code, target, std::move(message)});
}

std::any read_entry(const Entry& entry) {
    const auto& declaration = entry.declaration;
    if (declaration.kind != SymbolKind::value) {
        fail(ErrorCode::wrong_kind, declaration.reference, "Expected a public value");
    }
    try {
        return entry.getter();
    } catch (const std::exception& error) {
        fail(ErrorCode::execution_failed, declaration.reference, error.what());
    } catch (...) {
        fail(ErrorCode::execution_failed, declaration.reference, "Getter threw a non-standard exception");
    }
}

std::any call_entry(const Entry& entry, const std::vector<std::any>& arguments) {
    const auto& declaration = entry.declaration;
    if (declaration.kind != SymbolKind::method) {
        fail(ErrorCode::wrong_kind, declaration.reference, "Expected a public method");
    }
    if (arguments.size() != declaration.parameters.size()) {
        fail(ErrorCode::argument_count, declaration.reference,
             "Expected " + std::to_string(declaration.parameters.size()) +
                 " arguments, received " + std::to_string(arguments.size()));
    }
    for (std::size_t index = 0; index < arguments.size(); ++index) {
        if (std::type_index(arguments[index].type()) != declaration.parameters[index].type) {
            fail(ErrorCode::type_mismatch, declaration.reference,
                 "Type mismatch for parameter '" + declaration.parameters[index].name + "'");
        }
    }
    try {
        return entry.method(arguments);
    } catch (const std::exception& error) {
        fail(ErrorCode::execution_failed, declaration.reference, error.what());
    } catch (...) {
        fail(ErrorCode::execution_failed, declaration.reference, "Method threw a non-standard exception");
    }
}

}  // namespace detail

Module::Module(std::string name, std::function<void()> validate)
    : name_(std::move(name)), validate_(std::move(validate)) {
    if (name_.empty()) {
        detail::fail(ErrorCode::invalid_declaration, {name_, {}}, "Module name must not be empty");
    }
}

void Module::add_entry(std::shared_ptr<const detail::Entry> entry) {
    const auto& declaration = entry->declaration;
    if (declaration.reference.symbol.empty()) {
        detail::fail(ErrorCode::invalid_declaration, declaration.reference, "Symbol name must not be empty");
    }
    std::set<std::string> names;
    for (const auto& parameter : declaration.parameters) {
        if (parameter.name.empty() || !names.insert(parameter.name).second) {
            detail::fail(ErrorCode::invalid_declaration, declaration.reference,
                         "Parameter names must be non-empty and unique");
        }
    }
    for (const auto* dependencies : {&declaration.reads, &declaration.writes}) {
        for (const auto& reference : *dependencies) {
            if (reference.module.empty() || reference.symbol.empty()) {
                detail::fail(ErrorCode::invalid_declaration, declaration.reference,
                             "Dependency references must name a module and a symbol");
            }
        }
    }
    // emplace 不覆盖已发布入口，失败不改变构建对象。
    if (!entries_.emplace(declaration.reference.symbol, entry).second) {
        detail::fail(ErrorCode::duplicate_symbol, declaration.reference, "Duplicate public symbol");
    }
}

void Engine::add(Module module) {
    const Reference target{module.name_, {}};
    if (sealed_) {
        detail::fail(ErrorCode::registration_closed, target, "Registration is closed");
    }
    if (module.name_.empty()) {
        detail::fail(ErrorCode::invalid_declaration, target, "Module name must not be empty");
    }
    if (modules_.find(module.name_) != modules_.end()) {
        detail::fail(ErrorCode::duplicate_module, target, "Duplicate module name");
    }
    if (module.validate_) {
        try {
            module.validate_();
        } catch (const std::exception& error) {
            detail::fail(ErrorCode::validation_failed, target, error.what());
        } catch (...) {
            detail::fail(ErrorCode::validation_failed, target, "Validator threw a non-standard exception");
        }
    }
    // 校验代码可能调用宿主，发布时仍须满足注册生命周期和唯一性。
    if (sealed_) {
        detail::fail(ErrorCode::registration_closed, target, "Registration was closed during validation");
    }
    if (!modules_.emplace(target.module, std::move(module)).second) {
        detail::fail(ErrorCode::duplicate_module, target, "Module was registered during validation");
    }
}

std::vector<Declaration> Engine::catalog() const {
    std::vector<Declaration> result;
    for (const auto& module : modules_) {
        for (const auto& entry : module.second.entries_) {
            result.push_back(entry.second->declaration);
        }
    }
    return result;
}

std::shared_ptr<const detail::Entry> Engine::find(const Reference& reference) const {
    const auto module = modules_.find(reference.module);
    if (module == modules_.end()) {
        return {};
    }
    const auto entry = module->second.entries_.find(reference.symbol);
    return entry == module->second.entries_.end() ? nullptr : entry->second;
}

std::vector<Diagnostic> Engine::check() const {
    std::vector<Diagnostic> diagnostics;
    for (const auto& declaration : catalog()) {
        for (const auto* dependencies : {&declaration.reads, &declaration.writes}) {
            for (const auto& reference : *dependencies) {
                const auto target = find(reference);
                const auto label = "module '" + reference.module + "', symbol '" + reference.symbol + "'";
                if (!target) {
                    diagnostics.push_back({ErrorCode::missing_symbol, declaration.reference,
                                           "Unresolved dependency: " + label});
                } else if (target->declaration.kind != SymbolKind::value) {
                    diagnostics.push_back({ErrorCode::wrong_kind, declaration.reference,
                                           "Read/write dependency must be a value: " + label});
                }
            }
        }
    }
    return diagnostics;
}

void Engine::seal() {
    if (sealed_) {
        return;
    }
    auto diagnostics = check();
    if (!diagnostics.empty()) {
        throw EngineError(std::move(diagnostics.front()));
    }
    sealed_ = true;
}

std::shared_ptr<const detail::Entry> Engine::require_entry(const Reference& reference) const {
    if (!sealed_) {
        detail::fail(ErrorCode::registration_open, reference, "Seal registration before execution or binding");
    }
    const auto entry = find(reference);
    if (!entry) {
        detail::fail(ErrorCode::missing_symbol, reference, "Public symbol not found");
    }
    return entry;
}

std::shared_ptr<const detail::Entry> Engine::bind_entry(
    const Reference& reference, SymbolKind kind, std::type_index result,
    const std::vector<std::type_index>& parameters) const {
    auto entry = require_entry(reference);
    const auto& declaration = entry->declaration;
    if (declaration.kind != kind) {
        detail::fail(ErrorCode::wrong_kind, reference, "Symbol kind does not match binding");
    }
    if (declaration.result_type != result) {
        detail::fail(ErrorCode::type_mismatch, reference, "Result type does not match binding");
    }
    if (declaration.parameters.size() != parameters.size()) {
        detail::fail(ErrorCode::argument_count, reference, "Parameter count does not match binding");
    }
    for (std::size_t index = 0; index < parameters.size(); ++index) {
        if (declaration.parameters[index].type != parameters[index]) {
            detail::fail(ErrorCode::type_mismatch, reference,
                         "Binding type mismatch for parameter '" + declaration.parameters[index].name + "'");
        }
    }
    return entry;
}

std::any Engine::read(const Reference& reference) const {
    return detail::read_entry(*require_entry(reference));
}

std::any Engine::call(const Reference& reference, const std::vector<std::any>& arguments) const {
    return detail::call_entry(*require_entry(reference), arguments);
}

}  // namespace ascend
