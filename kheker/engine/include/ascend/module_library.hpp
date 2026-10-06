#pragma once

#include <ascend/assembly.hpp>
#include <ascend/i18n.hpp>
#include <ascend/module_package.hpp>

#include <cstddef>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace ascend {

// 已载入模块的概要（ENV-18）。
struct ModuleEntry {
    std::string definition;
    std::string version;
    std::string implementation;
    bool stateless = false;
    std::string state_contract;
    std::size_t declarations = 0;
    std::size_t requirements = 0;
    std::size_t resources = 0;
};

// 模块库：宿主以代码登记内置实现，载入模块包（ENV-18）。
// 库只保存元数据与工厂，不保存模块状态；首版一个定义同时只载入一个包。
class ModuleLibrary {
public:
    using Factory = ModuleFactoryDirectory::Factory;

    // 登记内置实现；preset 为默认构造配置（供系统编辑器预填，可为空）。
    // 定义标识为空、重复或实现标识为空分别报告
    // invalid_declaration／duplicate_definition／invalid_declaration。
    void register_implementation(std::string definition, std::string implementation, Factory factory,
                                 Config preset = {});
    // 载入模块包：解码、解析实现、核对清单与运行时声明、预校验语言资源；
    // 全部通过后一次登记并返回定义标识。失败不登记任何内容。
    std::string load(const std::string& bytes);
    // 卸载已载入模块；返回是否移除。已登记进宿主文案目录的文本不随卸载撤销。
    bool unload(const std::string& definition);
    bool contains(const std::string& definition) const;
    // 按名称排序返回已载入定义标识。
    std::vector<std::string> definitions() const;
    std::vector<ModuleEntry> entries() const;
    // 已载入清单；未载入时报告 invalid_config。
    const ModuleManifest& manifest(const std::string& definition) const;
    // 包内语言资源（载入时已预校验）；未载入时报告 invalid_config。
    std::vector<I18nText> resources(const std::string& definition) const;
    // 内置实现目录，可直接用于装配定义实例化。
    const ModuleFactoryDirectory& factories() const { return factories_; }

private:
    struct Loaded {
        ModuleManifest manifest;
        std::vector<I18nText> resources;
    };

    ModuleFactoryDirectory factories_;
    std::map<std::string, std::string> implementations_;  // 定义标识 → 实现标识
    std::map<std::string, Loaded> loaded_;
};

}  // namespace ascend
