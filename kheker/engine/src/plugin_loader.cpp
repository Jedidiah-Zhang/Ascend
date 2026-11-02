#include "plugin_loader.hpp"

#include <ascend/config.hpp>
#include <ascend/engine.hpp>

#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>
#include <type_traits>
#include <utility>
#include <vector>

#if defined(_WIN32)
#include <windows.h>
#else
#include <dlfcn.h>
#include <sys/stat.h>
#include <unistd.h>
#endif

namespace ascend::detail {
namespace {

using Entry = const AscendPluginCallbacks* (*)();

[[noreturn]] void fail(ErrorCode code, const char* key, std::string message,
                       const Reference& target = {}) {
    throw EngineError({code, target,
                       TextRef(TextKey{engine_text_domain, key}, std::move(message))});
}

std::string string_of(AscendPluginString value) {
    if (value.size == 0) return {};
    return std::string(value.data, value.size);
}

AscendPluginString to_c(const std::string& value) noexcept {
    return AscendPluginString{value.data(), value.size()};
}

std::filesystem::path temporary_library_path() {
    static std::atomic<std::uint64_t> counter{0};
    const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
#if defined(_WIN32)
    const char* suffix = ".dll";
#elif defined(__APPLE__)
    const char* suffix = ".dylib";
#else
    const char* suffix = ".so";
#endif
    return std::filesystem::temp_directory_path() /
           ("ascend-plugin-" + std::to_string(stamp) + "-" + std::to_string(counter++) + suffix);
}

void ensure_executable(const std::filesystem::path& path) {
#if !defined(_WIN32)
    if (::chmod(path.c_str(), S_IRUSR | S_IWUSR | S_IXUSR) != 0) {
        fail(ErrorCode::io_failure, "plugin_loader.permissions",
             "Failed to set plugin temporary file permissions", {path.u8string(), {}});
    }
#endif
}

void* open_library(const std::filesystem::path& path, std::string& message) {
#if defined(_WIN32)
    HMODULE handle = LoadLibraryW(path.wstring().c_str());
    if (handle == nullptr) message = "LoadLibrary failed";
    return handle;
#else
    void* handle = ::dlopen(path.c_str(), RTLD_NOW | RTLD_LOCAL);
    if (handle == nullptr) {
        const char* error = ::dlerror();
        message = error == nullptr ? "dlopen failed" : error;
    }
    return handle;
#endif
}

Entry resolve_entry(void* library, std::string& message) {
#if defined(_WIN32)
    FARPROC symbol = GetProcAddress(static_cast<HMODULE>(library), ASCEND_PLUGIN_ENTRY_SYMBOL);
    if (symbol == nullptr) message = "plugin entry symbol is missing";
    return reinterpret_cast<Entry>(symbol);
#else
    void* symbol = ::dlsym(library, ASCEND_PLUGIN_ENTRY_SYMBOL);
    if (symbol == nullptr) message = "plugin entry symbol is missing";
    return reinterpret_cast<Entry>(symbol);
#endif
}

void close_library(void* library) {
    if (library == nullptr) return;
#if defined(_WIN32)
    FreeLibrary(static_cast<HMODULE>(library));
#else
    ::dlclose(library);
#endif
}

}  // namespace

std::string plugin_platform_name() {
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

PluginHandle::PluginHandle(const AscendPluginCallbacks* callbacks, void* library,
                           std::filesystem::path path, bool remove_on_destroy)
    : callbacks_(callbacks), library_(library), path_(std::move(path)),
      remove_on_destroy_(remove_on_destroy) {}

PluginHandle::~PluginHandle() {
    close_library(library_);
    if (remove_on_destroy_) {
        std::error_code error;
        std::filesystem::remove(path_, error);
    }
}

std::shared_ptr<PluginHandle> load_plugin(const ModuleBinary& binary,
                                          const ModuleManifest& manifest) {
    if (binary.format != ASCEND_PLUGIN_BINARY_FORMAT) {
        fail(ErrorCode::invalid_config, "plugin_loader.format",
             "Module package binary format is not supported", {manifest.definition, {}});
    }
    if (binary.platform != plugin_platform_name()) {
        fail(ErrorCode::invalid_config, "plugin_loader.platform",
             "Module package binary platform does not match this host: " + binary.platform,
             {manifest.definition, {}});
    }
    if (binary.entry != ASCEND_PLUGIN_ENTRY_SYMBOL) {
        fail(ErrorCode::invalid_config, "plugin_loader.entry",
             "Module package binary entry is not supported", {manifest.definition, {}});
    }

    const auto path = temporary_library_path();
    {
        std::ofstream stream(path, std::ios::binary | std::ios::trunc);
        if (!stream.is_open()) {
            fail(ErrorCode::io_failure, "plugin_loader.write",
                 "Cannot create temporary plugin library", {path.u8string(), {}});
        }
        stream.write(binary.bytes.data(), static_cast<std::streamsize>(binary.bytes.size()));
        stream.close();
        if (!stream) {
            std::error_code ignored;
            std::filesystem::remove(path, ignored);
            fail(ErrorCode::io_failure, "plugin_loader.write",
                 "Cannot write temporary plugin library", {path.u8string(), {}});
        }
    }
    try {
        ensure_executable(path);
    } catch (...) {
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        throw;
    }

    std::string message;
    void* library = open_library(path, message);
    if (library == nullptr) {
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.open", message, {manifest.definition, {}});
    }
    const Entry entry = resolve_entry(library, message);
    if (entry == nullptr) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.entry", message, {manifest.definition, {}});
    }
    const AscendPluginCallbacks* callbacks = entry();
    if (callbacks == nullptr) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.callbacks",
             "Plugin did not return a callback table", {manifest.definition, {}});
    }
    if (callbacks->abi_version != ASCEND_PLUGIN_ABI_VERSION) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.abi",
             "Plugin ABI version is not supported", {manifest.definition, {}});
    }
    const std::string definition = string_of(callbacks->definition);
    const std::string implementation = string_of(callbacks->implementation);
    const std::string version = string_of(callbacks->version);
    const std::string platform = string_of(callbacks->platform);
    if (definition != manifest.definition || implementation != manifest.implementation ||
        version != manifest.version || platform != binary.platform) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.identity",
             "Plugin identity does not match the module manifest", {manifest.definition, {}});
    }
    if (callbacks->create == nullptr || callbacks->destroy == nullptr ||
        callbacks->read == nullptr || callbacks->call == nullptr) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.callbacks",
             "Plugin callback table is incomplete", {manifest.definition, {}});
    }
    if (!manifest.stateless &&
        (callbacks->capture == nullptr || callbacks->restore == nullptr)) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.state",
             "Stateful plugin has no capture/restore callbacks", {manifest.definition, {}});
    }
    if (callbacks->declaration_count != 0 && callbacks->declarations == nullptr) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.declarations",
             "Plugin declaration table is missing", {manifest.definition, {}});
    }
    const auto valid_type = [](AscendPluginType type) {
        return type >= ASCEND_PLUGIN_TYPE_VOID && type <= ASCEND_PLUGIN_TYPE_STRING;
    };
    for (std::size_t index = 0; index < callbacks->declaration_count; ++index) {
        const auto& declaration = callbacks->declarations[index];
        const bool valid_kind = declaration.kind == ASCEND_PLUGIN_KIND_VALUE ||
                                declaration.kind == ASCEND_PLUGIN_KIND_METHOD;
        if (!valid_kind || declaration.symbol.size == 0 ||
            !valid_type(declaration.result_type) ||
            (declaration.parameter_count != 0 && declaration.parameters == nullptr)) {
            close_library(library);
            std::error_code ignored;
            std::filesystem::remove(path, ignored);
            fail(ErrorCode::invalid_config, "plugin_loader.declarations",
                 "Plugin declaration table is invalid", {manifest.definition, {}});
        }
        for (std::size_t parameter = 0; parameter < declaration.parameter_count; ++parameter) {
            if (!valid_type(declaration.parameters[parameter].type) ||
                declaration.parameters[parameter].type == ASCEND_PLUGIN_TYPE_VOID) {
                close_library(library);
                std::error_code ignored;
                std::filesystem::remove(path, ignored);
                fail(ErrorCode::invalid_config, "plugin_loader.declarations",
                     "Plugin declaration parameter type is invalid", {manifest.definition, {}});
            }
        }
    }

    Config default_config;
    try {
        const std::string text = string_of(callbacks->default_config_json);
        if (!text.empty()) default_config = Config::parse(text, "plugin default config");
    } catch (const std::exception& error) {
        close_library(library);
        std::error_code ignored;
        std::filesystem::remove(path, ignored);
        fail(ErrorCode::invalid_config, "plugin_loader.default_config", error.what(),
             {manifest.definition, {}});
    }

    bool remove_on_destroy = false;
