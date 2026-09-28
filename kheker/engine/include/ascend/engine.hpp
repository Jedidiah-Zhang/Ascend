#pragma once

#include <ascend/text.hpp>

#include <any>
#include <cstddef>
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

// 引擎可传输的值类型：不是引用或 cv 限定，且可复制构造。
template <class Type>
inline constexpr bool is_value_type = std::is_same_v<Type, std::decay_t<Type>> &&
                                     std::is_copy_constructible_v<Type>;

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

// 已解析绑定的不透明句柄：内部状态只保存在 src 中，调用方无法构造或拆解。
class BindingHandle {
private:
    friend class Engine;
    friend class Context;
    template <class Type> friend class ValueBinding;
    template <class Result, class... Args> friend class MethodBinding;

    BindingHandle(std::shared_ptr<const void> entry, std::shared_ptr<const void> runtime,
                  Reference reference);
    std::any read() const;
    std::any call(const std::vector<std::any>& arguments) const;
    // 在 catch 块内调用：把当前异常转换为执行诊断并重新抛出。
    [[noreturn]] void report_transport_failure() const;

    std::shared_ptr<const void> entry_;
    std::shared_ptr<const void> runtime_;
    Reference reference_;
};

class Context;
template <class Type> class ValueRequirement;
template <class Result, class... Args> class MethodRequirement;

