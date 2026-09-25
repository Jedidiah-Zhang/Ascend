#pragma once

#include <any>
#include <functional>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <tuple>
#include <typeindex>
#include <type_traits>
#include <utility>
#include <vector>

namespace ascend {

// 引用保持为两部分，不依赖名称分隔符或领域内置概念。
struct Reference {
    std::string module;
    std::string symbol;

    bool operator==(const Reference& other) const noexcept {
        return module == other.module && symbol == other.symbol;
    }

    bool operator<(const Reference& other) const noexcept {
        return std::tie(module, symbol) < std::tie(other.module, other.symbol);
    }
};

enum class ErrorCode {
    invalid_declaration,
    duplicate_module,
    duplicate_symbol,
    missing_symbol,
    wrong_kind,
    type_mismatch,
    argument_count,
    registration_open,
    registration_closed,
    validation_failed,
    execution_failed,
    missing_module,
    missing_requirement,
    duplicate_requirement,
    duplicate_connection,
    unconnected_requirement,
    contract_mismatch,
};

struct Diagnostic {
    ErrorCode code;
    Reference target;
    std::string message;
};

class EngineError : public std::runtime_error {
public:
    explicit EngineError(Diagnostic diagnostic);
    const Diagnostic& diagnostic() const noexcept { return diagnostic_; }

private:
    Diagnostic diagnostic_;
};

enum class SymbolKind { value, method };

struct Parameter {
    std::string name;
    std::type_index type;
};

// 原生类型只用于本进程匹配，不是存档标识或跨工具链 ABI。
struct Declaration {
    Reference reference;
    SymbolKind kind;
    std::type_index result_type{typeid(void)};
    std::vector<Parameter> parameters;
    std::string description;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
    std::string contract;
};

struct MethodOptions {
    std::string description;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
    std::string contract;
};

struct Requirement {
    Reference reference;
    SymbolKind kind;
    std::type_index result_type{typeid(void)};
    std::vector<std::type_index> parameters;
    std::string contract;
    std::string description;
};

struct Connection {
    Reference requirement;
    Reference provider;
    bool forwarded = false;
};

class Context;
template <class Type> class ValueRequirement;
template <class Result, class... Args> class MethodRequirement;

namespace detail {

struct Runtime;
class AssemblyBuilder;

struct Entry {
    Declaration declaration;
    std::function<std::any(const Context&)> getter;
    std::function<std::any(const Context&, const std::vector<std::any>&)> method;
};

struct BoundEntry {
    std::shared_ptr<const Entry> entry;
    std::shared_ptr<const Runtime> runtime;
};

[[noreturn]] void fail(ErrorCode code, const Reference& target, std::string message);
std::any read_entry(const Entry& entry, const std::shared_ptr<const Runtime>& runtime);
std::any call_entry(const Entry& entry, const std::shared_ptr<const Runtime>& runtime,
                    const std::vector<std::any>& arguments);

// 只包围引擎内部的值封装／解包；调用检查产生的诊断不在此重新包装。
template <class Function>
auto transport_value(const Reference& target, Function&& function) {
    try {
        return std::forward<Function>(function)();
    } catch (const std::exception& error) {
        fail(ErrorCode::execution_failed, target, error.what());
    } catch (...) {
        fail(ErrorCode::execution_failed, target, "Value transport threw a non-standard exception");
    }
}

template <class Type>
constexpr bool value_type = std::is_same_v<Type, std::decay_t<Type>> &&
                            std::is_copy_constructible_v<Type>;

template <class Result, class... Args, std::size_t... Indices>
std::any invoke_native(const std::function<Result(const Context&, const Args&...)>& function,
                        const Context& context,
                        const std::vector<std::any>& arguments,
                        std::index_sequence<Indices...>) {
    if constexpr (std::is_void_v<Result>) {
        function(context, std::any_cast<const Args&>(arguments[Indices])...);
        return {};
    } else {
        return std::make_any<Result>(function(context, std::any_cast<const Args&>(arguments[Indices])...));
    }
}

}  // namespace detail

// 仅解析当前实例声明的需求；复制上下文不会延长运行时寿命。
class Context {
private:
    template <class Type> friend class ValueRequirement;
    template <class Result, class... Args> friend class MethodRequirement;
    friend std::any detail::read_entry(const detail::Entry&, const std::shared_ptr<const detail::Runtime>&);
    friend std::any detail::call_entry(const detail::Entry&, const std::shared_ptr<const detail::Runtime>&,
                                      const std::vector<std::any>&);
    Context(const std::shared_ptr<const detail::Runtime>& runtime, std::string module)
        : runtime_(runtime), module_(std::move(module)) {}
    detail::BoundEntry resolve(const std::shared_ptr<const Requirement>& requirement) const;
    std::weak_ptr<const detail::Runtime> runtime_;
    std::string module_;
};

class Module {
public:
    // 校验函数在注册时执行；回调所引用的外部资源由模块作者维护。
    explicit Module(std::string name, std::function<void()> validate = {});

