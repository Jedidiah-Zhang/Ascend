#pragma once

#include <ascend/text.hpp>

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

inline constexpr const char* engine_text_domain = "ascend.engine";

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

// 检查、绑定、调用与装配加载共用的错误类别；具体触发条件见相应操作的注释。
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
    duplicate_definition,
    unknown_definition,
    invalid_config,
    invalid_assembly,
    invalid_json,
    unsupported_format_version,
    io_failure,
    invalid_i18n,
};

// target 定位出错的引用或实例；text 是唯一的消息定义，字符串只在展示时生成。
// source 与 path 供装配记录等外部来源定位；同进程引擎诊断留空。
struct Diagnostic {
    ErrorCode code;
    Reference target;
    TextRef text;
    std::string source{};
    std::string path{};
    // 操作边界的 target 定位当前入口；cause 保留内部原始诊断。
    // 构造中的模块尚未建立完整归属，cause.target 可能仅为局部引用。
    std::shared_ptr<const Diagnostic> cause{};
};

std::string render_diagnostic(const Diagnostic& diagnostic, const TextCatalog* catalog = nullptr,
                              const std::string& locale = {});

// 所有引擎失败都以该异常抛出；diagnostic() 返回原始诊断。
class EngineError : public std::runtime_error {
public:
    explicit EngineError(Diagnostic diagnostic);
    const Diagnostic& diagnostic() const noexcept { return diagnostic_; }

private:
    Diagnostic diagnostic_;
};

enum class SymbolKind { value, method };

// 参数名称与精确 C++ 类型；名称用于目录与诊断。
struct Parameter {
    std::string name;
    std::type_index type;
};

// 目录中的一项声明：引用、符号种类、结果类型、参数、语义说明、声明的读写引用与契约标识。
// 原生类型只用于本进程匹配，不是存档标识或跨工具链 ABI。
struct Declaration {
    Reference reference;
    SymbolKind kind;
    std::type_index result_type{typeid(void)};
    std::vector<Parameter> parameters;
    TextRef description;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
    std::string contract;
};

// 公开方法的可选声明信息：description 为语义说明，reads／writes 为声明的公开量引用，
// contract 为连接时与需求核对的接口契约标识。
struct MethodOptions {
    TextRef description;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
    std::string contract;
};

// 一项接口需求：种类、精确签名、契约标识与说明；装配时连接到提供方。
struct Requirement {
    Reference reference;
    SymbolKind kind;
    std::type_index result_type{typeid(void)};
    std::vector<std::type_index> parameters;
    std::string contract;
    TextRef description;
};

// 一条连接；forwarded 为真表示该记录来自需求转接，提供方字段指向转接后的外层需求。
struct Connection {
    Reference requirement;
    Reference provider;
    bool forwarded = false;
};

class Context;
template <class Type> class ValueRequirement;
template <class Result, class... Args> class MethodRequirement;

