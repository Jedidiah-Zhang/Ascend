#include "workspace_model.hpp"

#include <algorithm>
#include <tuple>
#include <utility>

namespace ascend::workbench {

namespace {

// 诊断日志上限：长会话只保留最近条目。
constexpr std::size_t kMaxDiagnosticLog = 200;

// 关闭中央页：从打开集合移除并清理指向它的置前请求。
void close_panel(PanelState& panels, const char* id) {
    auto& open = panels.central_open;
    open.erase(std::remove(open.begin(), open.end(), std::string(id)), open.end());
    if (panels.central_focus == id) panels.central_focus.clear();
}

// 模块包概要列表的内容比较：相同列表不重复通知（概要字段全部参与）。
bool same_packages(const std::vector<session::ModulePackageView>& left,
                   const std::vector<session::ModulePackageView>& right) {
    if (left.size() != right.size()) return false;
    for (std::size_t index = 0; index < left.size(); ++index) {
        const auto& a = left[index];
        const auto& b = right[index];
        if (std::tie(a.definition, a.version, a.implementation, a.stateless, a.state_contract,
                     a.declarations, a.requirements, a.resources) !=
            std::tie(b.definition, b.version, b.implementation, b.stateless, b.state_contract,
                     b.declarations, b.requirements, b.resources)) {
            return false;
        }
    }
    return true;
}

}  // namespace

WorkspaceModel::WorkspaceModel(QObject* parent) : QObject(parent) {}

ControlsView WorkspaceModel::controls() const {
    const session::Status& status = state_.status;
    const bool compare = status.branches == session::max_branches;
    ControlsView controls;
    controls.editable = !state_.busy && status.phase != session::Phase::empty;
    controls.runnable = !state_.busy &&
                        (status.phase == session::Phase::runnable || status.phase == session::Phase::stopped);
    controls.can_checkpoint = controls.runnable && state_.series.size() == 1 && !compare;
    controls.can_branch = controls.runnable && status.has_checkpoint && state_.series.size() == 1 && !compare;
    controls.can_reset_branches = !state_.busy && compare;
    controls.can_replay = !state_.busy && !state_.series.empty();
    controls.can_save_project = !state_.busy && status.phase != session::Phase::empty &&
                                !status.project_directory.empty();
    controls.can_open_file = !state_.busy;
    controls.can_manage_packages = !state_.busy;
    controls.can_unload_package = !state_.busy && !state_.packages.empty();
    return controls;
}

void WorkspaceModel::beginIdentity(IdentityIntent intent, std::string file, std::string secondary) {
    // 身份文件字段切换：打开项目关联研究文件与装配文件；打开记录关联当前记录。
    // 新建与打开示例只解除文件关联，dirty 标记保持既有语义（随后由命令簿记更新）。
    switch (intent) {
        case IdentityIntent::new_research:
        case IdentityIntent::open_example:
            state_.system_file.clear();
            state_.research_file.clear();
            state_.record_file.clear();
            break;
        case IdentityIntent::open_system:
            state_.system_file = std::move(file);
            state_.system_dirty = false;
            state_.research_file.clear();
            state_.record_file.clear();
            break;
        case IdentityIntent::open_project:
            state_.research_file = std::move(file);
            state_.system_file = std::move(secondary);
            state_.research_dirty = false;
            state_.system_dirty = false;
            state_.record_file.clear();
            break;
        case IdentityIntent::open_record:
            state_.record_file = std::move(file);
            break;
    }
    // 检查报告属于上一身份：清空并通知（诊断日志按设计跨身份保留）。
    state_.check = session::CheckReport{};
    state_.has_check = false;
    Q_EMIT changed(WorkspaceChange::identity | WorkspaceChange::check);
}

void WorkspaceModel::note_system_saved(std::string path) {
    state_.system_file = std::move(path);
    state_.system_dirty = false;
}

void WorkspaceModel::note_research_saved(std::string path) {
    state_.research_file = std::move(path);
    state_.research_dirty = false;
}

void WorkspaceModel::note_record_saved(std::string path) {
    state_.record_file = std::move(path);
}

void WorkspaceModel::set_system_dirty(bool dirty) {
    state_.system_dirty = dirty;
}

void WorkspaceModel::set_research_dirty(bool dirty) {
    state_.research_dirty = dirty;
}

void WorkspaceModel::applyBusy(bool busy) {
    if (state_.busy == busy) return;
    state_.busy = busy;
    Q_EMIT changed(WorkspaceChange::controls);
}

void WorkspaceModel::applyStatus(const session::Status& status) {
    state_.status = status;
    Q_EMIT changed(WorkspaceChange::run | WorkspaceChange::controls);
}

void WorkspaceModel::applyModel(const ModelSnapshot& model) {
    const bool observations_changed = model.observations != state_.model.observations;
    state_.model = model;
    // 模型快照不发布 record：记录视图只由 applyRecord（requestRecord 的结果）驱动，
    // 避免只读记录请求触发目录级重建、并避免每条带模型的命令重复渲染。
    WorkspaceChange change = WorkspaceChange::catalog | WorkspaceChange::assembly | WorkspaceChange::spec;
    if (observations_changed) change = change | WorkspaceChange::observations;
    Q_EMIT changed(change);
}

void WorkspaceModel::applySamplesAppended(int series, const std::vector<session::SampleView>& samples,
                                          const std::vector<session::StepEvent>& events) {
    if (series < 0 || static_cast<std::size_t>(series) >= state_.series.size()) return;
    auto& trace = state_.series[static_cast<std::size_t>(series)];
    trace.samples.insert(trace.samples.end(), samples.begin(), samples.end());
    trace.events.insert(trace.events.end(), events.begin(), events.end());
    Q_EMIT changed(WorkspaceChange::samples);
}

void WorkspaceModel::applyComparison(const session::ComparisonView& comparison) {
    state_.comparison = comparison;
    Q_EMIT changed(WorkspaceChange::comparison);
}

void WorkspaceModel::applyTracesReset(const std::vector<session::TrackTraceView>& traces) {
    state_.series = traces;
    state_.results_available = !state_.series.empty();
    bool panels_changed = false;
    if (state_.results_available) {
        // 出现运行/记录：打开时间轴与逻辑帧差异；首次出现时切到时间轴，
        // 已打开过则不打断当前页（例如系统编辑器里的迭代）。
        const bool opening = !panel_open(state_.panels, panel_id::timeline);
        if (opening) {
            state_.panels.central_open.push_back(panel_id::timeline);
            panels_changed = true;
        }
        if (!panel_open(state_.panels, panel_id::differences)) {
            state_.panels.central_open.push_back(panel_id::differences);
            panels_changed = true;
        }
        if (opening) state_.panels.central_focus = panel_id::timeline;
    } else if (state_.status.phase == session::Phase::empty || state_.status.phase == session::Phase::editing) {
        // 空会话或编辑态不保留结果页签。
        const bool had_timeline = panel_open(state_.panels, panel_id::timeline);
        const bool had_differences = panel_open(state_.panels, panel_id::differences);
        close_panel(state_.panels, panel_id::timeline);
        close_panel(state_.panels, panel_id::differences);
        panels_changed = had_timeline || had_differences;
    }
    Q_EMIT changed(WorkspaceChange::results | WorkspaceChange::controls |
                   (panels_changed ? WorkspaceChange::panels : WorkspaceChange::none));
}

void WorkspaceModel::noteCentralOpened(const std::string& id) {
    if (!panel_open(state_.panels, id.c_str())) state_.panels.central_open.push_back(id);
    state_.panels.central_focus = id;  // 用户操作：置前
}

void WorkspaceModel::noteCentralClosed(const std::string& id) {
    close_panel(state_.panels, id.c_str());
}

void WorkspaceModel::clearCentralFocus() {
    state_.panels.central_focus.clear();
}

void WorkspaceModel::applyCheck(const session::CheckReport& report) {
    state_.check = report;
    state_.has_check = true;
    Q_EMIT changed(WorkspaceChange::check);
}

void WorkspaceModel::appendDiagnostics(const std::vector<session::DiagnosticView>& diagnostics) {
    if (diagnostics.empty()) return;
    state_.diagnostics.insert(state_.diagnostics.end(), diagnostics.begin(), diagnostics.end());
    if (state_.diagnostics.size() > kMaxDiagnosticLog) {
        state_.diagnostics.erase(state_.diagnostics.begin(),
                                 state_.diagnostics.begin() + static_cast<std::ptrdiff_t>(
                                     state_.diagnostics.size() - kMaxDiagnosticLog));
    }
    Q_EMIT changed(WorkspaceChange::diagnostics);
}

void WorkspaceModel::applyRecord(const session::RecordView& record) {
    state_.record = record;
    Q_EMIT changed(WorkspaceChange::record);
}

void WorkspaceModel::applyPackages(std::vector<session::ModulePackageView> packages) {
    if (same_packages(state_.packages, packages)) return;
    state_.packages = std::move(packages);
    Q_EMIT changed(WorkspaceChange::packages | WorkspaceChange::controls);
}

}  // namespace ascend::workbench
