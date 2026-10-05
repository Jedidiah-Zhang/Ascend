#pragma once

#include "session_controller.hpp"
#include "ui_text.hpp"

#include <ascend/session/adapter.hpp>

#include <QMainWindow>
#include <QString>
#include <QStringList>
#include <QVector>

#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <utility>
#include <vector>

class QAction;
class QCheckBox;
class QComboBox;
class QDockWidget;
class QGroupBox;
class QLabel;
class QLineEdit;
class QListWidget;
class QMenu;
class QPushButton;
class QScrollArea;
class QSpinBox;
class QSplitter;
class QTabWidget;
class QTableWidget;
class QTextBrowser;
class QTreeWidget;
class QTreeWidgetItem;
class QVBoxLayout;
class QWidget;

namespace ascend::workbench {

class ConfigEditor;
class WaveformWidget;

// 最小研究工作台主窗口：目录浏览、配置与输入编辑、运行控制、分支比较与诊断呈现。
// 窗口只提交命令并呈现控制层发布的值快照，不直接访问会话与运行对象。
class MainWindow : public QMainWindow {
    Q_OBJECT

public:
    MainWindow(SessionController* controller, std::shared_ptr<const session::AdapterRegistry> adapters,
               std::shared_ptr<const UiTexts> texts, QWidget* parent = nullptr);
    void setCloseHandler(std::function<void()> handler) { close_handler_ = std::move(handler); }

    // 测试访问：当前会话状态、状态行文本与命令执行标记。
    const session::Status& currentStatus() const noexcept { return status_; }
    QString statusLine() const;
    bool busy() const noexcept { return busy_; }
    QString currentFile() const { return current_file_; }
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

private:
    struct SeriesData {
        QString label;
        std::int64_t origin = 0;
        QVector<session::SampleView> samples;
        QVector<session::InterventionView> interventions;
        QVector<session::StepEvent> events;
    };

    void buildLayout();
    void buildTopBar(QVBoxLayout* root);
    void buildLeftPane();
    void buildConfigPane();
    void buildResultsPane();
    void buildRightPane();
    void buildDiagnosticsDock();
    void buildPackagePage();
    void buildSystemEditor();
    void buildStartPage();
    void buildMenuBar();
    // 中央工作区页签开关：打开时加入并在必要时切到该页，关闭时移除（视图对象保留）。
    void setCentralViewVisible(QWidget* page, const QString& title, bool visible);
    // 系统编辑器：按模型快照重建控件；选择变化时刷新提供方候选与按钮状态。
    void rebuildSystemEditor();
    void onSystemSelectionChanged();
    void updateSystemEditorControls();
    void chooseNewSystem();
    void onSystemDefinitionChanged();
    void addSystemModule();
    void removeSystemModule();
    void connectSystemRequirement();
    void disconnectSystemRequirement();
    void onSpecAdvanceChanged();
    void onSpecObservationsChanged();
    // 带参数编辑命令的公共簿记：置忙碌标记并排队调用控制层；编辑命令标记研究文件与系统草稿未保存。
    void beginSystemCommand(const char* method, bool edits_draft = true);
    // 研究入口与因果系统文件：新建研究对话框、打开/保存系统与结果处理。
    void chooseNewResearch();
    void chooseOpenSystem();
    void saveSystem();
    void chooseSaveSystemAs();
    void onSystemOpened(const QString& path, bool ok, const QString& detail);
    void onSystemSaved(const QString& path, bool ok, const QString& detail);
    void updateSystemFileLabel();
    // 开始页动作：关闭开始页并进入相应流程。
    void startNewResearch();
    void startOpenResearch();
    void startOpenExample();

    void updateControls();
    void updateStatusLabels();
    void rebuildConfigForms();
    void rebuildModuleTree();
    void rebuildSpec();
    void rebuildRequirements();
    void rebuildConnections();
    void rebuildWaveform();
    void updateCursorTable();
    struct SignalSelection {
        int index = 0;
        bool difference = false;
        QString name;
    };
    std::vector<SignalSelection> checked_signals() const;
    void rebuildDiff();
    void rebuildStateFields();
    void rebuildSampleSelectors();
    void showDeclarationDetails(const session::DeclarationView& declaration);
    void showRequirementDetails(const session::RequirementView& requirement);
    void showDeclarationAt(int index);
    void showRequirementAt(int index);
    QTreeWidgetItem* module_node_for(QTreeWidget* tree, std::map<std::string, QTreeWidgetItem*>& nodes,
                                     const std::string& path);
    void requestSampleDetail(std::int64_t frame);
    void requestLatestSample();
    void chooseOpenExperiment();
    void chooseSaveExperiment();
    void saveExperiment();
    void onExperimentSaved(const QString& path, bool ok, const QString& detail);
    void onExperimentOpened(const QString& path, bool ok, const QString& detail);
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

    session::Status status_;
    ModelSnapshot model_;
    session::ComparisonView current_comparison_;
    std::vector<session::DiagnosticView> diagnostics_;
    std::vector<SeriesData> series_;
    QTreeWidgetItem* observation_group_ = nullptr;
    QTreeWidgetItem* diff_group_ = nullptr;
    bool busy_ = false;
    bool closing_ = false;
    QString pending_;
    std::int64_t selected_frame_ = -1;
    int selected_series_ = 0;
    bool waveform_rebuilding_ = false;

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

