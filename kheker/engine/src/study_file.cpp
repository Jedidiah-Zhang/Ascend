#include <ascend/study_file.hpp>

#include "experiment_json.hpp"
#include "json.hpp"

#include <ascend/engine.hpp>
#include <ascend/text.hpp>

#include <string>
#include <utility>
#include <vector>

namespace ascend {
namespace {

[[noreturn]] void fail(ErrorCode code, const char* key, const char* fallback) {
    throw EngineError({code, {}, TextRef(TextKey{engine_text_domain, key}, std::string(fallback))});
}

const Config& require_member(const Config& object, const char* name) {
    const auto* member = object.find(name);
    if (member == nullptr) {
        fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata is missing a required field");
    }
    return *member;
}

std::string require_string(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::string) {
        fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata field has the wrong type");
    }
    return member.string();
}

const Config& require_object(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata field has the wrong type");
    }
    return member;
}

// 装配文件引用限定为项目内相对路径：非空、逐段规范（拒绝空段、"." 与 ".."）、
// 不使用绝对路径、盘符或反斜杠。
bool valid_relative_assembly_path(const std::string& path) {
    if (path.empty() || path.front() == '/' || path.front() == '\\' || path.back() == '/') return false;
    if (path.find('\\') != std::string::npos || path.find(':') != std::string::npos) return false;
    std::size_t start = 0;
    while (start <= path.size()) {
        const auto end = path.find('/', start);
        const auto length = end == std::string::npos ? std::string::npos : end - start;
        const auto segment = path.substr(start, length);
        if (segment.empty() || segment == "." || segment == "..") return false;
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return true;
}

}  // namespace

std::string encode_study_file(const StudyFile& file) {
    if (!valid_relative_assembly_path(file.assembly)) {
        fail(ErrorCode::invalid_config, "study_file.assembly",
             "Study file assembly path must be a relative path inside the project");
    }
    std::vector<std::pair<std::string, Config>> members;
    members.emplace_back("format", Config::string("ascend.experiment"));
    members.emplace_back("version", Config::integer(1));
    if (!file.model.empty()) members.emplace_back("model", Config::string(file.model));
    members.emplace_back("assembly", Config::string(file.assembly));
    members.emplace_back("spec", detail::spec_config(file.spec));
    return detail::write_json(Config::object(std::move(members)), "study");
}

StudyFile decode_study_file(const std::string& text) {
    const auto root = detail::parse_json(text, "study file");
    if (root.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata is not an object");
    }
    if (require_string(root, "format") != "ascend.experiment") {
        fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata format is not recognized");
    }
    const auto& version = require_member(root, "version");
    if (version.kind() != Config::Kind::integer) {
        fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata field has the wrong type");
    }
    if (version.integer() != 1) {
        fail(ErrorCode::unsupported_format_version, "study_file.version",
             "Study file metadata version is not supported");
    }
    StudyFile file;
    if (const auto* model = root.find("model"); model != nullptr) {
        if (model->kind() != Config::Kind::string) {
            fail(ErrorCode::invalid_json, "study_file.meta", "Study file metadata field has the wrong type");
        }
        file.model = model->string();
    }
    file.assembly = require_string(root, "assembly");
    if (!valid_relative_assembly_path(file.assembly)) {
        fail(ErrorCode::invalid_json, "study_file.assembly",
             "Study file assembly path must be a relative path inside the project");
    }
    file.spec = detail::parse_spec(require_object(root, "spec"), "study_file.meta");
    return file;
}

}  // namespace ascend
