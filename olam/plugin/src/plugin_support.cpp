#include <olam/plugin/plugin_support.hpp>

#include <ascend/module_package.hpp>

#include <any>
#include <cstdint>
#include <deque>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace olam::plugin {
namespace {

AscendPluginString to_c(const std::string& value) noexcept {
    return AscendPluginString{value.data(), value.size()};
}

std::string platform_name() {
#if defined(_M_ARM64) || defined(__aarch64__)
    const char* architecture = "arm64";
#else
    const char* architecture = "x86_64";
#endif
#if defined(_WIN32)
    return std::string("windows-") + architecture;
#elif defined(__linux__)
    return std::string("linux-") + architecture;
#elif defined(__APPLE__)
    return std::string("macos-") + architecture;
#else
    return "unknown";
#endif
}

AscendPluginType type_from_name(const std::string& name) {
    if (name == "void") return ASCEND_PLUGIN_TYPE_VOID;
    if (name == "bool") return ASCEND_PLUGIN_TYPE_BOOL;
    if (name == "int64") return ASCEND_PLUGIN_TYPE_INT64;
    if (name == "double") return ASCEND_PLUGIN_TYPE_DOUBLE;
    if (name == "string") return ASCEND_PLUGIN_TYPE_STRING;
    throw std::runtime_error("unsupported stable type in plugin manifest: " + name);
}

struct DeclarationInfo {
    AscendPluginKind kind = ASCEND_PLUGIN_KIND_VALUE;
    AscendPluginType result = ASCEND_PLUGIN_TYPE_VOID;
    std::vector<AscendPluginType> parameters;
};

struct Storage {
    explicit Storage(const Definition& source) : definition(source) {
        ascend::Engine probe;
        probe.add(definition.factory("probe", definition.default_config));
        manifest = ascend::export_module_manifest(probe, "", "probe");
        if (!manifest.requirements.empty()) {
            throw std::runtime_error("C ABI v1 plugins cannot declare requirements");
        }
        for (const auto& declaration : manifest.declarations) {
            if (!declaration.module.empty()) {
                throw std::runtime_error("C ABI v1 plugins support flat modules only");
            }
            if (!declaration.reads.empty() || !declaration.writes.empty()) {
                throw std::runtime_error(
                    "C ABI v1 plugins cannot declare value reads or writes");
            }
        }

        parameters.resize(manifest.declarations.size());
        declarations.resize(manifest.declarations.size());
        for (std::size_t index = 0; index < manifest.declarations.size(); ++index) {
            const auto& source_declaration = manifest.declarations[index];
            auto& parameter = parameters[index];
            parameter.reserve(source_declaration.parameters.size());
            for (const auto& item : source_declaration.parameters) {
                parameter.push_back(
                    AscendPluginParameter{intern(item.name), type_from_name(item.type)});
            }
            AscendPluginDeclaration declaration;
            declaration.symbol = intern(source_declaration.symbol);
            declaration.kind = source_declaration.kind == ascend::SymbolKind::method
                                   ? ASCEND_PLUGIN_KIND_METHOD
                                   : ASCEND_PLUGIN_KIND_VALUE;
            declaration.result_type = type_from_name(source_declaration.result_type);
            declaration.parameters = parameter.data();
            declaration.parameter_count = parameter.size();
            if (source_declaration.description.is_literal()) {
                declaration.description_domain = {};
                declaration.description_key = intern(source_declaration.description.literal());
                declaration.description_fallback = {};
            } else {
                declaration.description_domain = intern(source_declaration.description.key().domain);
                declaration.description_key = intern(source_declaration.description.key().key);
                declaration.description_fallback =
                    source_declaration.description.fallback().has_value()
                        ? intern(*source_declaration.description.fallback())
                        : AscendPluginString{};
            }
            declaration.contract = intern(source_declaration.contract);
            declarations[index] = declaration;

            DeclarationInfo info;
            info.kind = declaration.kind;
            info.result = declaration.result_type;
            for (const auto& item : source_declaration.parameters) {
                info.parameters.push_back(type_from_name(item.type));
            }
            by_symbol.emplace(source_declaration.symbol, std::move(info));
        }

        callback_table.abi_version = ASCEND_PLUGIN_ABI_VERSION;
        callback_table.platform = intern(platform_name());
        callback_table.definition = intern(definition.definition);
        callback_table.implementation = intern(definition.implementation);
        callback_table.version = intern(definition.version);
        callback_table.stateless = manifest.stateless ? 1 : 0;
        callback_table.state_contract = intern(manifest.state_contract);
        callback_table.default_config_json = intern(definition.default_config.to_json());
        callback_table.declarations = declarations.data();
        callback_table.declaration_count = declarations.size();
        callback_table.create = &Storage::create_callback;
        callback_table.destroy = &Storage::destroy_callback;
        callback_table.read = &Storage::read_callback;
        callback_table.call = &Storage::call_callback;
        callback_table.capture = &Storage::capture_callback;
        callback_table.restore = &Storage::restore_callback;
    }

