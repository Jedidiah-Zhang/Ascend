#include <ascend/session/project.hpp>

#include <ascend/engine.hpp>
#include <ascend/session/adapter.hpp>
#include <ascend/text.hpp>

#include <algorithm>
#include <chrono>
#include <cstdint>
#include <ctime>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <sstream>
#include <system_error>
#include <utility>

namespace ascend::session {
namespace {

namespace fs = std::filesystem;

[[noreturn]] void fail(ErrorCode code, const char* key, const char* fallback,
                       const std::string& source = {}) {
    throw EngineError(
        Diagnostic{code, {}, TextRef(TextKey{session_text_domain, key}, fallback), source, {}, {}});
}

fs::path records_directory(const std::string& directory) {
    return fs::path(directory) / project_records_directory;
}

// 组装项目内路径：引用必须是规范相对路径且落点保持在项目目录内（防御性复核）。
fs::path resolve_inside(const fs::path& dir, const std::string& relative) {
    const fs::path path(relative);
    if (relative.empty() || path.is_absolute() || relative.front() == '/' ||
        relative.find('\\') != std::string::npos || relative.find(':') != std::string::npos) {
        fail(ErrorCode::io_failure, "session.project.io",
             "The assembly reference must be a relative path inside the project", relative);
    }
    for (const auto& part : path) {
        if (part == "." || part == "..") {
            fail(ErrorCode::io_failure, "session.project.io",
                 "The assembly reference must stay inside the project", relative);
        }
    }
    return dir / path;
}

// 生成工具管理的记录文件名：UTC 时间戳加冲突序号。
fs::path generate_record_path(const fs::path& records_dir) {
    const auto now = std::chrono::system_clock::now();
    const std::time_t seconds = std::chrono::system_clock::to_time_t(now);
    std::tm utc{};
#ifdef _WIN32
    gmtime_s(&utc, &seconds);
#else
    gmtime_r(&seconds, &utc);
#endif
    std::ostringstream stamp;
    stamp << std::put_time(&utc, "%Y%m%dT%H%M%S");
    fs::path candidate = records_dir / (stamp.str() + project_record_extension);
    for (int index = 1; fs::exists(candidate); ++index) {
        candidate = records_dir / (stamp.str() + "-" + std::to_string(index) + project_record_extension);
    }
    return candidate;
}

}  // namespace

std::string read_text_file(const std::string& path) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        fail(ErrorCode::io_failure, "session.project.io", "Failed to read a project file", path);
    }
    std::ostringstream buffer;
    buffer << stream.rdbuf();
    if (stream.bad()) {
        fail(ErrorCode::io_failure, "session.project.io", "Failed to read a project file", path);
    }
    return buffer.str();
}

void write_text_file(const std::string& path, const std::string& bytes) {
    const fs::path target(path);
    fs::path temp = target;
    temp += ".tmp";
    const auto remove_temp = [&temp] {
        std::error_code ignored;
        fs::remove(temp, ignored);
    };
    {
        std::ofstream stream(temp, std::ios::binary | std::ios::trunc);
        if (!stream) {
            // 打不开时不删除临时路径：它可能不是本次创建的对象。
            fail(ErrorCode::io_failure, "session.project.io", "Failed to write a project file",
                 target.string());
        }
        stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
        stream.flush();
        if (!stream) {
            remove_temp();
            fail(ErrorCode::io_failure, "session.project.io", "Failed to write a project file",
                 target.string());
        }
        stream.close();
        // 关闭结果必须成功：析构时才暴露的写入错误不能进入替换步骤。
        if (!stream) {
            remove_temp();
            fail(ErrorCode::io_failure, "session.project.io", "Failed to finalize a project file",
                 target.string());
        }
    }
    std::error_code error;
    fs::rename(temp, target, error);
    if (error) {
        remove_temp();
        fail(ErrorCode::io_failure, "session.project.io", "Failed to replace a project file",
             target.string());
    }
}

std::string project_folder_name(const std::string& name) {
    std::string out;
    for (const char ch : name) {
        if (ch == '/' || ch == '\\' || ch == ':' || ch == '\0') continue;
        out.push_back(ch);
    }
    while (!out.empty() && (out.back() == ' ' || out.back() == '.')) out.pop_back();
    return out.empty() ? "study" : out;
}

std::string find_study_file(const std::string& directory) {
    std::error_code error;
    if (!fs::is_directory(directory, error) || error) {
        fail(ErrorCode::io_failure, "session.project.io", "The project directory does not exist",
             directory);
    }
    std::vector<std::string> found;
    for (const auto& entry : fs::directory_iterator(directory, error)) {
        if (error) break;
        std::error_code type_error;
        if (!entry.is_regular_file(type_error) || type_error) continue;
        if (entry.path().extension() == project_study_extension) found.push_back(entry.path().string());
    }
    if (error) {
        fail(ErrorCode::io_failure, "session.project.io", "Failed to read the project directory",
             directory);
    }
    std::sort(found.begin(), found.end());
    if (found.empty()) {
        fail(ErrorCode::invalid_json, "session.project.missing",
             "The project directory has no study file (*.aexp)", directory);
    }
    if (found.size() > 1) {
        fail(ErrorCode::invalid_json, "session.project.multiple",
             "The project directory has multiple study files (*.aexp)", directory);
    }
    return found.front();
}

