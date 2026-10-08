#pragma once

#include "json.hpp"

#include <ascend/experiment.hpp>

namespace ascend::detail {

// 规格与引用的 JSON 编码助手：研究文件（ENV-16）与运行记录共用。
// error_key 指定解析失败时诊断使用的文本域键（如 "study_file.meta"）。
Config reference_config(const Reference& reference);
Config spec_config(const ExperimentSpec& spec);
Reference parse_reference(const Config& value, const char* error_key);
ExperimentSpec parse_spec(const Config& value, const char* error_key);

}  // namespace ascend::detail
