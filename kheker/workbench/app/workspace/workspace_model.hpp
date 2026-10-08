#pragma once

// 工作区状态层：集中身份清理、运行可用性与控件启用条件；
// 输入只有控制器值快照与命令意图，不依赖 Qt Widgets，可由无控件单测覆盖。
// 设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "workspace_view.hpp"

#include <QObject>

#include <string>
#include <vector>

namespace ascend::workbench {

class WorkspaceModel : public QObject {
    Q_OBJECT

public:
    explicit WorkspaceModel(QObject* parent = nullptr);

    const WorkspaceState& state() const noexcept { return state_; }
    // 由状态派生控件启用条件；调用方不再自行拼装规则。
    ControlsView controls() const;

    // 命令意图：身份切换（新建研究／打开示例／打开系统／打开项目／打开记录成功）。
    // file 与 secondary 为切换后关联的文件路径：
    // - new_research／open_example：清空装配、研究与记录文件（不动 dirty 标记）；
    // - open_system：system_file = file、system_dirty = false，研究文件与记录清空；
    // - open_project：research_file = file（研究文件）、system_file = secondary（装配文件），
    //   两者 dirty = false，当前记录清空；
    // - open_record：record_file = file，项目文件保持不变。
    void beginIdentity(IdentityIntent intent, std::string file = {}, std::string secondary = {});

    // 文件流程簿记（静默，不发出信号）：只更新身份文件字段与未保存标记，不触发渲染；
    // 渲染由外壳在保存/打开等路径显式触发（避免误发 identity 清理瞬时视图）。
    void note_system_saved(std::string path);    // system_file = path; system_dirty = false
    void note_research_saved(std::string path);  // research_file = path; research_dirty = false
    void note_record_saved(std::string path);    // record_file = path
    void set_system_dirty(bool dirty);
    void set_research_dirty(bool dirty);

    // 面板可见性：界面把用户操作静默同步进来；规则变更由 changed(panels) 通知。
    void noteCentralOpened(const std::string& id);
    void noteCentralClosed(const std::string& id);
    // 界面应用置前请求后清除。
    void clearCentralFocus();

public Q_SLOTS:
    void applyBusy(bool busy);
    void applyStatus(const session::Status& status);
    void applyModel(const ModelSnapshot& model);
    void applyTracesReset(const std::vector<session::TrackTraceView>& traces);
    // 采样增量：追加到指定序列（越界忽略）。
    void applySamplesAppended(int series, const std::vector<session::SampleView>& samples,
                              const std::vector<session::StepEvent>& events);
    void applyComparison(const session::ComparisonView& comparison);
    void applyCheck(const session::CheckReport& report);
    // 诊断日志累积；保留最近 max_diagnostic_log 条（空批次不通知）。
    void appendDiagnostics(const std::vector<session::DiagnosticView>& diagnostics);
    void applyRecord(const session::RecordView& record);
    // 已载入模块包概要列表；内容不变时不通知。
    void applyPackages(std::vector<session::ModulePackageView> packages);

Q_SIGNALS:
    void changed(WorkspaceChange changed);

private:
    WorkspaceState state_;
};

}  // namespace ascend::workbench
