#pragma once

#include <ascend/assembly.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>
#include <olam/time.hpp>

#include <cstdint>
#include <string>
#include <vector>

namespace olam::module {

// 基础时钟模块的声明接入标识（ENV-17、ENV-18）。
// 模块实例对应一个单根时钟树；公开值描述根时钟，模块状态按契约捕获与恢复。
inline constexpr const char* time_definition = "olam.clock";
inline constexpr const char* time_implementation = "olam.clock.v1";
inline constexpr const char* time_version = "1.0";
inline constexpr const char* time_text_domain = "olam.clock";
inline constexpr const char* time_state_contract = "olam.clock.state.v1";

// 首版模块使用的固定示例步长（微秒）。它代表世界基础占位，不作为实例可调参数、
// 公开量或世界默认值；世界基础接入后由宿主在创建时钟时提供。
inline constexpr std::int64_t time_demo_step = time_ticks_per_second / 1000;

// 系统编辑器新建实例时预填的默认构造配置（不包含步长）。
ascend::Config time_preset();

// 构造单根时钟模块实例。config 为 null 或空对象时使用默认配置；步长在构造时
// 固定，运行中只能暂停／恢复和改变相对倍率。
ascend::Module make_time_module(const std::string& instance, const ascend::Config& config);

// 登记内置实现；定义标识、实现标识与默认构造配置一并进入模块库。
void register_time_module(ascend::ModuleLibrary& library);

// 生成可载入的模块包；resources 为包内语言资源（通常为 zh-CN JSON 文本）。
ascend::ModulePackage make_time_package(std::vector<ascend::ModuleResource> resources = {});
std::string time_package_bytes(std::vector<ascend::ModuleResource> resources = {});

}  // namespace olam::module
