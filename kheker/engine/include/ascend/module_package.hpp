#pragma once

#include <ascend/engine.hpp>

#include <cstdint>
#include <string>
#include <vector>

namespace ascend {

// 模块清单中的参数：名称与稳定类型名。
struct ManifestParameter {
    std::string name;
    std::string type;
};

// 一条公开声明：相对模块路径、符号、种类、结果与参数类型名、说明、声明读写与契约。
struct ManifestDeclaration {
    std::string module;  // 相对模块路径；空表示实例自身
    std::string symbol;
    SymbolKind kind = SymbolKind::value;
    std::string result_type;
    std::vector<ManifestParameter> parameters;
    TextRef description;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
    std::string contract;
};

// 一项需求：相对模块路径、符号、种类、结果与参数类型名、契约与说明。
struct ManifestRequirement {
    std::string module;
    std::string symbol;
    SymbolKind kind = SymbolKind::value;
    std::string result_type;
    std::vector<std::string> parameters;
    std::string contract;
    TextRef description;
};

// 模块清单：声明与需求从已封闭实例导出；定义、版本与实现标识由宿主填写。
struct ModuleManifest {
    std::string definition;
    std::string version;
    std::string implementation;
    bool stateless = false;
    std::string state_contract;
    std::vector<ManifestDeclaration> declarations;
    std::vector<ManifestRequirement> requirements;
};

// 一条语言资源：域、语言与语言文件 JSON 文本。
struct ModuleResource {
    std::string domain;
    std::string locale;
    std::string text;
};

// 模块包：清单与语言资源；首版实现载体为宿主内置注册，包不含实现（ENV-17）。
struct ModulePackage {
    ModuleManifest manifest;
    std::vector<ModuleResource> resources;
};

// 从已封闭引擎的实例导出清单（公开声明、需求与状态能力）；类型不可命名或引用越出
// 模块时报 EngineError（type_mismatch、invalid_config、state_incomplete）。
ModuleManifest export_module_manifest(const Engine& engine, const std::string& scope,
                                      const std::string& instance);

// 核对清单与宿主已链接实例的运行时声明；不一致抛出 EngineError（state_mismatch）。
void check_module_manifest(const ModuleManifest& manifest, const Engine& engine,
                           const std::string& scope, const std::string& instance);

// 模块包编解码（ENV-14 容器，文件种类 2）；失败抛出 EngineError。
std::string encode_module_package(const ModulePackage& package);
ModulePackage decode_module_package(const std::string& bytes);

}  // namespace ascend