namespace detail {

// 装配记录的来源位置：source 为记录标识，path 为记录内字段路径。
struct SourceLocation {
    std::string source;
    std::string path;
};

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

// 作者文本作为原文进入诊断；内置消息保留模板与参数，不提前渲染。
[[noreturn]] void fail(ErrorCode code, const Reference& target, std::string message);
[[noreturn]] void fail_text(ErrorCode code, const Reference& target, TextRef text);
Diagnostic wrap_diagnostic(ErrorCode code, const Reference& target, TextRef text, const Diagnostic& cause);
enum class FailureBoundary { getter, method, validation, transport };
// 仅在 catch 内调用；统一保存 EngineError 原因链、作者原文和非标准异常消息。
[[noreturn]] void rethrow_boundary(FailureBoundary boundary, const Reference& target);
std::any read_entry(const Entry& entry, const std::shared_ptr<const Runtime>& runtime);
std::any call_entry(const Entry& entry, const std::shared_ptr<const Runtime>& runtime,
                    const std::vector<std::any>& arguments);

// 只包围引擎内部的值封装／解包；调用检查产生的诊断不在此重新包装。
template <class Function>
auto transport_value(const Reference& target, Function&& function) {
    try {
        return std::forward<Function>(function)();
    } catch (...) {
        rethrow_boundary(FailureBoundary::transport, target);
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

// 可注册模块：公开量与方法、接口需求、直接子模块、连接、需求转接与接口导出。
// 装配方负责命名与接线；回调引用的外部资源由模块作者维护。
class Module {
public:
    // 校验函数在注册时执行；回调所引用的外部资源由模块作者维护。
    explicit Module(std::string name, std::function<void()> validate = {});

    // 登记公开量，读取回调返回当前值；Type 必须是可复制的值类型。
    // description 供目录与工作台展示，contract 用于装配连接核对。
    template <class Type, class Getter>
    void add_value(std::string name, Getter&& getter, TextRef description = {},
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
            detail::fail_text(ErrorCode::invalid_declaration, reference,
                              {{engine_text_domain, "engine.declaration.missing_getter"}, "Missing value getter"});
        }
        Declaration declaration{reference, SymbolKind::value, typeid(Type), {},
                                std::move(description), {}, {}, std::move(contract)};
        auto read = [function = std::move(function)](const Context& context) -> std::any {
            return std::make_any<Type>(function(context));
        };
        add_entry(std::make_shared<detail::Entry>(
            detail::Entry{std::move(declaration), std::move(read), {}}));
    }

    // 登记公开方法；parameters 为参数名称，数量与绑定签名一致。
    // options 提供语义说明、声明的读写引用与契约标识。
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
            detail::fail_text(ErrorCode::invalid_declaration, reference,
                              {{engine_text_domain, "engine.declaration.missing_method"}, "Missing method implementation"});
        }
        if (parameters.size() != sizeof...(Args)) {
            detail::fail_text(ErrorCode::invalid_declaration, reference,
                              {{engine_text_domain, "engine.declaration.parameter_count"},
                               "Parameter names do not match the declared signature"});
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

    // 声明值需求与操作需求；contract 必须非空，作为连接时核对的接口身份。
    // 返回的需求对象只能通过 Context 解析到当前实例的连接。
    template <class Type>
    ValueRequirement<Type> require_value(std::string name, std::string contract,
                                         TextRef description = {});
    template <class Result, class... Args>
    MethodRequirement<Result, Args...> require_method(std::string name, std::string contract,
                                                      TextRef description = {});

    // 实例名由装配方给出；工厂返回的模块以该名参与作用域身份。
    const std::string& name() const noexcept { return name_; }

    // 加入直接子模块；同层子模块名必须唯一。
    void add(Module child);
    // 将直接子模块的一项需求连接到同层提供项；每项需求至多一条连接。
    void connect(Reference requirement, Reference provider);
    // 移除需求上的连接，用于检查失败后的修正。
    void disconnect(const Reference& requirement);
    // 将本模块已声明的需求转接到直接子模块的需求。
    void forward(std::string requirement, Reference child_requirement);
    // 同名需求尚未声明时，按目标子需求的种类、签名与契约声明后再转接；
    // 已声明时与 forward 相同，按已声明签名检查。
    void forward_inherited(std::string requirement, Reference child_requirement);
    // 将直接子模块的公开符号导出为本模块的公开符号。
    void export_symbol(std::string name, Reference child_symbol);

private:
    friend class Engine;
    friend class AssemblyDefinition;
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
    detail::SourceLocation source_;
    std::map<std::string, detail::SourceLocation> requirement_sources_;
    std::map<Reference, detail::SourceLocation> connection_sources_;
    std::map<std::string, detail::SourceLocation> export_sources_;
};

// 已解析的公开量绑定；read 返回值副本，传输失败抛出执行诊断。
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

// 已解析的方法绑定；调用时封装参数、执行并解包结果，传输失败抛出执行诊断。
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

// 回调中使用的类型化值需求；通过 Context 按当前实例解析。
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

// 回调中使用的类型化操作需求；通过 Context 按当前实例解析。
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
                                              TextRef description) {
    static_assert(detail::value_type<Type>, "Requirements need copyable value types");
    auto requirement = std::make_shared<const Requirement>(Requirement{
        {name_, std::move(name)}, SymbolKind::value, typeid(Type), {},
        std::move(contract), std::move(description)});
    add_requirement(requirement);
    return ValueRequirement<Type>(std::move(requirement));
}

template <class Result, class... Args>
MethodRequirement<Result, Args...> Module::require_method(std::string name, std::string contract,
                                                          TextRef description) {
    static_assert(std::is_void_v<Result> || detail::value_type<Result>, "Invalid requirement result");
    static_assert((detail::value_type<Args> && ...), "Invalid requirement parameters");
    auto requirement = std::make_shared<const Requirement>(Requirement{
        {name_, std::move(name)}, SymbolKind::method, typeid(Result), {typeid(Args)...},
        std::move(contract), std::move(description)});
    add_requirement(requirement);
    return MethodRequirement<Result, Args...>(std::move(requirement));
}

// 单线程宿主驱动的装配与运行入口。
// 装配阶段可添加、连接并反复检查；封闭后不再改变模块与连接，外部绑定句柄持有完整运行装配。
class Engine {
public:
    Engine() = default;
    Engine(const Engine&) = delete;
    Engine& operator=(const Engine&) = delete;
    // 支持按值返回新装配；移动后的原对象状态未指定。
    Engine(Engine&&) = default;
    Engine& operator=(Engine&&) = default;

    // 在指定作用域加入模块；空 scope 为根作用域，模块名在同层唯一。
    void add(Module module, const std::string& scope = {});
    // 在指定作用域建立或移除连接；作用域必须已存在。
    void connect(Reference requirement, Reference provider, const std::string& scope = {});
    void disconnect(const Reference& requirement, const std::string& scope = {});
    // 列出该作用域直接子模块的公开声明、需求与连接；只读取声明信息，可在封闭前调用。
    std::vector<Declaration> catalog(const std::string& scope = {}) const;
    std::vector<Requirement> requirements(const std::string& scope = {}) const;
    std::vector<Connection> connections(const std::string& scope = {}) const;
    // 在该作用域内查找与指定需求兼容的公开提供项（种类、精确签名与契约标识均需匹配）；不建立连接。
    std::vector<Declaration> candidates(const Reference& requirement, const std::string& scope = {}) const;
    // 返回全部装配诊断，不抛出；封闭发布运行装配，存在诊断时抛出第一项并保持可注册状态。
    std::vector<Diagnostic> check() const;
    void seal();

    // 运行阶段读取顶层公开量或调用顶层公开方法；未封闭时报告 registration_open。
    std::any read(const Reference& reference) const;
    std::any call(const Reference& reference, const std::vector<std::any>& arguments) const;

    // 解析引用并核对种类、结果类型与参数后返回类型化绑定；约束同 read／call。
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
    friend class AssemblyDefinition;
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