    // 左侧
    QTreeWidget* module_tree_ = nullptr;
    QTreeWidget* spec_tree_ = nullptr;
    QTreeWidget* requirements_tree_ = nullptr;
    QTreeWidget* connections_tree_ = nullptr;

    // 模块库（中央工作区页签，默认不打开，载入模块包时自动打开）
    QWidget* package_page_ = nullptr;
    QTableWidget* package_table_ = nullptr;
    QPushButton* package_load_button_ = nullptr;
    QPushButton* package_load_folder_button_ = nullptr;
    QPushButton* package_unload_button_ = nullptr;
    QAction* package_tab_action_ = nullptr;
    QAction* load_module_action_ = nullptr;
    QAction* load_module_folder_action_ = nullptr;
    QAction* unload_module_action_ = nullptr;
    QAction* new_research_action_ = nullptr;
    std::vector<session::ModulePackageView> packages_;

    // 中央工作区页签（浏览器式：可拖动重排、可关闭、从查看菜单重开）
    QWidget* timeline_page_ = nullptr;
    QWidget* diff_page_ = nullptr;
    QAction* timeline_action_ = nullptr;
    QAction* diff_action_ = nullptr;

    // 系统编辑器（中央工作区页签）
    QWidget* editor_page_ = nullptr;
    QAction* editor_action_ = nullptr;
    QLabel* system_name_label_ = nullptr;
    QPushButton* new_system_button_ = nullptr;
    QPushButton* open_system_button_ = nullptr;
    QPushButton* save_system_button_ = nullptr;
    QString system_file_;  // 当前因果系统文件（`.aasm`）；空表示尚未保存
    QComboBox* definition_combo_ = nullptr;
    QLineEdit* instance_edit_ = nullptr;
    QPushButton* add_module_button_ = nullptr;
    QTreeWidget* system_tree_ = nullptr;
    QComboBox* provider_combo_ = nullptr;
    QPushButton* connect_button_ = nullptr;
    QPushButton* disconnect_button_ = nullptr;
    QPushButton* remove_module_button_ = nullptr;
    QPushButton* editor_check_button_ = nullptr;
    QPushButton* editor_apply_button_ = nullptr;
    QComboBox* advance_combo_ = nullptr;
    QListWidget* observation_list_ = nullptr;
    QLabel* input_label_ = nullptr;
    bool editor_updating_ = false;
    bool system_dirty_ = false;  // 因果系统草稿相对系统文件的未保存状态

    // 开始页（中央工作区页签，默认打开）
    QWidget* start_page_ = nullptr;
    QAction* start_action_ = nullptr;
    QPushButton* start_new_button_ = nullptr;
    QPushButton* start_open_button_ = nullptr;
    QPushButton* start_example_button_ = nullptr;

    // 中部
    QScrollArea* config_area_ = nullptr;
    QWidget* config_container_ = nullptr;
    QVBoxLayout* config_layout_ = nullptr;
    std::map<std::string, ConfigEditor*> config_editors_;
    QTabWidget* results_tabs_ = nullptr;
    QTreeWidget* signal_tree_ = nullptr;
    WaveformWidget* waveform_ = nullptr;
    QTableWidget* cursor_table_ = nullptr;
    QWidget* series_checks_host_ = nullptr;
    std::vector<QCheckBox*> series_checks_;
    QTableWidget* diff_table_ = nullptr;
    QLabel* diff_note_ = nullptr;

    // 右侧
    QTextBrowser* details_view_ = nullptr;
    QLabel* checkpoint_label_ = nullptr;
    QTableWidget* state_table_ = nullptr;
    QComboBox* sample_series_combo_ = nullptr;
    QLabel* sample_label_ = nullptr;
    QTreeWidget* sample_tree_ = nullptr;

    // 底部
    QTabWidget* bottom_tabs_ = nullptr;
    QTreeWidget* diagnostics_tree_ = nullptr;
    QTextBrowser* causes_view_ = nullptr;
    QTableWidget* check_table_ = nullptr;
    QLabel* check_note_ = nullptr;
    QLabel* record_label_ = nullptr;
    QTableWidget* record_table_ = nullptr;

    QLabel* status_label_ = nullptr;
    QLabel* revision_label_ = nullptr;

    // 可停靠/浮动的面板：模块管理器、配置与状态为停靠窗口，结果为主工作区。
    QTabWidget* left_pane_ = nullptr;
    QWidget* right_pane_ = nullptr;
    QDockWidget* modules_dock_ = nullptr;
    QDockWidget* config_dock_ = nullptr;
    QDockWidget* state_dock_ = nullptr;

    // 菜单栏：已实装命令与按钮同源，占位项禁用。
    QAction* open_action_ = nullptr;
    QAction* open_experiment_action_ = nullptr;
    QAction* save_action_ = nullptr;
    QAction* save_as_action_ = nullptr;
    QString current_file_;
    bool file_dirty_ = false;
    session::RecordView record_view_;
    std::vector<std::pair<QAction*, QPushButton*>> action_buttons_;
    QDockWidget* diagnostics_dock_ = nullptr;

    std::function<void()> close_handler_;
};

}  // namespace ascend::workbench