std::string create_project(const std::string& directory, const std::string& study_name,
                           const StudyFile& study, const std::string& assembly_bytes) {
    const fs::path dir(directory);
    std::error_code error;
    bool directory_created = false;
    if (fs::exists(dir, error)) {
        if (!fs::is_directory(dir, error) || error) {
            fail(ErrorCode::io_failure, "session.project.exists",
                 "The project path exists and is not a directory", directory);
        }
        if (!fs::is_empty(dir, error) || error) {
            fail(ErrorCode::io_failure, "session.project.exists",
                 "The project directory already exists and is not empty", directory);
        }
    } else {
        fs::create_directories(dir, error);
        if (error) {
            fail(ErrorCode::io_failure, "session.project.io", "Failed to create the project directory",
                 directory);
        }
        directory_created = true;
    }
    const auto study_path = dir / (project_folder_name(study_name) + project_study_extension);
    const auto assembly_path = resolve_inside(dir, study.assembly);
    const auto records = records_directory(directory);
    bool records_created = false;
    try {
        // 先写装配文件（含所需父目录），最后写研究文件：研究文件出现即代表项目完整。
        if (!assembly_path.parent_path().empty()) {
            fs::create_directories(assembly_path.parent_path(), error);
            if (error) {
                fail(ErrorCode::io_failure, "session.project.io",
                     "Failed to create the assembly folder", assembly_path.parent_path().string());
            }
        }
        records_created = !fs::exists(records, error);
        fs::create_directories(records, error);
        if (error) {
            fail(ErrorCode::io_failure, "session.project.io", "Failed to create the records directory",
                 records.string());
        }
        write_text_file(assembly_path.string(), assembly_bytes);
        write_text_file(study_path.string(), encode_study_file(study));
    } catch (...) {
        // 失败清理：删除本次写入的文件与目录，使同一路径可以重试。
        std::error_code ignored;
        fs::remove(study_path, ignored);
        fs::remove(assembly_path, ignored);
        if (records_created) fs::remove_all(records, ignored);
        if (directory_created) fs::remove_all(dir, ignored);
        throw;
    }
    return study_path.string();
}

ProjectContents load_project(const std::string& directory) {
    ProjectContents contents;
    contents.directory = directory;
    contents.study_path = find_study_file(directory);
    contents.study = decode_study_file(read_text_file(contents.study_path));
    contents.assembly_path = resolve_inside(fs::path(directory), contents.study.assembly).string();
    std::error_code error;
    if (!fs::exists(contents.assembly_path, error) || error) {
        fail(ErrorCode::invalid_json, "session.project.assembly_missing",
             "The assembly file referenced by the study file does not exist", contents.assembly_path);
    }
    contents.assembly_bytes = read_text_file(contents.assembly_path);
    contents.records = list_records(directory);
    return contents;
}

void write_study_file(const std::string& path, const StudyFile& study) {
    write_text_file(path, encode_study_file(study));
}

std::string write_record_file(const std::string& directory, const std::string& record_path,
                              const RecordFile& record) {
    const auto records = records_directory(directory);
    std::error_code error;
    fs::create_directories(records, error);
    if (error) {
        fail(ErrorCode::io_failure, "session.project.io", "Failed to create the records directory",
             records.string());
    }
    const fs::path target = record_path.empty() ? generate_record_path(records) : fs::path(record_path);
    write_text_file(target.string(), encode_record_file(record));
    return target.string();
}

std::vector<ProjectRecordEntry> list_records(const std::string& directory) {
    std::vector<ProjectRecordEntry> entries;
    const auto records = records_directory(directory);
    std::error_code error;
    if (!fs::is_directory(records, error) || error) return entries;
    for (const auto& entry : fs::directory_iterator(records, error)) {
        if (error) break;
        std::error_code type_error;
        if (!entry.is_regular_file(type_error) || type_error) continue;
        if (entry.path().extension() != project_record_extension) continue;
        ProjectRecordEntry item;
        item.path = entry.path().string();
        item.name = entry.path().filename().string();
        try {
            item.summary = summarize_record_file(read_text_file(item.path));
            item.readable = true;
        } catch (const EngineError& error) {
            item.problem = error.diagnostic();
        }
        entries.push_back(std::move(item));
    }
    std::sort(entries.begin(), entries.end(),
              [](const ProjectRecordEntry& left, const ProjectRecordEntry& right) {
                  return left.name < right.name;
              });
    return entries;
}

}  // namespace ascend::session
