#pragma once

#include <ascend/assembly.hpp>
#include <ascend/experiment.hpp>

#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace ascend::example {

// 确定性三变量示例：x' = x + a; y' = x; z' = y + z，右侧读取步开始值。
// 观测为 64 位有符号整数 (x, y, z)；计算溢出时报错，不产生有符号溢出。
// 命令行示例、工作台与测试共用本组件；独立参考序列见下方常量。
inline constexpr const char* plant_definition = "example.plant";
inline constexpr const char* stimulus_definition = "example.stimulus";
inline constexpr const char* plant_implementation = "example.plant.integer.v1";
inline constexpr const char* stimulus_implementation = "example.stimulus.integer.v1";
inline constexpr const char* text_domain = "example.experiment";

using Integer = std::int64_t;

struct Values {
    Integer x = 0;
    Integer y = 0;
    Integer z = 0;

    bool operator==(const Values& other) const noexcept {
        return x == other.x && y == other.y && z == other.z;
    }
    bool operator!=(const Values& other) const noexcept { return !(*this == other); }
};

// 模块工厂目录。resource_root 非空时为其登记示例语言资源
// （root + "/i18n/zh-CN.json"）；空表示只登记工厂，由宿主自行加载资源。
ModuleFactoryDirectory factories(const std::string& resource_root = {});

// 示例语言资源列表；resource_root 为空时返回空列表。
std::vector<I18nResource> i18n_resources(const std::string& resource_root);

// 装配定义：plant 初值 (x, y, z) 与 stimulus 初值 a；plant/input 连接 input/value。
// 内置世界：刺激初值默认 1，世界按自身配置即可自主演化；宿主可经规格输入 a 覆盖驱动值。
AssemblyDefinition environment(const Values& initial = {}, Integer input = 1);

// 实验规格：推进入口 plant.advance；输入 a；观测 x、y、z。
ExperimentSpec specification();

// 参考干预：在共同检查点上施加 plant/state 的 x := 10。
inline constexpr const char* reference_intervention_module = "plant/state";
inline constexpr const char* reference_intervention_field = "x";
inline constexpr Integer reference_intervention_value = 10;

// 手工核对参考：a = 1 恒定，干预在逻辑帧 2 施加 x := 10。
inline constexpr std::array<Values, 6> reference_control = {
    Values{0, 0, 0}, Values{1, 0, 0}, Values{2, 1, 0},
    Values{3, 2, 1}, Values{4, 3, 3}, Values{5, 4, 6}};
inline constexpr std::array<Values, 6> reference_treated = {
    Values{0, 0, 0}, Values{1, 0, 0}, Values{10, 1, 0},
    Values{11, 10, 1}, Values{12, 11, 11}, Values{13, 12, 22}};

}  // namespace ascend::example
