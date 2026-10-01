#pragma once

#include <ascend/experiment.hpp>

#include <any>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace ascend {

// 逐步事件：输入、推进或采样失败与停止；诊断保留结构化消息，渲染时再本地化。
struct ExperimentEvent {
    enum class Kind { input_failed, advance_failed, sample_failed, stopped };
    Kind kind = Kind::advance_failed;
    std::int64_t frame = 0;
    Diagnostic diagnostic;
};

// 一条序列：探索或分支轨迹，附逐步事件。
struct ExperimentTrace {
    RunTrace trace;
    bool exploration = false;
    std::vector<ExperimentEvent> events;
    std::int64_t current_frame = 0;  // 实际到达的逻辑帧（采样失败后可大于末条采样）
};

// 一次实验的文件内容：打开后重建与续跑所需的会话状态（ENV-14～16）。
struct ExperimentFile {
    std::string model;
    AssemblyDefinition assembly;
    ExperimentSpec spec;
    std::map<std::string, std::string> implementations;
    std::optional<std::uint64_t> run_id;
    std::uint64_t run_revision = 0;
    std::optional<std::pair<std::uint64_t, AssemblyDefinition>> draft;
    std::optional<Checkpoint> checkpoint;
    std::map<std::string, std::any> input_settings;
    std::vector<DrivenInput> inputs;      // 共同输入记录（驱动前逻辑帧、名称、参数）
    std::vector<ExperimentTrace> traces;  // 探索在前，分支随后
};

// 编码为实验文件字节；解码失败抛出 EngineError（unsupported_format_version、
// invalid_json、io_failure、type_mismatch）。首版输入参数与观测值只支持 64 位
// 整数，其他原生类型在编码前拒绝。
std::string encode_experiment_file(const ExperimentFile& file);
ExperimentFile decode_experiment_file(const std::string& bytes);

}  // namespace ascend
