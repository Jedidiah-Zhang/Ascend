#pragma once

#include <ascend/assembly.hpp>

#include <string>

namespace ascend::detail {

// 解析 JSON 文本为配置值；语法错误携带来源与行列，语义错误携带字段路径。
Config parse_json(const std::string& text, const std::string& source);

std::string json_pointer_join(const std::string& parent, const std::string& segment);

// 在复制配置子树或打开输出文件之前验证 UTF-8 和容器深度。
void validate_json(const Config& value, const std::string& source, const std::string& path = {},
                   std::size_t depth = 0);

// 稳定排版输出；对象成员保持既有顺序。
std::string write_json(const Config& value, const std::string& source = {});

}  // namespace ascend::detail