    template <class Type, class Getter>
    void add_value(std::string name, Getter&& getter, std::string description = {},
                    std::string contract = {}) {
        static_assert(detail::value_type<Type>, "Public values must be copyable value types");
        const Reference reference{name_, std::move(name)};
        std::function<Type(const Context&)> function;
        if constexpr (std::is_invocable_r_v<Type, Getter&, const Context&>) {
            function = std::forward<Getter>(getter);
        } else {
            std::function<Type()> plain(std::forward<Getter>(getter));
            if (plain) {
                function = [plain = std::move(plain)](const Context&) { return plain(); };
            }
        }
        if (!function) {
            detail::fail(ErrorCode::invalid_declaration, reference, "Missing value getter");
        }
        Declaration declaration{reference, SymbolKind::value, typeid(Type), {},
                                std::move(description), {}, {}, std::move(contract)};
        auto read = [function = std::move(function)](const Context& context) -> std::any {
            return std::make_any<Type>(function(context));
        };
        add_entry(std::make_shared<detail::Entry>(
            detail::Entry{std::move(declaration), std::move(read), {}}));
    }

    template <class Result, class... Args, class Function>
    void add_method(std::string name, std::vector<std::string> parameters,
                    Function&& method, MethodOptions options = {}) {
        static_assert(std::is_void_v<Result> || detail::value_type<Result>,
                        "Method results must be void or copyable value types");
        static_assert((detail::value_type<Args> && ...),
                        "Method parameters must be copyable value types");
        const Reference reference{name_, std::move(name)};
        std::function<Result(const Context&, const Args&...)> function;
        if constexpr (std::is_invocable_r_v<Result, Function&, const Context&, const Args&...>) {
            function = std::forward<Function>(method);
        } else {
            std::function<Result(const Args&...)> plain(std::forward<Function>(method));
            if (plain) {
                function = [plain = std::move(plain)](const Context&, const Args&... args) -> Result {
                    return plain(args...);
                };
            }
        }
        if (!function) {
            detail::fail(ErrorCode::invalid_declaration, reference, "Missing method implementation");
        }
        if (parameters.size() != sizeof...(Args)) {
            detail::fail(ErrorCode::invalid_declaration, reference,
                            "Parameter names do not match the declared signature");
        }
        Declaration declaration{reference, SymbolKind::method, typeid(Result), {},
                                std::move(options.description), std::move(options.reads),
                                std::move(options.writes), std::move(options.contract)};
        const std::vector<std::type_index> types{typeid(Args)...};
        for (std::size_t index = 0; index < types.size(); ++index) {
            declaration.parameters.push_back({std::move(parameters[index]), types[index]});
        }
        auto invoke = [function = std::move(function)](const Context& context,
                                                       const std::vector<std::any>& arguments) {
            return detail::invoke_native<Result, Args...>(
                function, context, arguments, std::index_sequence_for<Args...>{});
        };
        add_entry(std::make_shared<detail::Entry>(
            detail::Entry{std::move(declaration), {}, std::move(invoke)}));
    }

    template <class Type>
    ValueRequirement<Type> require_value(std::string name, std::string contract,
                                         std::string description = {});
    template <class Result, class... Args>
    MethodRequirement<Result, Args...> require_method(std::string name, std::string contract,
                                                      std::string description = {});

    void add(Module child);
    void connect(Reference requirement, Reference provider);
    void disconnect(const Reference& requirement);
    void forward(std::string requirement, Reference child_requirement);
    void export_symbol(std::string name, Reference child_symbol);

private:
    friend class Engine;
    friend class detail::AssemblyBuilder;
    void add_entry(std::shared_ptr<const detail::Entry> entry);
    void add_requirement(std::shared_ptr<const Requirement> requirement);
    void add_connection(Connection connection);