#if defined(_WIN32)
    remove_on_destroy = true;
#else
    std::error_code ignored;
    std::filesystem::remove(path, ignored);
#endif
    auto handle = std::make_shared<PluginHandle>(callbacks, library, path, remove_on_destroy);
    handle->definition_ = definition;
    handle->implementation_ = implementation;
    handle->version_ = version;
    handle->platform_ = platform;
    handle->state_contract_ = string_of(callbacks->state_contract);
    handle->stateless_ = callbacks->stateless != 0;
    handle->default_config_ = std::move(default_config);
    return handle;
}

namespace {

struct PluginInstance {
    std::shared_ptr<PluginHandle> handle;
    void* state = nullptr;
    std::string instance;
    std::string text;
    std::string error;

    ~PluginInstance() {
        if (state != nullptr && handle && handle->callbacks()->destroy != nullptr) {
            handle->callbacks()->destroy(state);
        }
    }
};

[[noreturn]] void fail_plugin(const AscendPluginError& error, ErrorCode code,
                              const Reference& target) {
    const std::string message = string_of(error.message);
    throw EngineError({code, target,
                       TextRef(message.empty() ? "Plugin callback failed" : message)});
}

TextRef plugin_text(const AscendPluginDeclaration& declaration) {
    if (declaration.description_domain.size == 0) {
        return TextRef(string_of(declaration.description_key));
    }
    std::optional<std::string> fallback;
    if (declaration.description_fallback.size != 0) {
        fallback = string_of(declaration.description_fallback);
    }
    return TextRef(TextKey{string_of(declaration.description_domain),
                           string_of(declaration.description_key)},
                   std::move(fallback));
}

AscendPluginValue value_of(bool value) {
    AscendPluginValue result{};
    result.type = ASCEND_PLUGIN_TYPE_BOOL;
    result.data.boolean = value ? 1 : 0;
    return result;
}

AscendPluginValue value_of(std::int64_t value) {
    AscendPluginValue result{};
    result.type = ASCEND_PLUGIN_TYPE_INT64;
    result.data.integer = value;
    return result;
}

AscendPluginValue value_of(double value) {
    AscendPluginValue result{};
    result.type = ASCEND_PLUGIN_TYPE_DOUBLE;
    result.data.number = value;
    return result;
}

AscendPluginValue value_of(const std::string& value) {
    AscendPluginValue result{};
    result.type = ASCEND_PLUGIN_TYPE_STRING;
    result.data.string = to_c(value);
    return result;
}

template <class Type>
Type read_plugin_value(const std::shared_ptr<PluginInstance>& instance, const std::string& symbol) {
    AscendPluginValue value{};
    AscendPluginError error{};
    const auto status = instance->handle->callbacks()->read(
        instance->state, to_c(symbol), &value, &error);
    if (status != ASCEND_PLUGIN_OK) {
        fail_plugin(error, ErrorCode::execution_failed, {instance->instance, symbol});
    }
    if constexpr (std::is_same_v<Type, bool>) {
        if (value.type != ASCEND_PLUGIN_TYPE_BOOL) {
            throw EngineError({ErrorCode::type_mismatch, {instance->instance, symbol},
                               TextRef("Plugin value type does not match the declaration")});
        }
        return value.data.boolean != 0;
    } else if constexpr (std::is_same_v<Type, std::int64_t>) {
        if (value.type != ASCEND_PLUGIN_TYPE_INT64) {
            throw EngineError({ErrorCode::type_mismatch, {instance->instance, symbol},
                               TextRef("Plugin value type does not match the declaration")});
        }
        return value.data.integer;
    } else if constexpr (std::is_same_v<Type, double>) {
        if (value.type != ASCEND_PLUGIN_TYPE_DOUBLE) {
            throw EngineError({ErrorCode::type_mismatch, {instance->instance, symbol},
                               TextRef("Plugin value type does not match the declaration")});
        }
        return value.data.number;
    } else {
        if (value.type != ASCEND_PLUGIN_TYPE_STRING) {
            throw EngineError({ErrorCode::type_mismatch, {instance->instance, symbol},
                               TextRef("Plugin value type does not match the declaration")});
        }
        return string_of(value.data.string);
    }
}

template <class Type>
Type result_plugin_value(const PluginInstance& instance, const std::string& symbol,
                         const AscendPluginValue& value) {
    if constexpr (std::is_same_v<Type, bool>) {
        if (value.type != ASCEND_PLUGIN_TYPE_BOOL) {
            throw EngineError({ErrorCode::type_mismatch, {instance.instance, symbol},
                               TextRef("Plugin result type does not match the declaration")});
        }
        return value.data.boolean != 0;
    } else if constexpr (std::is_same_v<Type, std::int64_t>) {
        if (value.type != ASCEND_PLUGIN_TYPE_INT64) {
            throw EngineError({ErrorCode::type_mismatch, {instance.instance, symbol},
                               TextRef("Plugin result type does not match the declaration")});
        }
        return value.data.integer;
    } else if constexpr (std::is_same_v<Type, double>) {
        if (value.type != ASCEND_PLUGIN_TYPE_DOUBLE) {
            throw EngineError({ErrorCode::type_mismatch, {instance.instance, symbol},
                               TextRef("Plugin result type does not match the declaration")});
        }
        return value.data.number;
    } else {
        if (value.type != ASCEND_PLUGIN_TYPE_STRING) {
            throw EngineError({ErrorCode::type_mismatch, {instance.instance, symbol},
                               TextRef("Plugin result type does not match the declaration")});
        }
        return string_of(value.data.string);
    }
}

template <class Result, class... Args>
void add_typed_method(Module& module, const std::shared_ptr<PluginInstance>& instance,
                      const AscendPluginDeclaration& declaration) {
    std::vector<std::string> names;
    names.reserve(declaration.parameter_count);
    for (std::size_t index = 0; index < declaration.parameter_count; ++index) {
        names.push_back(string_of(declaration.parameters[index].name));
    }
    MethodOptions options;
    options.description = plugin_text(declaration);
    options.contract = string_of(declaration.contract);
    const std::string symbol = string_of(declaration.symbol);
    module.add_method<Result, Args...>(
        symbol, std::move(names),
        [instance, symbol](const Args&... arguments) -> Result {
            const std::array<AscendPluginValue, sizeof...(Args)> values{
                value_of(arguments)...};
            AscendPluginValue result{};
            AscendPluginError error{};
            const auto status = instance->handle->callbacks()->call(
                instance->state, to_c(symbol), values.data(), values.size(), &result, &error);
            if (status != ASCEND_PLUGIN_OK) {
                fail_plugin(error, ErrorCode::execution_failed,
                            {instance->instance, symbol});
            }
            if constexpr (!std::is_void_v<Result>) {
                return result_plugin_value<Result>(*instance, symbol, result);
            }
        },
        std::move(options));
}

template <std::size_t Depth, class Result, class... Args>
void dispatch_method(Module& module, const std::shared_ptr<PluginInstance>& instance,
                     const AscendPluginDeclaration& declaration, std::size_t index) {
    if (index == declaration.parameter_count) {
        add_typed_method<Result, Args...>(module, instance, declaration);
        return;
    }
    if constexpr (Depth < 3) {
        switch (declaration.parameters[index].type) {
            case ASCEND_PLUGIN_TYPE_BOOL:
                dispatch_method<Depth + 1, Result, Args..., bool>(module, instance, declaration,
                                                                  index + 1);
                return;
            case ASCEND_PLUGIN_TYPE_INT64:
                dispatch_method<Depth + 1, Result, Args..., std::int64_t>(
                    module, instance, declaration, index + 1);
                return;
            case ASCEND_PLUGIN_TYPE_DOUBLE:
                dispatch_method<Depth + 1, Result, Args..., double>(module, instance, declaration,
                                                                    index + 1);
                return;
            case ASCEND_PLUGIN_TYPE_STRING:
                dispatch_method<Depth + 1, Result, Args..., std::string>(
                    module, instance, declaration, index + 1);
                return;
            default:
                throw std::runtime_error("Plugin method parameter type is not supported");
        }
    }
    throw std::runtime_error("C ABI v1 supports at most three method parameters");
}

template <class Result>
void dispatch_result(Module& module, const std::shared_ptr<PluginInstance>& instance,
                     const AscendPluginDeclaration& declaration) {
    if (declaration.parameter_count > 3) {
        throw std::runtime_error("C ABI v1 supports at most three method parameters");
    }
    dispatch_method<0, Result>(module, instance, declaration, 0);
}

void add_value(Module& module, const std::shared_ptr<PluginInstance>& instance,
               const AscendPluginDeclaration& declaration) {
    const std::string symbol = string_of(declaration.symbol);
    switch (declaration.result_type) {
        case ASCEND_PLUGIN_TYPE_BOOL:
            module.add_value<bool>(symbol,
                                   [instance, symbol] {
                                       return read_plugin_value<bool>(instance, symbol);
                                   },
                                   plugin_text(declaration), string_of(declaration.contract));
            break;
        case ASCEND_PLUGIN_TYPE_INT64:
            module.add_value<std::int64_t>(
                symbol,
                [instance, symbol] { return read_plugin_value<std::int64_t>(instance, symbol); },
                plugin_text(declaration), string_of(declaration.contract));
            break;
        case ASCEND_PLUGIN_TYPE_DOUBLE:
            module.add_value<double>(
                symbol, [instance, symbol] { return read_plugin_value<double>(instance, symbol); },
                plugin_text(declaration), string_of(declaration.contract));
            break;
        case ASCEND_PLUGIN_TYPE_STRING:
            module.add_value<std::string>(
                symbol,
                [instance, symbol] { return read_plugin_value<std::string>(instance, symbol); },
                plugin_text(declaration), string_of(declaration.contract));
            break;
        default:
            throw std::runtime_error("Plugin public value has an unsupported type");
    }
}

void add_method(Module& module, const std::shared_ptr<PluginInstance>& instance,
                const AscendPluginDeclaration& declaration) {
    switch (declaration.result_type) {
        case ASCEND_PLUGIN_TYPE_VOID:
            dispatch_result<void>(module, instance, declaration);
            break;
        case ASCEND_PLUGIN_TYPE_BOOL:
            dispatch_result<bool>(module, instance, declaration);
            break;
        case ASCEND_PLUGIN_TYPE_INT64:
            dispatch_result<std::int64_t>(module, instance, declaration);
            break;
        case ASCEND_PLUGIN_TYPE_DOUBLE:
            dispatch_result<double>(module, instance, declaration);
            break;
        case ASCEND_PLUGIN_TYPE_STRING:
            dispatch_result<std::string>(module, instance, declaration);
            break;
        default:
            throw std::runtime_error("Plugin public method has an unsupported result type");
    }
}

}  // namespace