    AscendPluginString intern(const std::string& value) {
        strings.push_back(value);
        return to_c(strings.back());
    }

    struct Instance {
        std::string name;
        std::unique_ptr<ascend::Engine> engine;
        std::string text;  // 最近一次字符串结果或错误，保证 C 指针在下一次回调前有效
        std::string error;
    };

    static int32_t fail(Instance* instance, const AscendPluginError* error,
                        AscendPluginError* output) noexcept {
        (void)error;
        if (instance != nullptr && !instance->error.empty()) {
            if (output != nullptr) {
                output->code = ASCEND_PLUGIN_ERROR;
                output->message = to_c(instance->error);
            }
            return ASCEND_PLUGIN_ERROR;
        }
        if (output != nullptr) {
            output->code = ASCEND_PLUGIN_ERROR;
            output->message = {};
        }
        return ASCEND_PLUGIN_ERROR;
    }

    static int32_t create_callback(AscendPluginString instance_value,
                                   AscendPluginString config_json, void** state,
                                   AscendPluginError* error) noexcept {
        if (state == nullptr) {
            if (error != nullptr) error->code = ASCEND_PLUGIN_ERROR;
            return ASCEND_PLUGIN_ERROR;
        }
        try {
            auto instance = std::make_unique<Instance>();
            instance->name.assign(instance_value.data, instance_value.size);
            ascend::Config config;
            if (config_json.size != 0) {
                config = ascend::Config::parse(
                    std::string(config_json.data, config_json.size), "plugin config");
            }
            auto engine = std::make_unique<ascend::Engine>();
            engine->add(global().definition.factory(instance->name, config));
            engine->seal();
            instance->engine = std::move(engine);
            *state = instance.release();
            return ASCEND_PLUGIN_OK;
        } catch (const std::exception& exception) {
            static thread_local std::string create_error;
            create_error = exception.what();
            if (error != nullptr) {
                error->code = ASCEND_PLUGIN_ERROR;
                error->message = to_c(create_error);
            }
            return ASCEND_PLUGIN_ERROR;
        }
    }

    static void destroy_callback(void* state) { delete static_cast<Instance*>(state); }

    static const DeclarationInfo* find(const std::string& symbol) noexcept {
        const auto found = global().by_symbol.find(symbol);
        return found == global().by_symbol.end() ? nullptr : &found->second;
    }

    static int32_t read_callback(void* state, AscendPluginString symbol,
                                 AscendPluginValue* result, AscendPluginError* error) noexcept {
        auto* instance = static_cast<Instance*>(state);
        if (instance == nullptr || result == nullptr) {
            if (error != nullptr) error->code = ASCEND_PLUGIN_ERROR;
            return ASCEND_PLUGIN_ERROR;
        }
        try {
            const std::string name(symbol.data, symbol.size);
            const auto* declaration = find(name);
            if (declaration == nullptr || declaration->kind != ASCEND_PLUGIN_KIND_VALUE) {
                instance->error = "Unknown public value: " + name;
                return fail(instance, nullptr, error);
            }
            const std::any value = instance->engine->read({instance->name, name});
            switch (declaration->result) {
                case ASCEND_PLUGIN_TYPE_BOOL:
                    result->type = ASCEND_PLUGIN_TYPE_BOOL;
                    result->data.boolean = std::any_cast<bool>(value) ? 1 : 0;
                    break;
                case ASCEND_PLUGIN_TYPE_INT64:
                    result->type = ASCEND_PLUGIN_TYPE_INT64;
                    result->data.integer = std::any_cast<std::int64_t>(value);
                    break;
                case ASCEND_PLUGIN_TYPE_DOUBLE:
                    result->type = ASCEND_PLUGIN_TYPE_DOUBLE;
                    result->data.number = std::any_cast<double>(value);
                    break;
                case ASCEND_PLUGIN_TYPE_STRING:
                    instance->text = std::any_cast<std::string>(value);
                    result->type = ASCEND_PLUGIN_TYPE_STRING;
                    result->data.string = to_c(instance->text);
                    break;
                default:
                    instance->error = "Unsupported public value type";
                    return fail(instance, nullptr, error);
            }
            return ASCEND_PLUGIN_OK;
        } catch (const std::exception& exception) {
            instance->error = exception.what();
            return fail(instance, nullptr, error);
        }
    }

