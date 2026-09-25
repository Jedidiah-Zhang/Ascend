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
};

struct MethodOptions {
    std::string description;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
};

namespace detail {

struct Entry {
    Declaration declaration;
    std::function<std::any()> getter;
    std::function<std::any(const std::vector<std::any>&)> method;
};

[[noreturn]] void fail(ErrorCode code, const Reference& target, std::string message);
std::any read_entry(const Entry& entry);
std::any call_entry(const Entry& entry, const std::vector<std::any>& arguments);

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
std::any invoke_native(const std::function<Result(const Args&...)>& function,
                        const std::vector<std::any>& arguments,
                        std::index_sequence<Indices...>) {
    if constexpr (std::is_void_v<Result>) {
        function(std::any_cast<const Args&>(arguments[Indices])...);
        return {};
    } else {
        return std::make_any<Result>(function(std::any_cast<const Args&>(arguments[Indices])...));
    }
}

}  // namespace detail

class Module {
public:
    // 校验函数在注册时执行；回调所引用的外部资源由模块作者维护。
    explicit Module(std::string name, std::function<void()> validate = {});

    template <class Type, class Getter>
    void add_value(std::string name, Getter&& getter, std::string description = {}) {
        static_assert(detail::value_type<Type>, "Public values must be copyable value types");
        const Reference reference{name_, std::move(name)};
        std::function<Type()> function(std::forward<Getter>(getter));
        if (!function) {
            detail::fail(ErrorCode::invalid_declaration, reference, "Missing value getter");
        }
        Declaration declaration{reference, SymbolKind::value, typeid(Type), {},
                                std::move(description), {}, {}};
        auto read = [function = std::move(function)]() -> std::any {
            return std::make_any<Type>(function());
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
        std::function<Result(const Args&...)> function(std::forward<Function>(method));
        if (!function) {
            detail::fail(ErrorCode::invalid_declaration, reference, "Missing method implementation");
        }
        if (parameters.size() != sizeof...(Args)) {
            detail::fail(ErrorCode::invalid_declaration, reference,
                            "Parameter names do not match the declared signature");
        }
        Declaration declaration{reference, SymbolKind::method, typeid(Result), {},
                                std::move(options.description), std::move(options.reads),
                                std::move(options.writes)};
        const std::vector<std::type_index> types{typeid(Args)...};
        for (std::size_t index = 0; index < types.size(); ++index) {
            declaration.parameters.push_back({std::move(parameters[index]), types[index]});
        }
        auto invoke = [function = std::move(function)](const std::vector<std::any>& arguments) {
            return detail::invoke_native<Result, Args...>(
                function, arguments, std::index_sequence_for<Args...>{});
        };
        add_entry(std::make_shared<detail::Entry>(
            detail::Entry{std::move(declaration), {}, std::move(invoke)}));
    }

private:
    friend class Engine;
    void add_entry(std::shared_ptr<const detail::Entry> entry);

    std::string name_;
    std::function<void()> validate_;
    std::map<std::string, std::shared_ptr<const detail::Entry>> entries_;
};

template <class Type>
class ValueBinding {
public:
    Type read() const {
        auto result = detail::read_entry(*entry_);
        return detail::transport_value(entry_->declaration.reference, [&] {
            return std::any_cast<Type>(std::move(result));
        });
    }

private:
    friend class Engine;
    explicit ValueBinding(std::shared_ptr<const detail::Entry> entry) : entry_(std::move(entry)) {}
    std::shared_ptr<const detail::Entry> entry_;
};

template <class Result, class... Args>
class MethodBinding {
public:
    Result operator()(const Args&... arguments) const {
        const auto packed = detail::transport_value(entry_->declaration.reference, [&] {
            return std::vector<std::any>{std::make_any<Args>(arguments)...};
        });
        auto result = detail::call_entry(*entry_, packed);
        if constexpr (!std::is_void_v<Result>) {
            return detail::transport_value(entry_->declaration.reference, [&] {
                return std::any_cast<Result>(std::move(result));
            });
        }
    }

private:
    friend class Engine;
    explicit MethodBinding(std::shared_ptr<const detail::Entry> entry) : entry_(std::move(entry)) {}
    std::shared_ptr<const detail::Entry> entry_;
};

// 首个切片由单线程宿主显式调用。封闭注册后目录不变；句柄独立持有入口。
class Engine {
public:
    Engine() = default;
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;

    void add(Module module);
    std::vector<Declaration> catalog() const;
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
    std::shared_ptr<const detail::Entry> find(const Reference& reference) const;
    std::shared_ptr<const detail::Entry> require_entry(const Reference& reference) const;
    std::shared_ptr<const detail::Entry> bind_entry(
        const Reference& reference, SymbolKind kind, std::type_index result,
        const std::vector<std::type_index>& parameters) const;

    std::map<std::string, Module> modules_;
    bool sealed_ = false;
};

}  // namespace ascend