Module make_plugin_module(std::shared_ptr<PluginHandle> handle, const std::string& instance,
                          const Config& config) {
    const std::string config_json = config.to_json();
    void* state = nullptr;
    AscendPluginError error{};
    const AscendPluginString instance_value = to_c(instance);
    const AscendPluginString config_value = to_c(config_json);
    if (handle->callbacks()->create(instance_value, config_value, &state, &error) !=
        ASCEND_PLUGIN_OK) {
        const std::string message = string_of(error.message);
        throw EngineError({ErrorCode::invalid_config, {instance, {}},
                           TextRef(message.empty() ? "Plugin create failed" : message)});
    }
    auto instance_state = std::make_shared<PluginInstance>();
    instance_state->handle = handle;
    instance_state->state = state;
    instance_state->instance = instance;

    Module module(instance);
    for (std::size_t index = 0; index < handle->callbacks()->declaration_count; ++index) {
        const auto& declaration = handle->callbacks()->declarations[index];
        if (declaration.kind == ASCEND_PLUGIN_KIND_VALUE) {
            add_value(module, instance_state, declaration);
        } else if (declaration.kind == ASCEND_PLUGIN_KIND_METHOD) {
            add_method(module, instance_state, declaration);
        } else {
            throw std::runtime_error("Plugin declaration kind is not supported");
        }
    }
    if (handle->stateless()) {
        module.declare_stateless();
    } else {
        const std::string symbol = instance;
        module.add_state(
            handle->state_contract(),
            [instance_state] {
                AscendPluginString json{};
                AscendPluginError capture_error{};
                if (instance_state->handle->callbacks()->capture(
                        instance_state->state, &json, &capture_error) != ASCEND_PLUGIN_OK) {
                    const std::string message = string_of(capture_error.message);
                    throw EngineError({ErrorCode::invalid_state, {instance_state->handle->definition(), {}},
                                       TextRef(message.empty() ? "Plugin state capture failed" : message)});
                }
                return Config::parse(string_of(json), "plugin state");
            },
            [instance_state](const Config& state_value) {
                const std::string json = state_value.to_json();
                AscendPluginError restore_error{};
                if (instance_state->handle->callbacks()->restore(
                        instance_state->state, to_c(json), &restore_error) != ASCEND_PLUGIN_OK) {
                    const std::string message = string_of(restore_error.message);
                    throw EngineError({ErrorCode::invalid_state, {instance_state->handle->definition(), {}},
                                       TextRef(message.empty() ? "Plugin state restore failed" : message)});
                }
            });
    }
    return module;
}

}  // namespace ascend::detail
