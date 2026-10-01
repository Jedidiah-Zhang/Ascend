#pragma once

#include <cstdint>
#include <string>
#include <vector>

namespace ascend::detail {

// ENV-14 容器：魔数、容器版本、文件种类、段表。实验文件与模块包共用。
inline constexpr std::uint8_t container_kind_experiment = 1;
inline constexpr std::uint8_t container_kind_module = 2;
inline constexpr std::uint8_t container_section_required = 0x01;

struct ContainerSection {
    std::uint64_t id = 0;
    std::uint8_t flags = container_section_required;
    std::string data;
};

// 写入容器；段按给定顺序存放。
std::string write_container(std::uint8_t kind, const std::vector<ContainerSection>& sections);

// 读取并校验容器；key_prefix 用于诊断文本键（<prefix>.magic、.version、.kind、
// .section、.corrupt）。失败抛出 EngineError（invalid_json、unsupported_format_version）。
std::vector<ContainerSection> read_container(const std::string& bytes, std::uint8_t expected_kind,
                                             const char* key_prefix);

}  // namespace ascend::detail