// 仅解析当前实例声明的需求；复制上下文不会延长运行时寿命。
class Context {
private:
    friend class BindingHandle;
    template <class Type> friend class ValueRequirement;
    template <class Result, class... Args> friend class MethodRequirement;
    Context(std::shared_ptr<const void> runtime, std::string module)
        : runtime_(std::move(runtime)), module_(std::move(module)) {}
    BindingHandle resolve(const std::shared_ptr<const Requirement>& requirement) const;
    std::weak_ptr<const void> runtime_;
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
        static_assert(is_value_type<Type>, "Public values must be copyable value types");
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
        Declaration declaration{reference, SymbolKind::value, typeid(Type), {},
                                std::move(description), {}, {}, std::move(contract)};
        // 回调为空时不构造擦除回调，由非模板入口报告缺少读取实现。
        std::function<std::any(const Context&)> read;
        if (function) {
            read = [function = std::move(function)](const Context& context) -> std::any {
                return std::make_any<Type>(function(context));
            };
        }
        add_value_entry(std::move(declaration), std::move(read));
    }

    // 登记公开方法；parameters 为参数名称，数量与绑定签名一致。
    // options 提供语义说明、声明的读写引用与契约标识。
    template <class Result, class... Args, class Function>
    void add_method(std::string name, std::vector<std::string> parameters,
                    Function&& method, MethodOptions options = {}) {
        static_assert(std::is_void_v<Result> || is_value_type<Result>,
                        "Method results must be void or copyable value types");
        static_assert((is_value_type<Args> && ...),
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
        Declaration declaration{reference, SymbolKind::method, typeid(Result), {},
                                std::move(options.description), std::move(options.reads),
                                std::move(options.writes), std::move(options.contract)};
        // 回调为空时不构造擦除回调，由非模板入口报告缺少方法实现。
        std::function<std::any(const Context&, const std::vector<std::any>&)> invoke;
        if (function) {
            invoke = [function = std::move(function)](const Context& context,
                                                      const std::vector<std::any>& arguments) -> std::any {
                return invoke_method<Result, Args...>(function, context, arguments,
                                                      std::index_sequence_for<Args...>{});
            };
        }
        add_method_entry(std::move(declaration), std::move(parameters),
                         std::vector<std::type_index>{typeid(Args)...}, std::move(invoke));
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
    // 内部装配构建器：定义在 src，仅用于遍历模块结构。
    friend class AssemblyBuilder;

    // 非模板登记入口：校验声明并保存擦除类型后的回调。
    void add_value_entry(Declaration declaration, std::function<std::any(const Context&)> getter);
    void add_method_entry(Declaration declaration, std::vector<std::string> names,
                          std::vector<std::type_index> types,
                          std::function<std::any(const Context&, const std::vector<std::any>&)> method);
    void add_requirement(std::shared_ptr<const Requirement> requirement);
    void add_connection(Connection connection);

    // 记录来源位置，供装配记录诊断；由 AssemblyDefinition 写入。
    struct Location {
        std::string source;
        std::string path;
    };
    void set_source(std::string source, std::string path);
    void set_requirement_source(const std::string& symbol, std::string source, std::string path);
    void set_connection_source(const Reference& requirement, std::string source, std::string path);
    void set_export_source(const std::string& name, std::string source, std::string path);

    // 擦除参数后按声明顺序还原并调用；调用方负责参数数量与类型已在绑定或调用时核对。
    template <class Result, class... Args, class Function, std::size_t... Indices>
    static std::any invoke_method(const Function& function, const Context& context,
                                  const std::vector<std::any>& arguments,
                                  std::index_sequence<Indices...>) {
        if constexpr (std::is_void_v<Result>) {
            function(context, std::any_cast<const Args&>(arguments[Indices])...);
            return {};
        } else {
            return std::make_any<Result>(function(context, std::any_cast<const Args&>(arguments[Indices])...));
        }
    }

    std::string name_;
    std::function<void()> validate_;
    std::map<std::string, std::shared_ptr<const void>> entries_;
    std::map<std::string, std::shared_ptr<const Requirement>> requirements_;
    std::vector<Module> children_;
    std::map<Reference, Connection> connections_;
    std::map<std::string, Reference> exports_;
    Location source_;
    std::map<std::string, Location> requirement_sources_;
    std::map<Reference, Location> connection_sources_;
    std::map<std::string, Location> export_sources_;
};

// 已解析的公开量绑定；read 返回值副本，传输失败抛出执行诊断。
template <class Type>
class ValueBinding {
public:
    Type read() const {
        std::any value = handle_.read();
        try {
            return std::any_cast<Type>(std::move(value));
        } catch (...) {
            handle_.report_transport_failure();
        }
    }

private:
    friend class Engine;
    template <class> friend class ValueRequirement;
    explicit ValueBinding(BindingHandle handle) : handle_(std::move(handle)) {}
    BindingHandle handle_;
};

// 已解析的方法绑定；调用时封装参数、执行并解包结果，传输失败抛出执行诊断。
template <class Result, class... Args>
class MethodBinding {
public:
    Result operator()(const Args&... arguments) const {
        std::vector<std::any> packed;
        try {
            packed = {std::make_any<Args>(arguments)...};
        } catch (...) {
            handle_.report_transport_failure();
        }
        std::any result = handle_.call(packed);
        if constexpr (!std::is_void_v<Result>) {
            try {
                return std::any_cast<Result>(std::move(result));
            } catch (...) {
                handle_.report_transport_failure();
            }
        }
    }

private:
    friend class Engine;
    template <class, class...> friend class MethodRequirement;
    explicit MethodBinding(BindingHandle handle) : handle_(std::move(handle)) {}
    BindingHandle handle_;
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
    static_assert(is_value_type<Type>, "Requirements need copyable value types");
    auto requirement = std::make_shared<const Requirement>(Requirement{
        {name_, std::move(name)}, SymbolKind::value, typeid(Type), {},
        std::move(contract), std::move(description)});
    add_requirement(requirement);
    return ValueRequirement<Type>(std::move(requirement));
}

template <class Result, class... Args>
MethodRequirement<Result, Args...> Module::require_method(std::string name, std::string contract,
                                                          TextRef description) {
    static_assert(std::is_void_v<Result> || is_value_type<Result>, "Invalid requirement result");
    static_assert((is_value_type<Args> && ...), "Invalid requirement parameters");
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
        static_assert(is_value_type<Type>, "Bindings require a copyable value type");
        return ValueBinding<Type>(bind_entry(reference, SymbolKind::value, typeid(Type), {}));
    }

    template <class Result, class... Args>
    MethodBinding<Result, Args...> bind_method(const Reference& reference) const {
        static_assert(std::is_void_v<Result> || is_value_type<Result>,
                        "Bindings require void or a copyable result type");
        static_assert((is_value_type<Args> && ...), "Bindings require copyable argument types");
        return MethodBinding<Result, Args...>(
            bind_entry(reference, SymbolKind::method, typeid(Result), {typeid(Args)...}));
    }

private:
    friend class AssemblyDefinition;
    Module& find_scope(const std::string& scope);
    const Module& find_scope(const std::string& scope) const;
    // 未封闭时的目录草稿：模块或连接变化后失效，避免反复浏览目录时重复构建整棵树。
    void build_draft() const;
    void invalidate_draft();
    // 解析顶层公开项并核对签名；返回的句柄持有运行装配。
    BindingHandle require_entry(const Reference& reference) const;
    BindingHandle bind_entry(
        const Reference& reference, SymbolKind kind, std::type_index result,
        const std::vector<std::type_index>& parameters) const;

    Module root_{"$assembly"};
    std::shared_ptr<const void> runtime_;
    mutable std::shared_ptr<const void> draft_runtime_;
    mutable std::vector<Diagnostic> draft_diagnostics_;
    mutable bool draft_ready_ = false;
};

}  // namespace ascend
