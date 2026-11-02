#pragma once

#include <ascend/assembly.hpp>
#include <ascend/module_package.hpp>
#include <ascend/plugin.h>

#include <filesystem>
#include <memory>
#include <string>

namespace ascend::detail {

std::string plugin_platform_name();
class PluginHandle;
std::shared_ptr<PluginHandle> load_plugin(const ModuleBinary& binary,
                                          const ModuleManifest& manifest);

// 已加载的动态插件；析构时释放共享库。活动模块回调持有该对象的 shared_ptr，
// 因此卸载登记不会提前 dlclose。
class PluginHandle {
public:
    friend std::shared_ptr<PluginHandle> load_plugin(const ModuleBinary& binary,
                                                     const ModuleManifest& manifest);
    PluginHandle(const AscendPluginCallbacks* callbacks, void* library,
                 std::filesystem::path path, bool remove_on_destroy);
    ~PluginHandle();
    PluginHandle(const PluginHandle&) = delete;
    PluginHandle& operator=(const PluginHandle&) = delete;

    const AscendPluginCallbacks* callbacks() const noexcept { return callbacks_; }
    const std::string& definition() const noexcept { return definition_; }
    const std::string& implementation() const noexcept { return implementation_; }
    const std::string& version() const noexcept { return version_; }
    const std::string& platform() const noexcept { return platform_; }
    const std::string& state_contract() const noexcept { return state_contract_; }
    bool stateless() const noexcept { return stateless_; }
    const Config& default_config() const noexcept { return default_config_; }

private:
    const AscendPluginCallbacks* callbacks_ = nullptr;
    void* library_ = nullptr;
    std::filesystem::path path_;
    bool remove_on_destroy_ = false;
    std::string definition_;
    std::string implementation_;
    std::string version_;
    std::string platform_;
    std::string state_contract_;
    bool stateless_ = false;
    Config default_config_;
};

std::shared_ptr<PluginHandle> load_plugin(const ModuleBinary& binary,
                                          const ModuleManifest& manifest);

// 用 C ABI 回调构造宿主侧代理模块；实例状态由共享指针管理。
Module make_plugin_module(std::shared_ptr<PluginHandle> handle, const std::string& instance,
                          const Config& config);

}  // namespace ascend::detail
