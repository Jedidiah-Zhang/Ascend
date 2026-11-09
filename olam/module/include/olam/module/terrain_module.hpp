#pragma once

#include <ascend/assembly.hpp>
#include <ascend/module_library.hpp>
#include <ascend/module_package.hpp>

#include <cstdint>
#include <limits>
#include <string>
#include <vector>

namespace olam::module {

// 地形模块的声明接入标识（ENV-17、ENV-18）。
// 一个模块实例实例化一个 1 米网格空间，并按 seed 确定性生成基础高程；
// 逐格修改是稀疏状态层。首版只提供精确实现，水体与材质另行接入。
inline constexpr const char* terrain_definition = "olam.terrain";
inline constexpr const char* terrain_implementation = "olam.terrain.full.v1";
inline constexpr const char* terrain_version = "1.0";
inline constexpr const char* terrain_text_domain = "olam.terrain";
inline constexpr const char* terrain_state_contract = "olam.terrain.state.v1";

// 地形方法返回整数时的缺失标记。Elevation 覆盖完整 int32，-1 是合法高程，
// 因此不能用作越界哨兵；调用方应先比较该值或先调用 contains。
inline constexpr std::int64_t terrain_missing = std::numeric_limits<std::int64_t>::min();

// 系统编辑器新建实例时预填的默认构造配置：
// 512×512 格、种子 0、六层噪声、特征 128 格、基准 0 毫米、振幅 150000 毫米。
ascend::Config terrain_preset();

// 构造地形模块实例。config 为 null 或空对象时使用默认配置；空间尺寸由
// columns × 1 米与 rows × 1 米推导，origin 为空间绝对原点（毫米）。
ascend::Module make_terrain_module(const std::string& instance, const ascend::Config& config);

// 登记内置实现；定义标识、实现标识与默认构造配置一并进入模块库。
void register_terrain_module(ascend::ModuleLibrary& library);

// 生成可载入的模块包；resources 为包内语言资源（通常为 zh-CN JSON 文本）。
ascend::ModulePackage make_terrain_package(std::vector<ascend::ModuleResource> resources = {});
std::string terrain_package_bytes(std::vector<ascend::ModuleResource> resources = {});

}  // namespace olam::module
