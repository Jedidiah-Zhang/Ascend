#pragma once

#include <ascend/assembly.hpp>
#include <ascend/engine.hpp>
#include <ascend/plugin.h>

#include <functional>
#include <string>

namespace olam::plugin {

// 一个插件库只导出一个模块定义；工厂仍使用进程内 C++ 接口，C ABI 只存在于
// DSO 边界。requirements、子模块、转接与导出不在首版 C ABI 范围内。
struct Definition {
    std::string definition;
    std::string implementation;
    std::string version;
    ascend::Config default_config;
    std::function<ascend::Module(const std::string&, const ascend::Config&)> factory;
};

// 生成并缓存 C ABI 描述表；返回指针在进程生命周期内有效。
const AscendPluginCallbacks* callbacks(const Definition& definition);

}  // namespace olam::plugin
