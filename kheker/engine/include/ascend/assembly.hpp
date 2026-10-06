#pragma once

#include <ascend/config.hpp>
#include <ascend/engine.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace ascend {

// 装配记录格式标识与当前支持版本；格式版本表示解释记录的方式，不等于模块实现版本。
inline constexpr const char* assembly_format = "ascend.assembly";
inline constexpr std::int64_t assembly_format_version = 1;
// JSON 读写采用相同的容器嵌套上限（根对象算一层）。
inline constexpr std::size_t assembly_json_max_depth = 128;

// 模块定义标识到构造入口的显式注册目录；定义标识用于选择工厂，实例名用于作用域身份。
class ModuleFactoryDirectory {
public:
    using Factory = std::function<Module(const std::string& instance, const Config& config)>;

    // 登记构造入口；preset 为默认构造配置（新建实例预填与编辑展示用，可为空）。
    // 定义标识为空或重复时分别报告 invalid_declaration／duplicate_definition。
    void add_definition(std::string definition, Factory factory, std::vector<I18nResource> resources = {},
                        Config preset = {});
    // 定义登记的默认构造配置；未登记报告 unknown_definition。
    Config default_config(const std::string& definition) const;
    // 合并另一个目录：已存在的定义保持不变，其余连同语言资源一并复制（用于宿主内置与
    // 模块库工厂合并成实例化目录）。不报告重复，合并可重复执行。
    void merge_from(const ModuleFactoryDirectory& other);
    // 查询定义登记的语言资源，不构造实例、不读取文件。多个实例共用定义资源。
    const std::vector<I18nResource>& i18n_resources(const std::string& definition) const;
    // 定义标识是否存在；definitions() 按名称排序返回全部标识。
    bool contains(const std::string& definition) const;
    std::vector<std::string> definitions() const;
    // 每次调用重新执行构造入口并返回独立实例。
    // 工厂抛出的 EngineError 以实例为目标包装，原始诊断保留在 cause 中。
    Module create(const std::string& definition, const std::string& instance, const Config& config) const;

private:
    struct Definition {
        Factory factory;
        std::vector<I18nResource> resources;
        Config preset;
    };
    std::map<std::string, Definition> factories_;
};

// 装配定义中的一条实例：模块定义标识、实例名与构造配置。
struct AssemblyInstance {
    std::string definition;
    std::string name;
    Config config;
};

// 一条显式连接：需求引用与提供方引用。
struct AssemblyConnection {
    Reference requirement;
    Reference provider;
};

// 需求转接：本作用域的需求名指向直接子模块的需求。
struct AssemblyForward {
    std::string requirement;
    Reference target;
};

// 接口导出：本组合的公开名指向直接子模块的公开符号。
struct AssemblyExport {
    std::string name;
    Reference target;
};

// 可由普通 C++ 装配代码创建的装配定义；同一份描述驱动保存与实例化。
// 作用域路径以 '/' 分隔，空路径为根作用域；根作用域不承载需求转接与接口导出。
class AssemblyDefinition {
public:
    AssemblyDefinition();

    // 在指定作用域新增子作用域；名称非空、不含 '/' 且同层唯一，否则报告 invalid_assembly。
    void add_scope(const std::string& name, const std::string& scope = {});
    // 在指定作用域加入实例；定义标识与实例名在实例化时由工厂核对。
    void add_instance(std::string definition, std::string name, Config config = {},
                      const std::string& scope = {});
    // 移除实例（按名称，取首个）；同名实例不存在报告 invalid_assembly。同一作用域内引用
    // 该实例的连接、转接与导出一并移除，作用域本身不变。
    void remove_instance(const std::string& name, const std::string& scope = {});
    // 在指定作用域声明显式连接、需求转接与接口导出；目标作用域必须已存在。
    void connect(Reference requirement, Reference provider, const std::string& scope = {});
    // 断开一条需求连接（按需求引用，取首个）；返回是否移除，无该连接时为 false。
    bool disconnect(const Reference& requirement, const std::string& scope = {});
    void forward_inherited(std::string requirement, Reference child_requirement,
                           const std::string& scope = {});
    void export_symbol(std::string name, Reference child_symbol, const std::string& scope = {});

    // 读取入口：作用域路径（含根，空字符串）按路径排序；实例快照按加入顺序返回。
    std::vector<std::string> scopes() const;
    std::vector<AssemblyInstance> instances(const std::string& scope = {}) const;
    // 替换已有实例的构造配置（同名实例取首个）；实例不存在报 invalid_assembly。
    // 替换不改变实例集合、子作用域、连接、转接与导出。
    void set_instance_config(const std::string& name, Config config, const std::string& scope = {});

    // 记录来源标识，用于诊断定位。
    const std::string& source() const noexcept { return source_; }
    // 输出为文本记录；save 写入文件，打开或写入失败报告 io_failure。
    std::string to_json() const;
    void save(const std::string& path) const;
    // 解析文本或读取文件为定义；语法、版本与结构错误分别诊断，成功后即可实例化。
    static AssemblyDefinition parse(const std::string& text, std::string source = {});
    static AssemblyDefinition load(const std::string& path);

    // 用工厂目录创建独立运行实例；返回未封闭的引擎，检查与封闭由调用方完成。
    // 任一阶段失败时不发布部分实例，已经加载的实例不受影响。
    Engine instantiate(const ModuleFactoryDirectory& factories) const;

private:
    struct Scope {
        std::string name;
        std::string path;
        std::vector<AssemblyInstance> instances;
        std::vector<AssemblyConnection> connections;
        std::vector<AssemblyForward> forwards;
        std::vector<AssemblyExport> exports;
        std::vector<std::string> children;  // 子作用域名，保持加入顺序
    };

    const Scope& require_scope(const std::string& path) const;
    Scope& require_scope(const std::string& path);
    void read_scope(const Config& object, const std::string& record, const std::string& parent_path, bool root);
    Reference read_reference(const Config& value, const std::string& record, const std::string& second) const;
    void check_keys(const Config& object, const std::string& record,
                    const std::vector<std::string>& allowed) const;
    [[noreturn]] void fail_record(ErrorCode code, const std::string& record, TextRef message) const;
    Config scope_config(const Scope& scope, bool root, const std::string& record = {},
                        std::size_t depth = 0) const;
    static Config reference_config(const Reference& reference, const std::string& second);
    Module create_module(const ModuleFactoryDirectory& factories, const AssemblyInstance& instance,
                         const std::string& record, const std::string& engine_path) const;
    Module build_scope(const ModuleFactoryDirectory& factories, const Scope& scope,
                       const std::string& record, const std::string& engine_path) const;
    EngineError attach(const EngineError& error, const std::string& record, const std::string& scope) const;

    std::map<std::string, Scope> scopes_;
    std::string source_;
};

}  // namespace ascend