    static std::any argument(const AscendPluginValue& value, AscendPluginType expected) {
        if (value.type != expected) {
            throw std::runtime_error("Plugin argument type mismatch");
        }
        switch (expected) {
            case ASCEND_PLUGIN_TYPE_BOOL: return value.data.boolean != 0;
            case ASCEND_PLUGIN_TYPE_INT64: return static_cast<std::int64_t>(value.data.integer);
            case ASCEND_PLUGIN_TYPE_DOUBLE: return value.data.number;
            case ASCEND_PLUGIN_TYPE_STRING:
                return value.data.string.size == 0
                           ? std::string{}
                           : std::string(value.data.string.data, value.data.string.size);
            default:
                throw std::runtime_error("Plugin argument type is not supported");
        }
    }

    static int32_t call_callback(void* state, AscendPluginString symbol,
                                 const AscendPluginValue* arguments, size_t argument_count,
                                 AscendPluginValue* result,
                                 AscendPluginError* error) noexcept {
        auto* instance = static_cast<Instance*>(state);
        if (instance == nullptr) {
            if (error != nullptr) error->code = ASCEND_PLUGIN_ERROR;
            return ASCEND_PLUGIN_ERROR;
        }
        try {
            const std::string name(symbol.data, symbol.size);
            const auto* declaration = find(name);
            if (declaration == nullptr || declaration->kind != ASCEND_PLUGIN_KIND_METHOD) {
                instance->error = "Unknown public method: " + name;
                return fail(instance, nullptr, error);
            }
            if (argument_count != declaration->parameters.size()) {
                instance->error = "Plugin argument count does not match the declaration";
                return fail(instance, nullptr, error);
            }
            std::vector<std::any> converted;
            converted.reserve(argument_count);
            for (std::size_t index = 0; index < argument_count; ++index) {
                converted.push_back(argument(arguments[index], declaration->parameters[index]));
            }
            const std::any value = instance->engine->call({instance->name, name}, converted);
            if (result != nullptr) {
                switch (declaration->result) {
                    case ASCEND_PLUGIN_TYPE_VOID:
                        result->type = ASCEND_PLUGIN_TYPE_VOID;
                        break;
                    case ASCEND_PLUGIN_TYPE_BOOL:
                        result->type = ASCEND_PLUGIN_TYPE_BOOL;
                        result->data.boolean = std::any_cast<bool>(value) ? 1 : 0;
                        break;
                    case ASCEND_PLUGIN_TYPE_INT64:
                        result->type = ASCEND_PLUGIN_TYPE_INT64;
                        result->data.integer = std::any_cast<std::int64_t>(value);
                        break;
                    case ASCEND_PLUGIN_TYPE_DOUBLE:
                        result->type = ASCEND_PLUGIN_TYPE_DOUBLE;
                        result->data.number = std::any_cast<double>(value);
                        break;
                    case ASCEND_PLUGIN_TYPE_STRING:
                        instance->text = std::any_cast<std::string>(value);
                        result->type = ASCEND_PLUGIN_TYPE_STRING;
                        result->data.string = to_c(instance->text);
                        break;
                    default:
                        instance->error = "Unsupported method result type";
                        return fail(instance, nullptr, error);
                }
            }
            return ASCEND_PLUGIN_OK;
        } catch (const std::exception& exception) {
            instance->error = exception.what();
            return fail(instance, nullptr, error);
        }
    }

