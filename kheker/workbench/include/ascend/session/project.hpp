#pragma once

// 研究项目目录约定与文件读写（ENV-16）：一个目录对应一个研究，根下唯一
// `*.aexp` 研究文件为入口；`records/` 存放工具命名的 `*.arec` 运行记录；
// 装配文件与模块包位于目录内。本层不依赖界面，工作台与无头入口共用。

#include <ascend/record_file.hpp>
#include <ascend/study_file.hpp>

#include <optional>
#include <string>
#include <vector>

namespace ascend::session {

inline constexpr const char* project_study_extension = ".aexp";
inline constexpr const char* project_record_extension = ".arec";
inline constexpr const char* project_records_directory = "records";
inline constexpr const char* project_default_assembly = "world.aasm";

// 一条运行记录的列表项：摘要来自记录 meta 段，不解码轨迹。
struct ProjectRecordEntry {
    std::string path;   // 绝对路径
    std::string name;   // 文件名
    bool readable = false;
    RecordSummary summary;
    std::optional<Diagnostic> problem;  // 不可读时的结构化原因
};

// 项目内容：研究文件、装配文件字节与记录清单。
struct ProjectContents {
    std::string directory;
    std::string study_path;
    std::string assembly_path;
    StudyFile study;
    std::string assembly_bytes;
    std::vector<ProjectRecordEntry> records;
};

// 在目录根查找唯一 `*.aexp`；缺失或存在多个时抛出 EngineError。
std::string find_study_file(const std::string& directory);

// 由研究名称派生项目目录名与研究文件名：去掉路径分隔符、冒号与控制字符，
// 去除尾部空白与点；为空时回退为 "study"。
std::string project_folder_name(const std::string& name);

// 创建项目：研究文件（引用装配文件）、装配文件与 records/ 目录。
// 目录必须不存在或为空；写入采用临时文件加原子替换；返回研究文件路径。
std::string create_project(const std::string& directory, const std::string& study_name,
                           const StudyFile& study, const std::string& assembly_bytes);

// 读取项目：研究文件、装配文件与记录清单；缺失或格式非法时抛出 EngineError。
ProjectContents load_project(const std::string& directory);

// 原子写入研究文件。
void write_study_file(const std::string& path, const StudyFile& study);

// 写入运行记录：record_path 为空时在 records/ 生成新文件名；返回写入路径。
std::string write_record_file(const std::string& directory, const std::string& record_path,
                              const RecordFile& record);

// 列出记录：按文件名排序；摘要读取失败时保留条目与原因。
std::vector<ProjectRecordEntry> list_records(const std::string& directory);

// 读取整个文件；失败抛出 EngineError（io_failure）。
std::string read_text_file(const std::string& path);

// 原子写入文本文件（同目录临时文件加替换）。
void write_text_file(const std::string& path, const std::string& bytes);

}  // namespace ascend::session
