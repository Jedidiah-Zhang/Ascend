#pragma once

#include "session_controller.hpp"
#include "ui_text.hpp"

#include <ascend/session/adapter.hpp>

#include <QMainWindow>
#include <QString>
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

    // 测试访问：当前会话状态、状态行文本、结果表格行数与命令执行标记。
    const session::Status& currentStatus() const noexcept { return status_; }
    QString statusLine() const;
    int resultRowCount() const;
    bool busy() const noexcept { return busy_; }
    // 启动时由工作台提交：标记忙状态并排队打开示例。
    void beginInitialLoad();

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
    void onSampleDetailReady(int series, std::int64_t boundary, const ascend::session::SampleDetailView& detail);
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
    void buildMenuBar();

    void updateControls();
    void updateStatusLabels();
    void rebuildConfigForms();
    void rebuildModuleTree();
    void rebuildSpec();
    void rebuildRequirements();
    void rebuildConnections();
    void rebuildResultTable();
    void rebuildWaveform();
    void updateCursorTable();
    std::vector<std::pair<int, QString>> checked_signals() const;
    void rebuildDiff();
    void rebuildStateFields();
    void rebuildSampleSelectors();
    void showDeclarationDetails(const session::DeclarationView& declaration);
    void showRequirementDetails(const session::RequirementView& requirement);
    void showDeclarationAt(int index);
    void showRequirementAt(int index);
    QTreeWidgetItem* module_node_for(QTreeWidget* tree, std::map<std::string, QTreeWidgetItem*>& nodes,
                                     const std::string& path);
    void requestCurrentSample();
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
    bool busy_ = false;
    bool closing_ = false;
    QString pending_;
    std::map<std::int64_t, int> row_index_;
    std::int64_t selected_boundary_ = -1;
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

    // 中部
    QScrollArea* config_area_ = nullptr;
    QWidget* config_container_ = nullptr;
    QVBoxLayout* config_layout_ = nullptr;
    std::map<std::string, ConfigEditor*> config_editors_;
    QTableWidget* result_table_ = nullptr;
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
    std::vector<std::pair<QAction*, QPushButton*>> action_buttons_;
    QDockWidget* diagnostics_dock_ = nullptr;

    std::function<void()> close_handler_;
};

}  // namespace ascend::workbench
