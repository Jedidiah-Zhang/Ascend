#pragma once

#include <ascend/assembly.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>

#include <string>
#include <vector>

namespace olam::module {

// 空间模块的声明接入标识（ENV-17、ENV-18）。
// 模块实例对应一个空间集合；默认构造配置为单个空间。空间创建后固定，
// 运行状态按状态契约捕获与恢复，供检查点、分支和记录重放使用。
inline constexpr const char* space_definition = "olam.space";
inline constexpr const char* space_implementation = "olam.space.v1";
inline constexpr const char* space_version = "1.0";
inline constexpr const char* space_text_domain = "olam.space";
inline constexpr const char* space_state_contract = "olam.space.state.v1";

// 系统编辑器新建实例时预填的默认构造配置：单个空间，原点 (0, 0)，尺寸 1000×1000，层序 0。
ascend::Config space_preset();

// 构造空间模块实例。config 为 null 或空对象时使用一个默认空间；对象可直接描述
// 单个空间，也可用 `spaces` 数组描述同一集合内的多个空间（见实现文档）。
// 空间身份缺省取实例名（数组元素取 `实例名#序号`），创建校验失败转换为引擎诊断。
ascend::Module make_space_module(const std::string& instance, const ascend::Config& config);

// 登记内置实现；定义标识、实现标识与默认构造配置一并进入模块库。
void register_space_module(ascend::ModuleLibrary& library);

// 生成可载入的模块包；resources 为包内语言资源（通常为 zh-CN JSON 文本）。
ascend::ModulePackage make_space_package(std::vector<ascend::ModuleResource> resources = {});
std::string space_package_bytes(std::vector<ascend::ModuleResource> resources = {});

}  // namespace olam::module
