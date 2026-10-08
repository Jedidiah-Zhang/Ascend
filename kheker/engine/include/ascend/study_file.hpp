#pragma once

#include <ascend/experiment.hpp>

#include <string>

namespace ascend {

// 研究文件（项目入口，ENV-16）：记录世界引用、规格与干预设计。
// 研究以项目目录落盘，根下唯一 `*.aexp` 文本文件为入口；装配编辑以项目内
// 装配文件为源，运行记录独立保存（见 record_file.hpp）。
// 干预设计（interventions）形式待定，当前版本不落字段。
struct StudyFile {
    std::string model;     // 模型显示名，可选
    std::string assembly;  // 项目内装配文件相对路径（非空、不以 '/' 开头、不含 ".." 段）
    ExperimentSpec spec;
};

// 编码为研究文件文本 JSON；解码失败抛出 EngineError（invalid_json、
// unsupported_format_version、invalid_config）。文件保持纯文本，便于检查与版本管理。
std::string encode_study_file(const StudyFile& file);
StudyFile decode_study_file(const std::string& text);

}  // namespace ascend
