#pragma once

// 工作区状态层与面板协议共用的值类型：变更标志、身份意图、控件条件与状态快照。
// 只依赖会话视图类型与标准库，界面（含无控件单测）共用。
// 设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "model_snapshot.hpp"

#include <ascend/session/session.hpp>

#include <cstdint>
#include <string>
#include <vector>

namespace ascend::workbench {

// 工作区信息域：状态层按位通告变化，面板只订阅自己需要的域。
enum class WorkspaceChange : std::uint32_t {
    none = 0,
    identity = 1u << 0,     // 身份切换：清理与上一身份绑定的瞬时视图
    catalog = 1u << 1,      // 模块目录与陈旧标记
    assembly = 1u << 2,     // 装配结构（实例、需求与连接）
    spec = 1u << 3,         // 实验规格（推进入口、输入映射与观测）
    run = 1u << 4,          // 运行相位、检查点、分支与修订
    results = 1u << 5,      // 轨迹序列与采样
    comparison = 1u << 6,   // 分支差异
    check = 1u << 7,        // 检查报告
    diagnostics = 1u << 8,  // 诊断日志
    panels = 1u << 10,      // 中央页签与停靠面板可见性
    controls = 1u << 11,    // 控件启用条件
    record = 1u << 12,      // 实际输入记录
    packages = 1u << 13,    // 已载入模块包概要
    samples = 1u << 14,     // 轨迹采样增量（追加，不重置视图）
    observations = 1u << 15,  // 规格观测集合变化
};

constexpr std::uint32_t change_bits(WorkspaceChange value) noexcept {
    return static_cast<std::uint32_t>(value);
}
constexpr WorkspaceChange operator|(WorkspaceChange left, WorkspaceChange right) noexcept {
    return static_cast<WorkspaceChange>(change_bits(left) | change_bits(right));
}
constexpr bool has(WorkspaceChange set, WorkspaceChange part) noexcept {
    return (change_bits(set) & change_bits(part)) != 0;
}

// 身份切换意图：界面在进入相应流程时声明，状态层据此发出清理通知。
enum class IdentityIntent {
    new_research,     // 新建研究（新建因果系统）
    open_example,     // 打开内置示例
    open_system,      // 打开因果系统文件成功
    open_experiment,  // 打开研究（实验文件）成功
};

// 控件启用条件：由相位、忙碌与运行状态派生；窗口只套用，不再各处理解规则。
struct ControlsView {
    bool editable = false;             // 检查／应用与配置编辑
    bool runnable = false;             // 单步／运行
    bool can_checkpoint = false;       // 创建检查点
    bool can_branch = false;           // 建立分支
    bool can_reset_branches = false;   // 重置分支
    bool can_replay = false;           // 重放核对
    bool can_save_experiment = false;  // 保存实验
    bool can_open_file = false;        // 打开实验／打开示例／新建研究（忙碌时禁用）
    bool can_manage_packages = false;  // 载入模块包（忙碌时禁用）
    bool can_unload_package = false;   // 卸载模块包（额外要求已载入非空）
};

// 中央页标识：状态层与面板注册表共用（停靠面板由 Qt 自行管理可见性）。
namespace panel_id {
inline constexpr const char* start = "start";
inline constexpr const char* editor = "editor";
inline constexpr const char* package_library = "package_library";
inline constexpr const char* timeline = "timeline";
inline constexpr const char* differences = "differences";
}  // namespace panel_id

// 中央页可见性：打开集合（按打开顺序）与一次性置前请求（界面应用后清除）。
struct PanelState {
    std::vector<std::string> central_open;
    std::string central_focus;
};

inline bool panel_open(const PanelState& panels, const char* id) {
    for (const auto& open : panels.central_open) {
        if (open == id) return true;
    }
    return false;
}

// 状态层持有的工作区状态：控制器快照、轨迹序列与差异、面板可见性与已载入模块包列表。
struct WorkspaceState {
    // 身份文件关联与未保存标记：由外壳的文件流程更新（beginIdentity／note_*_saved／set_*_dirty），
    // 渲染由外壳在保存/打开等路径显式触发。
    std::string system_file;      // 当前因果系统文件（`.aasm`）；空表示尚未保存
    std::string research_file;    // 当前研究（实验）文件（`.aexp`）；空表示尚未保存
    bool system_dirty = false;    // 因果系统草稿相对系统文件的未保存状态
    bool research_dirty = false;  // 研究草稿相对研究文件的未保存状态
    bool busy = false;
    session::Status status;
    ModelSnapshot model;
    std::vector<session::TrackTraceView> series;  // 轨迹序列：重置整体替换、采样增量追加
    bool results_available = false;               // 当前有运行或记录轨迹
    session::ComparisonView comparison;           // 最近一次请求的分支差异
    bool has_check = false;                       // 最近检查报告是否有效（身份切换清空）
    session::CheckReport check;                   // 最近一次检查报告
    std::vector<session::DiagnosticView> diagnostics;  // 诊断日志（累积，模型侧保留上限）
    session::RecordView record;                   // 最近一次请求的记录视图
    std::vector<session::ModulePackageView> packages;  // 已载入模块包概要（ENV-18）
    PanelState panels;
};

}  // namespace ascend::workbench