    std::string name_;
    std::function<void()> validate_;
    std::map<std::string, std::shared_ptr<const detail::Entry>> entries_;
    std::map<std::string, std::shared_ptr<const Requirement>> requirements_;
    std::vector<Module> children_;
    std::map<Reference, Connection> connections_;
    std::map<std::string, Reference> exports_;
};

template <class Type>
class ValueBinding {
public:
    Type read() const {
        auto result = detail::read_entry(*entry_, runtime_);
        return detail::transport_value(entry_->declaration.reference, [&] {
            return std::any_cast<Type>(std::move(result));
        });
    }

private:
    friend class Engine;
    friend class ValueRequirement<Type>;
    explicit ValueBinding(detail::BoundEntry bound)
        : entry_(std::move(bound.entry)), runtime_(std::move(bound.runtime)) {}
    std::shared_ptr<const detail::Entry> entry_;
    std::shared_ptr<const detail::Runtime> runtime_;
};

template <class Result, class... Args>
class MethodBinding {
public:
    Result operator()(const Args&... arguments) const {
        const auto packed = detail::transport_value(entry_->declaration.reference, [&] {
            return std::vector<std::any>{std::make_any<Args>(arguments)...};
        });
        auto result = detail::call_entry(*entry_, runtime_, packed);
        if constexpr (!std::is_void_v<Result>) {
            return detail::transport_value(entry_->declaration.reference, [&] {
                return std::any_cast<Result>(std::move(result));
            });
        }
    }

private:
    friend class Engine;
    friend class MethodRequirement<Result, Args...>;
    explicit MethodBinding(detail::BoundEntry bound)
        : entry_(std::move(bound.entry)), runtime_(std::move(bound.runtime)) {}
    std::shared_ptr<const detail::Entry> entry_;
    std::shared_ptr<const detail::Runtime> runtime_;
};

template <class Type>
class ValueRequirement {
public:
    Type read(const Context& context) const {
        return ValueBinding<Type>(context.resolve(requirement_)).read();
    }
private:
    friend class Module;
    explicit ValueRequirement(std::shared_ptr<const Requirement> requirement)
        : requirement_(std::move(requirement)) {}
    std::shared_ptr<const Requirement> requirement_;
};

template <class Result, class... Args>
class MethodRequirement {
public:
    Result operator()(const Context& context, const Args&... args) const {
        return MethodBinding<Result, Args...>(context.resolve(requirement_))(args...);
    }
private:
    friend class Module;
    explicit MethodRequirement(std::shared_ptr<const Requirement> requirement)
        : requirement_(std::move(requirement)) {}
    std::shared_ptr<const Requirement> requirement_;
};

template <class Type>
ValueRequirement<Type> Module::require_value(std::string name, std::string contract,
                                              std::string description) {
    static_assert(detail::value_type<Type>, "Requirements need copyable value types");
    auto requirement = std::make_shared<const Requirement>(Requirement{
        {name_, std::move(name)}, SymbolKind::value, typeid(Type), {},
        std::move(contract), std::move(description)});
    add_requirement(requirement);
    return ValueRequirement<Type>(std::move(requirement));
}

template <class Result, class... Args>
MethodRequirement<Result, Args...> Module::require_method(std::string name, std::string contract,
                                                          std::string description) {
    static_assert(std::is_void_v<Result> || detail::value_type<Result>, "Invalid requirement result");
    static_assert((detail::value_type<Args> && ...), "Invalid requirement parameters");
    auto requirement = std::make_shared<const Requirement>(Requirement{
        {name_, std::move(name)}, SymbolKind::method, typeid(Result), {typeid(Args)...},
        std::move(contract), std::move(description)});
    add_requirement(requirement);
    return MethodRequirement<Result, Args...>(std::move(requirement));
}

// 单线程宿主驱动。装配完成后目录和连接固定；外部句柄持有完整运行时。
class Engine {
public:
    Engine() = default;
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    void add(Module module, const std::string& scope = {});
    void connect(Reference requirement, Reference provider, const std::string& scope = {});
    void disconnect(const Reference& requirement, const std::string& scope = {});
    std::vector<Declaration> catalog(const std::string& scope = {}) const;
    std::vector<Requirement> requirements(const std::string& scope = {}) const;
    std::vector<Connection> connections(const std::string& scope = {}) const;
    std::vector<Declaration> candidates(const Reference& requirement, const std::string& scope = {}) const;
    std::vector<Diagnostic> check() const;
    void seal();

    std::any read(const Reference& reference) const;
    std::any call(const Reference& reference, const std::vector<std::any>& arguments) const;

    template <class Type>
    ValueBinding<Type> bind_value(const Reference& reference) const {
        static_assert(detail::value_type<Type>, "Bindings require a copyable value type");
        return ValueBinding<Type>(bind_entry(reference, SymbolKind::value, typeid(Type), {}));
    }

    template <class Result, class... Args>
    MethodBinding<Result, Args...> bind_method(const Reference& reference) const {
        static_assert(std::is_void_v<Result> || detail::value_type<Result>,
                        "Bindings require void or a copyable result type");
        static_assert((detail::value_type<Args> && ...), "Bindings require copyable argument types");
        return MethodBinding<Result, Args...>(
            bind_entry(reference, SymbolKind::method, typeid(Result), {typeid(Args)...}));
    }

private:
    Module& find_scope(const std::string& scope);
    const Module& find_scope(const std::string& scope) const;
    std::shared_ptr<const detail::Entry> require_entry(const Reference& reference) const;
    detail::BoundEntry bind_entry(
        const Reference& reference, SymbolKind kind, std::type_index result,
        const std::vector<std::type_index>& parameters) const;

    Module root_{"$assembly"};
    std::shared_ptr<const detail::Runtime> runtime_;
};

}  // namespace ascend