    static int32_t capture_callback(void* state, AscendPluginString* output,
                                    AscendPluginError* error) noexcept {
        auto* instance = static_cast<Instance*>(state);
        if (instance == nullptr || output == nullptr) {
            if (error != nullptr) error->code = ASCEND_PLUGIN_ERROR;
            return ASCEND_PLUGIN_ERROR;
        }
        try {
            const auto snapshot = instance->engine->capture_state();
            // C ABI v1 是平坦模块；单模块时直接透出模块状态，使插件与内置载体
            // 使用同一状态载荷。多模块代理保留包装结构供以后嵌套扩展。
            if (snapshot.modules.size() == 1 &&
                snapshot.modules.front().path == instance->name &&
                !snapshot.modules.front().stateless) {
                instance->text = snapshot.modules.front().state.to_json();
            } else {
                std::vector<ascend::Config> modules;
                modules.reserve(snapshot.modules.size());
                for (const auto& module : snapshot.modules) {
                    modules.push_back(ascend::Config::object(
                        {{"path", ascend::Config::string(module.path)},
                         {"contract", ascend::Config::string(module.contract)},
                         {"stateless", ascend::Config::boolean(module.stateless)},
                         {"state", module.state}}));
                }
                instance->text = ascend::Config::object(
                                     {{"modules", ascend::Config::array(std::move(modules))}})
                                     .to_json();
            }
            *output = to_c(instance->text);
            return ASCEND_PLUGIN_OK;
        } catch (const std::exception& exception) {
            instance->error = exception.what();
            return fail(instance, nullptr, error);
        }
    }

    static int32_t restore_callback(void* state, AscendPluginString state_json,
                                    AscendPluginError* error) noexcept {
        auto* instance = static_cast<Instance*>(state);
        if (instance == nullptr) {
            if (error != nullptr) error->code = ASCEND_PLUGIN_ERROR;
            return ASCEND_PLUGIN_ERROR;
        }
        try {
            const auto value = ascend::Config::parse(
                std::string(state_json.data, state_json.size), "plugin state");
            const auto* modules = value.kind() == ascend::Config::Kind::object
                                      ? value.find("modules")
                                      : nullptr;
            ascend::StateSnapshot snapshot;
            if (modules != nullptr) {
                if (modules->kind() != ascend::Config::Kind::array) {
                    throw std::invalid_argument("Plugin state modules field must be an array");
                }
                for (const auto& module : modules->elements()) {
                    if (module.kind() != ascend::Config::Kind::object) {
                        throw std::invalid_argument("Plugin state module entry must be an object");
                    }
                    const auto* path = module.find("path");
                    const auto* contract = module.find("contract");
                    const auto* stateless = module.find("stateless");
                    const auto* module_state = module.find("state");
                    if (path == nullptr || path->kind() != ascend::Config::Kind::string ||
                        contract == nullptr ||
                        contract->kind() != ascend::Config::Kind::string ||
                        stateless == nullptr ||
                        stateless->kind() != ascend::Config::Kind::boolean ||
                        module_state == nullptr) {
                        throw std::invalid_argument(
                            "Plugin state module entry has the wrong structure");
                    }
                    snapshot.modules.push_back(ascend::ModuleState{
                        path->string(), contract->string(), stateless->boolean(), *module_state});
                }
            } else {
                snapshot.modules.push_back(ascend::ModuleState{
                    instance->name, global().manifest.state_contract, false, value});
            }
            instance->engine->restore_state(snapshot);
            return ASCEND_PLUGIN_OK;
        } catch (const std::exception& exception) {
            instance->error = exception.what();
            return fail(instance, nullptr, error);
        }
    }

    static Storage& global();

    Definition definition;
    ascend::ModuleManifest manifest;
    std::deque<std::string> strings;
    std::vector<std::vector<AscendPluginParameter>> parameters;
    std::vector<AscendPluginDeclaration> declarations;
    std::map<std::string, DeclarationInfo> by_symbol;
    AscendPluginCallbacks callback_table{};
};

Storage* g_storage = nullptr;

Storage& Storage::global() { return *g_storage; }

}  // namespace

const AscendPluginCallbacks* callbacks(const Definition& definition) {
    try {
        static Storage storage(definition);
        g_storage = &storage;
        return &storage.callback_table;
    } catch (...) {
        return nullptr;
    }
}

}  // namespace olam::plugin
