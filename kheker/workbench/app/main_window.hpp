#pragma once

#include "panels/panel_context.hpp"
#include "session_controller.hpp"
#include "ui_text.hpp"
#include "workspace/panel_registry.hpp"
#include "workspace/workspace_view.hpp"

#include <ascend/session/adapter.hpp>

#include <QMainWindow>
#include <QString>
#include <QStringList>

#include <cstdint>
#include <functional>
#include <memory>
#include <utility>
#include <vector>

class QAction;
class QDockWidget;
class QLabel;
class QPushButton;
class QSpinBox;
class QTabWidget;
class QVBoxLayout;
class QWidget;

namespace ascend::workbench {

class ConfigPanel;
class DiagnosticsPanel;
class DiffPanel;
class ModulesPanel;
class PackageLibraryPanel;
class StartPagePanel;
class StatePanel;
class SystemEditorPanel;
class TimelinePanel;
class WorkspaceModel;

// 最小研究工作台主窗口：目录浏览、配置与输入编辑、运行控制、分支比较与诊断呈现。
// 窗口只提交命令并呈现控制层发布的值快照，不直接访问会话与运行对象。
class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    MainWindow(SessionController* controller, std::shared_ptr<const session::AdapterRegistry> adapters,
               std::shared_ptr<const UiTexts> texts, QWidget* parent = nullptr);
    void setCloseHandler(std::function<void()> handler) { close_handler_ = std::move(handler); }

    // 测试访问：当前会话状态、状态行文本与命令执行标记。
    const session::Status& currentStatus() const noexcept { return state().status; }
    QString statusLine() const;
    bool busy() const noexcept { return state().busy; }
    QString currentFile() const { return QString::fromStdString(state().research_file); }
    // 新窗口显示后关闭模块库页签（其默认态为不打开；载入模块包时自动打开并切到该页签）。
    void hidePackageLibrary();
    // 打开系统编辑器页签（编辑菜单与查看菜单共用）。
    void showSystemEditor();
    // 打开开始页签（默认打开；启动时置前）。
    void showStartPage();
    // 关闭开始页签（进入新建/打开流程时调用）。
    void hideStartPage();
protected:
    void closeEvent(QCloseEvent* event) override;

private Q_SLOTS:
    void onBusyChanged(bool busy);
    void onStatusChanged(const session::Status& status);
    void onModelChanged(const ascend::workbench::ModelSnapshot& model);
    void onTracesReset(const std::vector<ascend::session::TrackTraceView>& traces);
    void onSamplesAppended(int series, const std::vector<ascend::session::SampleView>& samples,
                           const std::vector<ascend::session::StepEvent>& events);
    void onComparisonReady(const ascend::session::ComparisonView& comparison);
    void onCheckFinished(const ascend::session::CheckReport& report);
    void onReplayFinished(const ascend::session::ReplayReport& report);
    void onSampleDetailReady(int series, std::int64_t frame, const ascend::session::SampleDetailView& detail);
    void onDiagnostics(const std::vector<ascend::session::DiagnosticView>& diagnostics);
    void onRecordChanged(const ascend::session::RecordView& record);
    // 状态层变更：按信息域驱动各视图渲染（唯一渲染入口）。
    void onWorkspaceChanged(WorkspaceChange changed);

private:
    // 状态层只读状态：成员渲染统一从这里取值。
    const WorkspaceState& state() const noexcept;

    void buildLayout();
    void buildTopBar(QVBoxLayout* root);
    void buildResultsPane();
    void buildDiagnosticsDock();
    void buildMenuBar();
    // 状态层 → 页签与动作：应用 central_open/central_focus（唯一套用点）。
    void applyPanelState();
    void showPanel(const char* id);
    void hidePanel(const char* id);
    // 系统编辑器命令：参数由 SystemEditorPanel 从控件读出，此处只做忙碌簿记与队列提交。
    void chooseNewSystem();
    void addSystemModule(const QString& definition, const QString& instance);
    void removeSystemModule(const QString& instance);
    void connectSystemRequirement(const QString& module, const QString& symbol, const QString& provider_module,
                                  const QString& provider_symbol);
    void disconnectSystemRequirement(const QString& module, const QString& symbol);
    void onSpecAdvanceChanged(const QString& module, const QString& symbol);
    void onSpecObservationsChanged(const QStringList& references);
    // 带参数编辑命令的公共簿记：置忙碌标记并排队调用控制层；编辑命令标记研究文件与系统草稿未保存。
    void beginSystemCommand(const char* method, bool edits_draft = true);
    // 实例配置编辑：由 ConfigPanel 通过上下文提交，此处做忙碌守卫与队列提交（保持原表单行为）。
    void setInstanceConfig(const QString& scope, const QString& name, const ascend::Config& config);
    // 研究入口与因果系统文件：新建研究对话框、打开/保存系统与结果处理。
    void chooseNewResearch();
    void chooseOpenSystem();
    void saveSystem();
    void chooseSaveSystemAs();
    void onSystemOpened(const QString& path, bool ok, const QString& detail);
    void onSystemSaved(const QString& path, bool ok, const QString& detail);
    // 身份切换清理：由状态层的 identity 通知触发，清掉与上一身份绑定的瞬时视图。
    void clearIdentityViews();
    // 开始页动作：关闭开始页并进入相应流程。
    void startNewResearch();
    void startOpenProject();
    void startOpenExample();

    void updateControls();
    void updateStatusLabels();
    void requestSampleDetail(std::int64_t frame);
    void requestLatestSample();
    // 研究项目与运行记录（ENV-16）：打开/保存项目、打开记录与结果处理。
    void chooseOpenProject();
    void saveProject();
    void chooseOpenRecord();
    void onProjectOpened(const QString& directory, const QString& study, const QString& assembly, bool ok,
                         const QString& detail);
    void onProjectSaved(const QString& study, const QString& assembly, const QString& record, bool ok,
                        const QString& detail);
    void onRecordOpened(const QString& path, bool ok, const QString& detail);
    void onRecordsChanged(const std::vector<ascend::session::RecordEntryView>& records);
    // 模块包：多选文件或整个文件夹的载入/卸载命令与结果、已载入概要（ENV-18）。
    void chooseLoadModulePackage();
    void chooseLoadModulePackageFolder();
    void loadModulePackages(const QStringList& paths);
    void chooseUnloadModulePackage();
    void unloadModulePackage(const QString& definition);
    void onModulePackages(const std::vector<ascend::session::ModulePackageView>& packages);
    void onModulePackagesLoaded(int loaded, int failed, const QString& detail);
    void onModulePackageUnloaded(const QString& definition, bool ok, const QString& detail);
    // 载入成功后显示模块库面板（未显示时以主窗口中央浮窗打开；已显示时保持位置并置前）。
    void showPackageLibrary();
    void updateWindowTitle();
    void updateRecordSummary();
    void dispatch(const char* method);
    void dispatchReadonly(const char* method);

    SessionController* controller_ = nullptr;
    std::shared_ptr<const session::AdapterRegistry> adapters_;
    std::shared_ptr<const UiTexts> texts_;
    const session::ValueAdapter* int_adapter_ = nullptr;

    WorkspaceModel* workspace_ = nullptr;
    // 面板上下文：面板只读状态与文案，命令与对话框回调在此初始化。
    PanelContext context_;
    StartPagePanel* start_panel_ = nullptr;
    PackageLibraryPanel* package_panel_ = nullptr;
    SystemEditorPanel* system_panel_ = nullptr;
    TimelinePanel* timeline_panel_ = nullptr;
    DiffPanel* diff_panel_ = nullptr;
    PanelRegistry panels_;
    bool applying_panels_ = false;
    bool closing_ = false;
    QString pending_;
    std::int64_t selected_frame_ = -1;
    int selected_series_ = 0;

    // 顶部
    QPushButton* check_button_ = nullptr;
    QPushButton* apply_button_ = nullptr;
    QPushButton* step_button_ = nullptr;
    QPushButton* run_button_ = nullptr;
    QSpinBox* steps_spin_ = nullptr;
    QPushButton* stop_button_ = nullptr;
    QPushButton* checkpoint_button_ = nullptr;
    QPushButton* branch_button_ = nullptr;
    QPushButton* reset_branches_button_ = nullptr;
    QPushButton* replay_button_ = nullptr;

    // 左侧停靠面板：模块管理器（四树与选中项详情由 ModulesPanel 自持）
    ModulesPanel* modules_panel_ = nullptr;

    // 模块库（中央工作区页签，默认不打开，载入模块包时自动打开；由 PackageLibraryPanel 持有控件）
    QAction* load_module_action_ = nullptr;
    QAction* load_module_folder_action_ = nullptr;
    QAction* unload_module_action_ = nullptr;
    QAction* new_research_action_ = nullptr;

    // 系统编辑器（中央工作区页签；控件与系统文件渲染由 SystemEditorPanel 自持，文件字段在状态层）

    // 配置停靠面板：实例配置表单由 ConfigPanel 自持；中部结果页签留在外壳
    ConfigPanel* config_panel_ = nullptr;
    QTabWidget* results_tabs_ = nullptr;

    // 右侧状态停靠面板：详情、状态字段与采样详情由 StatePanel 自持
    StatePanel* state_panel_ = nullptr;

    // 底部面板：诊断与原因链、检查、实际输入记录（独立面板只读状态渲染）
    DiagnosticsPanel* bottom_panel_ = nullptr;

    QLabel* status_label_ = nullptr;
    QLabel* revision_label_ = nullptr;

    // 可停靠/浮动的面板：模块管理器、配置与状态为停靠窗口，结果为主工作区。
    QDockWidget* modules_dock_ = nullptr;
    QDockWidget* config_dock_ = nullptr;
    QDockWidget* state_dock_ = nullptr;

    // 菜单栏：已实装命令与按钮同源，占位项禁用。
    QAction* open_action_ = nullptr;
    QAction* open_project_action_ = nullptr;
    QAction* open_record_action_ = nullptr;
    QAction* save_action_ = nullptr;
    std::vector<std::pair<QAction*, QPushButton*>> action_buttons_;
    QDockWidget* diagnostics_dock_ = nullptr;

    // 运行记录清单（打开记录对话框的数据）与待显示标记；新建研究待成功后再切换身份。
    std::vector<session::RecordEntryView> records_;
    bool records_dialog_pending_ = false;
    bool new_project_pending_ = false;

    std::function<void()> close_handler_;
};

}  // namespace ascend::workbench
