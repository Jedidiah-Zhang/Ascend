#include "main_window.hpp"

#include "branch_dialog.hpp"
#include "config_editor.hpp"
#include "waveform_widget.hpp"

#include <optional>
#include <QAction>
#include <QCheckBox>
#include <QCloseEvent>
#include <QColor>
#include <QComboBox>
#include <QDockWidget>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QItemSelectionModel>
#include <QKeyEvent>
#include <QKeySequence>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QMouseEvent>
#include <QPushButton>
#include <QScrollArea>
#include <QSignalBlocker>
#include <QSpinBox>
#include <QSplitter>
#include <QStatusBar>
#include <QTabWidget>
#include <QTableWidget>
#include <QTextBrowser>
#include <QTreeWidget>
#include <QVBoxLayout>

#include <algorithm>
#include <utility>

namespace ascend::workbench {
namespace {

QString from_utf8(const std::string& text) { return QString::fromStdString(text); }

QColor series_color(int index) {
    static const QColor palette[] = {QColor(0x1f, 0x77, 0xb4), QColor(0xd6, 0x27, 0x28),
                                     QColor(0x2c, 0xa0, 0x2c)};
    return palette[index % 3];
}

QString reference_text(const ascend::Reference& reference) {
    if (reference.module.empty()) return from_utf8(reference.symbol);
    if (reference.symbol.empty()) return from_utf8(reference.module);
    return from_utf8(reference.module) + "/" + from_utf8(reference.symbol);
}

// 分组显示时的局部引用：去掉所属模块前缀（模块自身显示为符号名）。
QString local_reference_text(const ascend::Reference& reference, const std::string& group) {
    if (group.empty()) return reference_text(reference);
    if (reference.module == group) {
        ascend::Reference local = reference;
        local.module.clear();
        return reference_text(local);
    }
    if (reference.module.rfind(group + "/", 0) == 0) {
        ascend::Reference local = reference;
        local.module = local.module.substr(group.size() + 1);
        return reference_text(local);
    }
    return reference_text(reference);
}

QString phase_text(const UiTexts& texts, session::Phase phase) {
    switch (phase) {
        case session::Phase::empty: return ui_text(texts, "workbench.status.empty", "Empty session");
        case session::Phase::editing:
            return ui_text(texts, "workbench.status.editing", "Editable (no valid run)");
        case session::Phase::runnable: return ui_text(texts, "workbench.status.runnable", "Runnable");
        case session::Phase::stopped: return ui_text(texts, "workbench.status.stopped", "Stopped");
        case session::Phase::failed: return ui_text(texts, "workbench.status.failed", "Failed");
        case session::Phase::record: return ui_text(texts, "workbench.status.record", "Record (read-only)");
    }
    return {};
}

QString code_text(const UiTexts& texts, ascend::ErrorCode code) {
    switch (code) {
        case ascend::ErrorCode::invalid_declaration:
            return ui_text(texts, "workbench.error.invalid_declaration", "Invalid declaration");
        case ascend::ErrorCode::missing_symbol: return ui_text(texts, "workbench.error.missing_symbol", "Missing symbol");
        case ascend::ErrorCode::wrong_kind: return ui_text(texts, "workbench.error.wrong_kind", "Kind mismatch");
        case ascend::ErrorCode::type_mismatch: return ui_text(texts, "workbench.error.type_mismatch", "Type mismatch");
        case ascend::ErrorCode::argument_count: return ui_text(texts, "workbench.error.argument_count", "Argument count");
        case ascend::ErrorCode::validation_failed:
            return ui_text(texts, "workbench.error.validation_failed", "Validation failed");
        case ascend::ErrorCode::execution_failed:
            return ui_text(texts, "workbench.error.execution_failed", "Execution failed");
        case ascend::ErrorCode::missing_module: return ui_text(texts, "workbench.error.missing_module", "Missing module");
        case ascend::ErrorCode::missing_requirement:
            return ui_text(texts, "workbench.error.missing_requirement", "Missing requirement");
        case ascend::ErrorCode::unconnected_requirement:
            return ui_text(texts, "workbench.error.unconnected_requirement", "Unconnected requirement");
        case ascend::ErrorCode::contract_mismatch:
            return ui_text(texts, "workbench.error.contract_mismatch", "Contract mismatch");
        case ascend::ErrorCode::invalid_config: return ui_text(texts, "workbench.error.invalid_config", "Invalid configuration");
        case ascend::ErrorCode::invalid_assembly: return ui_text(texts, "workbench.error.invalid_assembly", "Invalid assembly");
        case ascend::ErrorCode::invalid_json: return ui_text(texts, "workbench.error.invalid_json", "Record syntax");
        case ascend::ErrorCode::invalid_i18n: return ui_text(texts, "workbench.error.invalid_i18n", "Text resources");
        case ascend::ErrorCode::io_failure: return ui_text(texts, "workbench.error.io_failure", "Read failure");
        case ascend::ErrorCode::state_incomplete: return ui_text(texts, "workbench.error.state_incomplete", "Incomplete state");
        case ascend::ErrorCode::state_mismatch: return ui_text(texts, "workbench.error.state_mismatch", "State mismatch");
        case ascend::ErrorCode::invalid_state: return ui_text(texts, "workbench.error.invalid_state", "Invalid state");
        case ascend::ErrorCode::invalid_intervention:
            return ui_text(texts, "workbench.error.invalid_intervention", "Invalid intervention");
        case ascend::ErrorCode::duplicate_module: return ui_text(texts, "workbench.error.duplicate_module", "Duplicate module");
        case ascend::ErrorCode::duplicate_symbol: return ui_text(texts, "workbench.error.duplicate_symbol", "Duplicate symbol");
        default: return ui_text(texts, "workbench.error.generic", "Diagnostic");
    }
}

QString causes_html(const UiTexts& texts, const session::DiagnosticView& view, int depth) {
    QString text;
    const QString indent = QString(depth * 16, ' ');
    text += indent + QStringLiteral("<b>%1</b>").arg(code_text(texts, view.code).toHtmlEscaped());
    if (!view.target.module.empty() || !view.target.symbol.empty()) {
        text += " · " + reference_text(view.target).toHtmlEscaped();
    }
    if (!view.path.empty()) text += " · " + from_utf8(view.path).toHtmlEscaped();
    text += "<br>" + indent + from_utf8(view.message).toHtmlEscaped() + "<br>";
    if (!view.source.empty()) {
        text += indent
                + ui_text(texts, "workbench.diag.source_html", "Source: %1<br>")
                      .arg(from_utf8(view.source).toHtmlEscaped());
    }
    for (const auto& cause : view.causes) {
        text += indent + ui_text(texts, "workbench.diag.cause_html", "Cause:<br>")
                + causes_html(texts, cause, depth + 1);
    }
    return text;
}

// 查看菜单：面板开关为勾选项，选择后保持菜单展开以便连续切换。
class StayOpenMenu : public QMenu {
public:
    using QMenu::QMenu;

protected:
    void mouseReleaseEvent(QMouseEvent* event) override {
        QAction* action = actionAt(event->pos());
        if (action != nullptr && action->isCheckable() && action->isEnabled()) {
            action->trigger();
            return;
        }
        QMenu::mouseReleaseEvent(event);
    }

    void keyPressEvent(QKeyEvent* event) override {
        const bool activate = event->key() == Qt::Key_Return || event->key() == Qt::Key_Enter ||
                              event->key() == Qt::Key_Space;
        QAction* action = activeAction();
        if (activate && action != nullptr && action->isCheckable() && action->isEnabled()) {
            action->trigger();
            return;
        }
        QMenu::keyPressEvent(event);
    }
};

QString causes_text(const UiTexts& texts, const session::DiagnosticView& view, int depth) {
    QString text;
    const QString indent = QString(depth * 2, ' ');
    text += indent + code_text(texts, view.code);
    if (!view.target.module.empty() || !view.target.symbol.empty()) {
        text += " · " + reference_text(view.target);
    }
    if (!view.path.empty()) text += " · " + from_utf8(view.path);
    text += "\n" + indent + from_utf8(view.message) + "\n";
    if (!view.source.empty()) {
        text += indent + ui_text(texts, "workbench.diag.source", "Source: ") + from_utf8(view.source) + "\n";
    }
    for (const auto& cause : view.causes) {
        text += indent + ui_text(texts, "workbench.diag.cause", "Cause:\n") + causes_text(texts, cause, depth + 1);
    }
    return text;
}

}  // namespace

MainWindow::MainWindow(SessionController* controller, std::shared_ptr<const session::AdapterRegistry> adapters,
                       std::shared_ptr<const UiTexts> texts, QWidget* parent)
    : QMainWindow(parent), controller_(controller), adapters_(std::move(adapters)), texts_(std::move(texts)) {
    int_adapter_ = adapters_->find(typeid(std::int64_t));
    setWindowTitle(ui_text(*texts_, "workbench.app.display_name", "Ascend Research Platform · Causal Modeling Workbench"));
    resize(1280, 820);
    buildLayout();
    buildDiagnosticsDock();
    buildMenuBar();

    connect(controller_, &SessionController::busyChanged, this, &MainWindow::onBusyChanged);
    connect(controller_, &SessionController::statusChanged, this, &MainWindow::onStatusChanged);
    connect(controller_, &SessionController::modelChanged, this, &MainWindow::onModelChanged);
    connect(controller_, &SessionController::tracesReset, this, &MainWindow::onTracesReset);
    connect(controller_, &SessionController::samplesAppended, this, &MainWindow::onSamplesAppended);
    connect(controller_, &SessionController::comparisonReady, this, &MainWindow::onComparisonReady);
    connect(controller_, &SessionController::checkFinished, this, &MainWindow::onCheckFinished);
    connect(controller_, &SessionController::replayFinished, this, &MainWindow::onReplayFinished);
    connect(controller_, &SessionController::recordChanged, this, &MainWindow::onRecordChanged);
    connect(controller_, &SessionController::sampleDetailReady, this, &MainWindow::onSampleDetailReady);
    connect(controller_, &SessionController::diagnosticsReported, this, &MainWindow::onDiagnostics);
    connect(controller_, &SessionController::experimentSaved, this, &MainWindow::onExperimentSaved);
    connect(controller_, &SessionController::experimentOpened, this, &MainWindow::onExperimentOpened);

    for (const auto& error : texts_->load_errors) {
        auto* item = new QTreeWidgetItem(diagnostics_tree_);
        item->setText(0, ui_text(*texts_, "workbench.error.invalid_i18n", "Text resources"));
        item->setText(3, from_utf8(error));
    }

    updateControls();
    updateStatusLabels();
}

QString MainWindow::statusLine() const {
    QString text = phase_text(*texts_, status_.phase);
    if (status_.run_id.has_value()) {
        text += ui_text(*texts_, "workbench.status.run_suffix", " · run #%1 · source revision %2").arg(*status_.run_id).arg(status_.run_revision);
    }
    if (!status_.tracks.empty()) {
        text += ui_text(*texts_, "workbench.status.frame_suffix", " · frame");
        if (status_.tracks.size() == 1) {
            text += QStringLiteral(" %1").arg(status_.tracks.front().frame);
        } else {
            for (const auto& track : status_.tracks) {
                text += QStringLiteral(" %1 %2").arg(from_utf8(track.label)).arg(track.frame);
            }
        }
    }
    if (status_.has_checkpoint) {
        text += ui_text(*texts_, "workbench.status.checkpoint_suffix", " · checkpoint %1").arg(status_.checkpoint_frame);
    }
    return text;
}

void MainWindow::buildLayout() {
    // 中央为主工作区：命令栏 + 结果页签；其余面板做成停靠窗口，可浮动、嵌套与合并。
    auto* central = new QWidget(this);
    auto* root = new QVBoxLayout(central);
    root->setContentsMargins(6, 6, 6, 6);
    root->setSpacing(6);
    buildTopBar(root);
    buildResultsPane();
    root->addWidget(results_tabs_, 1);
    setCentralWidget(central);

    buildLeftPane();
    buildConfigPane();
    buildRightPane();

    const auto make_dock = [this](const char* object, const char* title_key, const char* fallback,
                                  QWidget* content) {
        auto* dock = new QDockWidget(ui_text(*texts_, title_key, fallback), this);
        dock->setObjectName(object);
        dock->setWidget(content);
        return dock;
    };
    modules_dock_ = make_dock("modulesDock", "workbench.view.left_panel", "Module manager", left_pane_);
    config_dock_ = make_dock("configDock", "workbench.view.config", "Configuration", config_area_);
    state_dock_ = make_dock("stateDock", "workbench.view.right_panel", "State", right_pane_);

    setDockNestingEnabled(true);
    setDockOptions(dockOptions() | QMainWindow::GroupedDragging);
    addDockWidget(Qt::LeftDockWidgetArea, modules_dock_);
    addDockWidget(Qt::LeftDockWidgetArea, config_dock_);
    splitDockWidget(modules_dock_, config_dock_, Qt::Vertical);
    addDockWidget(Qt::RightDockWidgetArea, state_dock_);
    resizeDocks({modules_dock_, config_dock_}, {320, 320}, Qt::Vertical);
    resizeDocks({modules_dock_, state_dock_}, {330, 360}, Qt::Horizontal);
}

void MainWindow::buildTopBar(QVBoxLayout* root) {
    auto* bar = new QWidget(this);
    auto* layout = new QHBoxLayout(bar);
    layout->setContentsMargins(0, 0, 0, 0);

    layout->addStretch(1);

    check_button_ = new QPushButton(ui_text(*texts_, "workbench.action.check", "Check"), bar);
    check_button_->setObjectName("checkButton");
    check_button_->setToolTip(ui_text(*texts_, "workbench.tooltip.check", "Factory, assembly, specification and type-adapter checks"));
    apply_button_ = new QPushButton(ui_text(*texts_, "workbench.action.apply", "Rebuild run"), bar);
    apply_button_->setObjectName("applyButton");
    apply_button_->setToolTip(ui_text(*texts_, "workbench.tooltip.apply", "Apply the current initial configuration and rebuild the run (traces and checkpoint reset)"));
    checkpoint_button_ = new QPushButton(ui_text(*texts_, "workbench.action.checkpoint", "Checkpoint"), bar);
    checkpoint_button_->setObjectName("checkpointButton");
    checkpoint_button_->setToolTip(ui_text(*texts_, "workbench.tooltip.checkpoint", "Create the shared checkpoint at the current frame"));
    branch_button_ = new QPushButton(ui_text(*texts_, "workbench.action.branch", "Branches…"), bar);
    branch_button_->setObjectName("branchButton");
    branch_button_->setToolTip(ui_text(*texts_, "workbench.tooltip.branch", "Build control and treated branches from the shared checkpoint"));
    reset_branches_button_ = new QPushButton(ui_text(*texts_, "workbench.action.reset_branches", "Rebuild from checkpoint"), bar);
    reset_branches_button_->setObjectName("resetBranchesButton");
    replay_button_ = new QPushButton(ui_text(*texts_, "workbench.action.replay", "Replay"), bar);
    replay_button_->setObjectName("replayButton");

    step_button_ = new QPushButton(ui_text(*texts_, "workbench.action.step", "Step"), bar);
    step_button_->setObjectName("stepButton");
    run_button_ = new QPushButton(ui_text(*texts_, "workbench.action.run", "Run"), bar);
    run_button_->setObjectName("runButton");
    steps_spin_ = new QSpinBox(bar);
    steps_spin_->setObjectName("stepsSpin");
    steps_spin_->setRange(1, static_cast<int>(session::max_steps_per_command));
    steps_spin_->setValue(1);
    steps_spin_->setToolTip(ui_text(*texts_, "workbench.label.steps_tooltip", "Number of steps to run"));
    stop_button_ = new QPushButton(ui_text(*texts_, "workbench.action.stop", "Stop"), bar);
    stop_button_->setObjectName("stopButton");

    layout->addWidget(check_button_);
    layout->addWidget(apply_button_);
    layout->addWidget(checkpoint_button_);
    layout->addWidget(branch_button_);
    layout->addWidget(reset_branches_button_);
    layout->addWidget(replay_button_);
    layout->addSpacing(12);
    layout->addWidget(step_button_);
    layout->addWidget(run_button_);
    layout->addWidget(steps_spin_);
    layout->addWidget(stop_button_);
    root->addWidget(bar);

    connect(check_button_, &QPushButton::clicked, this, [this] {
        dispatch("check");
    });
    connect(apply_button_, &QPushButton::clicked, this, [this] {
        dispatch("apply");
    });
    connect(step_button_, &QPushButton::clicked, this, [this] {
        dispatch("step");
    });
    connect(run_button_, &QPushButton::clicked, this, [this] {
        busy_ = true;
        pending_ = QStringLiteral("run");
        updateControls();
        controller_->clearStop();
        QMetaObject::invokeMethod(controller_, "runN", Qt::QueuedConnection,
                                  Q_ARG(int, steps_spin_->value()));
    });
    connect(stop_button_, &QPushButton::clicked, this, [this] { controller_->requestStop(); });
    connect(checkpoint_button_, &QPushButton::clicked, this, [this] {
        dispatch("createCheckpoint");
    });
    connect(reset_branches_button_, &QPushButton::clicked, this, [this] {
        dispatch("resetBranches");
    });
    connect(replay_button_, &QPushButton::clicked, this, [this] {
        dispatch("replay");
    });
    connect(branch_button_, &QPushButton::clicked, this, [this] {
        if (model_.state_fields.empty()) {
            QMessageBox::information(this, ui_text(*texts_, "workbench.branch.title_short", "Build branches"),
                                     ui_text(*texts_, "workbench.branch.need_checkpoint", "Run to a non-initial frame and create a checkpoint first."));
            return;
        }
        BranchDialog dialog(status_.checkpoint_frame, model_.state_fields, *int_adapter_, *texts_, this);
        if (dialog.exec() != QDialog::Accepted) return;
        std::vector<session::BranchRequest> branches;
        // 标签留空，由会话按其文本域给出“对照／干预”。
        branches.push_back(session::BranchRequest{{}, {}});
        branches.push_back(session::BranchRequest{{}, dialog.interventions()});
        busy_ = true;
        pending_ = QStringLiteral("branches");
        updateControls();
        QMetaObject::invokeMethod(controller_, "createBranches", Qt::QueuedConnection,
                                  Q_ARG(std::vector<ascend::session::BranchRequest>, branches));
    });
}

void MainWindow::buildLeftPane() {
    auto* tabs = new QTabWidget;
    tabs->setObjectName("leftPane");
    tabs->setMinimumWidth(260);
    left_pane_ = tabs;

    module_tree_ = new QTreeWidget(tabs);
    module_tree_->setObjectName("moduleTree");
    module_tree_->setColumnCount(2);
    module_tree_->setHeaderLabels({ui_text(*texts_, "workbench.table.module_symbol", "Module / symbol"),
                                   ui_text(*texts_, "workbench.table.type", "Type")});
    module_tree_->header()->setStretchLastSection(false);
    module_tree_->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    module_tree_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    tabs->addTab(module_tree_, ui_text(*texts_, "workbench.pane.modules", "Module tree"));

    spec_tree_ = new QTreeWidget(tabs);
    spec_tree_->setObjectName("specTree");
    spec_tree_->setHeaderHidden(true);
    tabs->addTab(spec_tree_, ui_text(*texts_, "workbench.pane.spec", "Spec"));

    requirements_tree_ = new QTreeWidget(tabs);
    requirements_tree_->setObjectName("requirementsTree");
    requirements_tree_->setColumnCount(3);
    requirements_tree_->setHeaderLabels({ui_text(*texts_, "workbench.table.symbol", "Symbol"),
                                         ui_text(*texts_, "workbench.table.type", "Type"),
                                         ui_text(*texts_, "workbench.table.description", "Description")});
    requirements_tree_->header()->setStretchLastSection(false);
    requirements_tree_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    requirements_tree_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    requirements_tree_->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    tabs->addTab(requirements_tree_, ui_text(*texts_, "workbench.pane.requirements", "Requirements"));

    connections_tree_ = new QTreeWidget(tabs);
    connections_tree_->setObjectName("connectionsTree");
    connections_tree_->setColumnCount(3);
    connections_tree_->setHeaderLabels({ui_text(*texts_, "workbench.table.requirement", "Requirement"),
                                        ui_text(*texts_, "workbench.table.provider", "Provider"),
                                        ui_text(*texts_, "workbench.table.forwarded", "Forwarded")});
    connections_tree_->header()->setStretchLastSection(false);
    connections_tree_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    connections_tree_->header()->setSectionResizeMode(1, QHeaderView::Stretch);
    connections_tree_->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    tabs->addTab(connections_tree_, ui_text(*texts_, "workbench.pane.connections", "Connections"));

    connect(module_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = module_tree_->selectedItems();
        if (selected.isEmpty()) return;
        QTreeWidgetItem* item = selected.front();
        if (item->data(0, Qt::UserRole + 1).toInt() != 1) return;
        showDeclarationAt(item->data(0, Qt::UserRole).toInt());
    });
    connect(requirements_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = requirements_tree_->selectedItems();
        if (selected.isEmpty()) return;
        QTreeWidgetItem* item = selected.front();
        if (item->data(0, Qt::UserRole + 1).toInt() != 1) return;
        showRequirementAt(item->data(0, Qt::UserRole).toInt());
    });
    connect(connections_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = connections_tree_->selectedItems();
        if (selected.isEmpty()) return;
        QTreeWidgetItem* item = selected.front();
        if (item->data(0, Qt::UserRole + 1).toInt() != 1) return;
        QString html = ui_text(*texts_, "workbench.detail.connection_title", "<h3>Connection</h3><p>%1</p>")
                           .arg(QStringLiteral("%1 ← %2").arg(item->text(0), item->text(1)).toHtmlEscaped());
        html += ui_text(*texts_, "workbench.detail.connection_note",
                        "<p style='color:#666'>Connections show the actual assembly; the environment implementation defines execution order; wiring does not imply a complete causal graph.</p>");
        details_view_->setHtml(html);
    });
}

void MainWindow::buildConfigPane() {
    config_area_ = new QScrollArea;
    config_area_->setObjectName("configArea");
    config_area_->setWidgetResizable(true);
    config_area_->setMinimumHeight(150);
    config_container_ = new QWidget(config_area_);
    config_layout_ = new QVBoxLayout(config_container_);
    config_layout_->setContentsMargins(4, 4, 4, 4);
    config_area_->setWidget(config_container_);
}

void MainWindow::buildResultsPane() {
    results_tabs_ = new QTabWidget;
    results_tabs_->setObjectName("resultsTabs");

    auto* timeline_page = new QWidget(results_tabs_);
    auto* timeline_layout = new QVBoxLayout(timeline_page);
    auto* timeline_bar = new QHBoxLayout;
    series_checks_host_ = new QWidget(timeline_page);
    auto* checks_layout = new QHBoxLayout(series_checks_host_);
    checks_layout->setContentsMargins(0, 0, 0, 0);
    series_checks_host_->setLayout(checks_layout);
    timeline_bar->addWidget(series_checks_host_);
    timeline_bar->addStretch(1);
    auto* timeline_hint = new QLabel(ui_text(*texts_, "workbench.waveform.hint",
                                             "Wheel to zoom · drag to pan · click to place cursor A · Shift+click for "
                                             "cursor B · double-click to fit"),
                                     timeline_page);
    timeline_hint->setStyleSheet(QStringLiteral("color: #666;"));
    timeline_bar->addWidget(timeline_hint);
    timeline_layout->addLayout(timeline_bar);

    auto* timeline_splitter = new QSplitter(Qt::Horizontal, timeline_page);
    signal_tree_ = new QTreeWidget(timeline_splitter);
    signal_tree_->setObjectName("signalTree");
    signal_tree_->setHeaderHidden(true);
    signal_tree_->setMinimumWidth(150);
    signal_tree_->setMaximumWidth(240);
    timeline_splitter->addWidget(signal_tree_);
    waveform_ = new WaveformWidget(texts_.get(), timeline_splitter);
    waveform_->setObjectName("waveform");
    timeline_splitter->addWidget(waveform_);
    timeline_splitter->setStretchFactor(1, 1);
    timeline_layout->addWidget(timeline_splitter, 1);

    cursor_table_ = new QTableWidget(timeline_page);
    cursor_table_->setObjectName("cursorTable");
    cursor_table_->setColumnCount(5);
    cursor_table_->setHorizontalHeaderLabels({ui_text(*texts_, "workbench.waveform.signal", "Signal"),
                                              ui_text(*texts_, "workbench.waveform.series", "Series"),
                                              ui_text(*texts_, "workbench.waveform.cursor_a", "A"),
                                              ui_text(*texts_, "workbench.waveform.cursor_b", "B"),
                                              ui_text(*texts_, "workbench.waveform.delta", "Δ")});
    cursor_table_->horizontalHeader()->setStretchLastSection(true);
    cursor_table_->verticalHeader()->setVisible(false);
    cursor_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    cursor_table_->setMaximumHeight(150);
    timeline_layout->addWidget(cursor_table_);
    results_tabs_->addTab(timeline_page, ui_text(*texts_, "workbench.pane.timeline", "Timeline"));

    auto* diff_page = new QWidget(results_tabs_);
    auto* diff_layout = new QVBoxLayout(diff_page);
    diff_note_ = new QLabel(diff_page);
    diff_note_->setWordWrap(true);
    diff_layout->addWidget(diff_note_);
    diff_table_ = new QTableWidget(diff_page);
    diff_table_->setObjectName("diffTable");
    diff_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    diff_layout->addWidget(diff_table_, 1);
    results_tabs_->addTab(diff_page, ui_text(*texts_, "workbench.pane.differences", "Shared-frame differences"));

    connect(signal_tree_, &QTreeWidget::itemChanged, this, [this] { rebuildWaveform(); });
    connect(waveform_, &WaveformWidget::cursorsChanged, this, &MainWindow::updateCursorTable);
    connect(waveform_, &WaveformWidget::frameSelected, this, [this](std::int64_t frame) {
        requestSampleDetail(frame);
    });
    connect(results_tabs_, &QTabWidget::currentChanged, this, [this](int index) {
        if (index == 1) dispatchReadonly("requestComparison");
    });
}

void MainWindow::buildRightPane() {
    auto* tabs = new QTabWidget;
    tabs->setObjectName("rightPane");
    tabs->setMinimumWidth(300);
    right_pane_ = tabs;

    details_view_ = new QTextBrowser(tabs);
    details_view_->setObjectName("detailsView");
    details_view_->setHtml(ui_text(*texts_, "workbench.detail.placeholder", "<p style='color:#666'>Select a module, public item or connection on the left to see details.</p>"));
    tabs->addTab(details_view_, ui_text(*texts_, "workbench.pane.details", "Item details"));

    auto* state_page = new QWidget(tabs);
    auto* state_layout = new QVBoxLayout(state_page);
    checkpoint_label_ = new QLabel(ui_text(*texts_, "workbench.state.no_checkpoint", "No checkpoint yet"), state_page);
    checkpoint_label_->setObjectName("checkpointLabel");
    checkpoint_label_->setWordWrap(true);
    state_layout->addWidget(checkpoint_label_);
    state_table_ = new QTableWidget(state_page);
    state_table_->setObjectName("stateTable");
    state_table_->setColumnCount(4);
    state_table_->setHorizontalHeaderLabels({ui_text(*texts_, "workbench.table.module", "Module"), ui_text(*texts_, "workbench.table.field", "Field"),
                                             ui_text(*texts_, "workbench.table.current_value", "Current value"), ui_text(*texts_, "workbench.table.editable", "Editable")});
    state_table_->horizontalHeader()->setStretchLastSection(true);
    state_table_->verticalHeader()->setVisible(false);
    state_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    state_layout->addWidget(state_table_, 1);
    tabs->addTab(state_page, ui_text(*texts_, "workbench.pane.state", "State and intervention"));

    auto* sample_page = new QWidget(tabs);
    auto* sample_layout = new QVBoxLayout(sample_page);
    auto* sample_bar = new QHBoxLayout;
    sample_bar->addWidget(new QLabel(ui_text(*texts_, "workbench.label.series", "Series:"), sample_page));
    sample_series_combo_ = new QComboBox(sample_page);
    sample_series_combo_->setObjectName("sampleSeriesCombo");
    sample_bar->addWidget(sample_series_combo_);
    sample_layout->addLayout(sample_bar);
    sample_label_ = new QLabel(ui_text(*texts_, "workbench.sample.select_hint", "Select a frame in the result table."), sample_page);
    sample_layout->addWidget(sample_label_);
    sample_tree_ = new QTreeWidget(sample_page);
    sample_tree_->setObjectName("sampleTree");
    sample_tree_->setColumnCount(3);
    sample_tree_->setHeaderLabels({ui_text(*texts_, "workbench.table.object", "Object"), ui_text(*texts_, "workbench.table.field", "Field"), ui_text(*texts_, "workbench.table.value", "Value")});
    sample_tree_->header()->setStretchLastSection(true);
    sample_layout->addWidget(sample_tree_, 1);
    tabs->addTab(sample_page, ui_text(*texts_, "workbench.pane.truth", "Truth and observations"));

    connect(sample_series_combo_, &QComboBox::currentIndexChanged, this, [this] {
        if (selected_frame_ >= 0) requestSampleDetail(selected_frame_);
        else requestLatestSample();
    });
}

void MainWindow::buildDiagnosticsDock() {
    auto* dock = new QDockWidget(ui_text(*texts_, "workbench.pane.diagnostics", "Diagnostics and records"), this);
    dock->setObjectName("diagnosticsDock");
    diagnostics_dock_ = dock;
    bottom_tabs_ = new QTabWidget(dock);

    auto* diagnostics_page = new QWidget(bottom_tabs_);
    auto* layout = new QVBoxLayout(diagnostics_page);
    layout->setContentsMargins(0, 0, 0, 0);
    diagnostics_tree_ = new QTreeWidget(diagnostics_page);
    diagnostics_tree_->setObjectName("diagnosticsTree");
    diagnostics_tree_->setColumnCount(4);
    diagnostics_tree_->setHeaderLabels({ui_text(*texts_, "workbench.table.category", "Category"), ui_text(*texts_, "workbench.table.target", "Target"),
                                        ui_text(*texts_, "workbench.table.source", "Source"), ui_text(*texts_, "workbench.table.message", "Message")});
    diagnostics_tree_->header()->setStretchLastSection(true);
    layout->addWidget(diagnostics_tree_, 1);
    causes_view_ = new QTextBrowser(diagnostics_page);
    causes_view_->setObjectName("causesView");
    causes_view_->setMaximumHeight(140);
    layout->addWidget(causes_view_);
    bottom_tabs_->addTab(diagnostics_page, ui_text(*texts_, "workbench.error.generic", "Diagnostic"));

    auto* check_page = new QWidget(bottom_tabs_);
    auto* check_layout = new QVBoxLayout(check_page);
    check_layout->setContentsMargins(0, 0, 0, 0);
    check_note_ = new QLabel(ui_text(*texts_, "workbench.check.not_yet", "Not checked yet."), check_page);
    check_note_->setWordWrap(true);
    check_layout->addWidget(check_note_);
    check_table_ = new QTableWidget(check_page);
    check_table_->setObjectName("checkTable");
    check_table_->setColumnCount(4);
    check_table_->setHorizontalHeaderLabels({ui_text(*texts_, "workbench.table.area", "Area"), ui_text(*texts_, "workbench.table.object", "Object"),
                                             ui_text(*texts_, "workbench.table.result", "Result"), ui_text(*texts_, "workbench.table.description", "Description")});
    check_table_->horizontalHeader()->setStretchLastSection(true);
    check_table_->verticalHeader()->setVisible(false);
    check_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    check_layout->addWidget(check_table_, 1);
    bottom_tabs_->addTab(check_page, ui_text(*texts_, "workbench.action.check", "Check"));

    auto* record_page = new QWidget(bottom_tabs_);
    auto* record_layout = new QVBoxLayout(record_page);
    record_layout->setContentsMargins(0, 0, 0, 0);
    record_label_ = new QLabel(ui_text(*texts_, "workbench.record.empty", "The record is empty."), record_page);
    record_layout->addWidget(record_label_);
    record_table_ = new QTableWidget(record_page);
    record_table_->setObjectName("recordTable");
    record_table_->setColumnCount(3);
    record_table_->setHorizontalHeaderLabels({ui_text(*texts_, "workbench.table.driven_frame", "Boundary before drive"), ui_text(*texts_, "workbench.table.input", "Input"),
                                              ui_text(*texts_, "workbench.table.value", "Value")});
    record_table_->horizontalHeader()->setStretchLastSection(true);
    record_table_->verticalHeader()->setVisible(false);
    record_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    record_layout->addWidget(record_table_, 1);
    bottom_tabs_->addTab(record_page, ui_text(*texts_, "workbench.pane.record", "Recorded inputs"));

    dock->setWidget(bottom_tabs_);
    addDockWidget(Qt::BottomDockWidgetArea, dock);
    dock->setMinimumHeight(180);

    status_label_ = new QLabel(ui_text(*texts_, "workbench.status.empty", "Empty session"), this);
    status_label_->setObjectName("statusLabel");
    revision_label_ = new QLabel(this);
    revision_label_->setObjectName("revisionLabel");
    statusBar()->addWidget(status_label_, 1);
    statusBar()->addPermanentWidget(revision_label_);

    connect(diagnostics_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = diagnostics_tree_->selectedItems();
        if (selected.isEmpty()) {
            causes_view_->clear();
            return;
        }
        const int index = selected.front()->data(0, Qt::UserRole).toInt();
        if (index < 0 || static_cast<std::size_t>(index) >= diagnostics_.size()) return;
        causes_view_->setHtml(causes_html(*texts_, diagnostics_[static_cast<std::size_t>(index)], 0));
    });
    connect(bottom_tabs_, &QTabWidget::currentChanged, this, [this](int index) {
        if (index == 2) dispatchReadonly("requestRecord");
    });
}

void MainWindow::updateControls() {
    const bool editable = !busy_ && status_.phase != session::Phase::empty;
    const bool runnable = !busy_ && (status_.phase == session::Phase::runnable || status_.phase == session::Phase::stopped);
    const bool compare = status_.branches == session::max_branches;
    check_button_->setEnabled(editable);
    apply_button_->setEnabled(editable);
    checkpoint_button_->setEnabled(runnable && series_.size() == 1 && !compare);
    branch_button_->setEnabled(runnable && status_.has_checkpoint && series_.size() == 1 && !compare);
    reset_branches_button_->setEnabled(!busy_ && compare);
    replay_button_->setEnabled(!busy_ && !series_.empty());
    step_button_->setEnabled(runnable);
    run_button_->setEnabled(runnable);
    steps_spin_->setEnabled(runnable);
    stop_button_->setEnabled(busy_ && pending_ == QStringLiteral("run"));
    for (auto& item : config_editors_) item.second->setEnabled(editable);
    for (const auto& [action, button] : action_buttons_) action->setEnabled(button->isEnabled());
    if (open_action_ != nullptr) open_action_->setEnabled(!busy_ && !closing_);
    const bool saveable = !busy_ && !closing_ && status_.phase != session::Phase::empty &&
                          status_.phase != session::Phase::editing;
    if (open_experiment_action_ != nullptr) open_experiment_action_->setEnabled(!busy_ && !closing_);
    if (save_action_ != nullptr) save_action_->setEnabled(saveable);
    if (save_as_action_ != nullptr) save_as_action_->setEnabled(saveable);
}

void MainWindow::buildMenuBar() {
    QMenuBar* bar = menuBar();
    const auto placeholder = [this](QMenu* menu, const char* key, const char* fallback) {
        QAction* action = menu->addAction(ui_text(*texts_, key, fallback));
        action->setEnabled(false);
        action->setToolTip(ui_text(*texts_, "workbench.placeholder.later",
                                   "Planned for a later stage; not implemented yet"));
        return action;
    };
    const auto wired = [this](QMenu* menu, QPushButton* button) {
        QAction* action = menu->addAction(button->text());
        action->setToolTip(button->toolTip());
        connect(action, &QAction::triggered, button, &QPushButton::click);
        action_buttons_.emplace_back(action, button);
        return action;
    };

    QMenu* file = bar->addMenu(ui_text(*texts_, "workbench.menu.file", "File"));
    file->setToolTipsVisible(true);
    open_action_ = file->addAction(ui_text(*texts_, "workbench.action.open_example", "Open example"));
    connect(open_action_, &QAction::triggered, this, [this] { dispatch("load"); });
    file->addSeparator();
    open_experiment_action_ = file->addAction(ui_text(*texts_, "workbench.action.open_experiment", "Open experiment…"));
    open_experiment_action_->setObjectName("openExperimentAction");
    open_experiment_action_->setShortcut(QKeySequence::Open);
    connect(open_experiment_action_, &QAction::triggered, this, &MainWindow::chooseOpenExperiment);
    save_action_ = file->addAction(ui_text(*texts_, "workbench.action.save_experiment", "Save"));
    save_action_->setObjectName("saveAction");
    save_action_->setShortcut(QKeySequence::Save);
    connect(save_action_, &QAction::triggered, this, &MainWindow::saveExperiment);
    save_as_action_ = file->addAction(ui_text(*texts_, "workbench.action.save_experiment_as", "Save as…"));
    save_as_action_->setObjectName("saveAsAction");
    save_as_action_->setShortcut(QKeySequence::SaveAs);
    connect(save_as_action_, &QAction::triggered, this, &MainWindow::chooseSaveExperiment);
    file->addSeparator();
    QAction* quit = file->addAction(ui_text(*texts_, "workbench.action.quit", "Quit"));
    connect(quit, &QAction::triggered, this, &QWidget::close);

    QMenu* edit = bar->addMenu(ui_text(*texts_, "workbench.menu.edit", "Edit"));
    edit->setToolTipsVisible(true);
    placeholder(edit, "workbench.action.undo", "Undo");
    placeholder(edit, "workbench.action.redo", "Redo");
    edit->addSeparator();
    placeholder(edit, "workbench.action.declaration_editor", "Declarations and bindings…");

    auto* view = new StayOpenMenu(ui_text(*texts_, "workbench.menu.view", "View"), this);
    view->setToolTipsVisible(true);
    bar->addMenu(view);
    const auto panel_action = [this](QMenu* menu, QWidget* panel, const char* key, const char* fallback) {
        QAction* action = menu->addAction(ui_text(*texts_, key, fallback));
        action->setCheckable(true);
        action->setChecked(!panel->isHidden());
        connect(action, &QAction::toggled, panel, &QWidget::setVisible);
        return action;
    };
    const auto dock_action = [this](QMenu* menu, QDockWidget* dock, const char* key, const char* fallback) {
        QAction* action = dock->toggleViewAction();
        action->setText(ui_text(*texts_, key, fallback));
        menu->addAction(action);
        return action;
    };
    dock_action(view, modules_dock_, "workbench.view.left_panel", "Module manager");
    dock_action(view, config_dock_, "workbench.view.config", "Configuration");
    panel_action(view, results_tabs_, "workbench.view.results", "Results");
    dock_action(view, state_dock_, "workbench.view.right_panel", "State");
    dock_action(view, diagnostics_dock_, "workbench.view.diagnostics", "Bottom panel (diagnostics and records)");
    view->addSeparator();
    placeholder(view, "workbench.action.switch_language", "Switch language…");

    QMenu* run = bar->addMenu(ui_text(*texts_, "workbench.menu.run", "Run"));
    run->setToolTipsVisible(true);
    wired(run, check_button_);
    wired(run, apply_button_);
    wired(run, checkpoint_button_);
    wired(run, branch_button_);
    wired(run, reset_branches_button_);
    wired(run, replay_button_);
    run->addSeparator();
    wired(run, step_button_);
    wired(run, run_button_);
    wired(run, stop_button_);
}

void MainWindow::updateStatusLabels() {
    if (!status_.model_name.empty()) updateWindowTitle();
    status_label_->setText(statusLine());
    QString revision = ui_text(*texts_, "workbench.status.draft_revision", "Draft revision %1").arg(status_.draft_revision);
    if (status_.dirty) {
        revision += ui_text(*texts_, "workbench.status.dirty", "(not applied; the run comes from revision %1)").arg(status_.run_revision);
        revision_label_->setStyleSheet(QStringLiteral("color: #b45309;"));
    } else {
        revision_label_->setStyleSheet(QStringLiteral("color: #555;"));
    }
    revision_label_->setText(revision);
}

void MainWindow::closeEvent(QCloseEvent* event) {
    closing_ = true;
    disconnect(controller_, nullptr, this, nullptr);
    controller_->requestStop();
    if (close_handler_) close_handler_();
    busy_ = false;
    pending_.clear();
    event->accept();
}

void MainWindow::dispatch(const char* method) {
    if (closing_ || controller_ == nullptr) return;
    busy_ = true;
    file_dirty_ = true;
    pending_ = QString::fromLatin1(method);
    updateControls();
    QMetaObject::invokeMethod(controller_, method, Qt::QueuedConnection);
}

void MainWindow::dispatchReadonly(const char* method) {
    if (closing_ || controller_ == nullptr) return;
    // 只读快照请求不占用忙碌标记，运行命令进行中仍可请求并保持停止可用。
    QMetaObject::invokeMethod(controller_, method, Qt::QueuedConnection);
}

void MainWindow::beginInitialLoad() {
    busy_ = true;
    pending_ = QStringLiteral("load");
    updateControls();
    QMetaObject::invokeMethod(controller_, "load", Qt::QueuedConnection);
}

void MainWindow::onBusyChanged(bool busy) {
    busy_ = busy;
    if (!busy) pending_.clear();
    updateControls();
}

void MainWindow::onStatusChanged(const session::Status& status) {
    status_ = status;
    updateStatusLabels();
    updateControls();
    rebuildWaveform();
}

void MainWindow::onModelChanged(const ModelSnapshot& model) {
    model_ = model;
    rebuildConfigForms();
    rebuildModuleTree();
    left_pane_->setTabText(0, model_.catalog_stale
                                  ? ui_text(*texts_, "workbench.pane.modules_stale",
                                            "Module tree (stale)")
                                  : ui_text(*texts_, "workbench.pane.modules", "Module tree"));
    rebuildSpec();
    rebuildRequirements();
    rebuildConnections();
    rebuildStateFields();
    rebuildSampleSelectors();
    updateControls();
    updateStatusLabels();
}

void MainWindow::onTracesReset(const std::vector<session::TrackTraceView>& traces) {
    series_.clear();
    for (const auto& trace : traces) {
        SeriesData series_data;
        series_data.label = from_utf8(trace.label);
        series_data.origin = trace.origin;
        for (const auto& sample : trace.samples) series_data.samples.push_back(sample);
        for (const auto& intervention : trace.interventions) series_data.interventions.push_back(intervention);
        for (const auto& event : trace.events) series_data.events.push_back(event);
        series_.push_back(std::move(series_data));
    }
    rebuildWaveform();
    rebuildSampleSelectors();
    diff_table_->clearContents();
    diff_table_->setRowCount(0);
    diff_note_->clear();
    requestLatestSample();
}

void MainWindow::onSamplesAppended(int series, const std::vector<session::SampleView>& samples,
                                   const std::vector<session::StepEvent>& events) {
    if (series < 0 || static_cast<std::size_t>(series) >= series_.size()) return;
    auto& series_data = series_[static_cast<std::size_t>(series)];
    for (const auto& sample : samples) series_data.samples.push_back(sample);
    for (const auto& event : events) series_data.events.push_back(event);
    rebuildWaveform();
    requestLatestSample();
}

void MainWindow::onComparisonReady(const session::ComparisonView& comparison) {
    current_comparison_ = comparison;
    rebuildDiff();
    rebuildWaveform();
}

void MainWindow::onCheckFinished(const session::CheckReport& report) {
    check_table_->setRowCount(0);
    for (const auto& item : report.items) {
        const int row = check_table_->rowCount();
        check_table_->insertRow(row);
        check_table_->setItem(row, 0, new QTableWidgetItem(from_utf8(item.area)));
        check_table_->setItem(row, 1, new QTableWidgetItem(from_utf8(item.subject)));
        check_table_->setItem(row, 2,
                              new QTableWidgetItem(item.passed ? ui_text(*texts_, "workbench.check.passed", "Passed") : ui_text(*texts_, "workbench.check.failed", "Failed")));
        check_table_->setItem(row, 3, new QTableWidgetItem(from_utf8(item.note)));
    }
    check_note_->setText(report.passed
                             ? ui_text(*texts_, "workbench.check.passed_note", "Check passed: covers factory and assembly, experiment specification, type adapters and implementation identifiers; passing is not a proof of implementation correctness or research qualification.")
                             : ui_text(*texts_, "workbench.check.failed_note", "Check failed: see the diagnostics and check items below."));
    bottom_tabs_->setCurrentIndex(1);
}

void MainWindow::onReplayFinished(const session::ReplayReport& report) {
    QString text;
    if (report.ok) {
        text = report.complete
                   ? ui_text(*texts_, "workbench.replay.success",
                             "Replay succeeded: verified truth and observations for %1 samples frame by frame.")
                         .arg(report.verified_frames)
                   : ui_text(*texts_, "workbench.replay.success_partial",
                             "Replay succeeded (some ranges not compared): verified truth and observations for %1 "
                             "samples frame by frame.")
                         .arg(report.verified_frames);
        if (!report.notes.empty()) {
            text += ui_text(*texts_, "workbench.replay.uncompared_header", "\n\nRanges not compared:");
            for (const auto& note : report.notes) text += "\n· " + from_utf8(note);
        }
    } else if (report.first_mismatch.has_value()) {
        const auto& mismatch = *report.first_mismatch;
        text = ui_text(*texts_, "workbench.replay.mismatch", "First mismatch: branch %1 · frame %2 · %3\nexpected %4, received %5")
                   .arg(from_utf8(mismatch.branch))
                   .arg(mismatch.frame)
                   .arg(from_utf8(mismatch.field))
                   .arg(from_utf8(mismatch.expected))
                   .arg(from_utf8(mismatch.received));
    } else if (report.diagnostic.has_value()) {
        text = ui_text(*texts_, "workbench.replay.failed", "Replay could not complete: %1").arg(from_utf8(report.diagnostic->message));
    } else {
        text = ui_text(*texts_, "workbench.replay.not_passed", "Replay did not pass.");
    }
    QMessageBox box(this);
    box.setWindowTitle(ui_text(*texts_, "workbench.replay.title", "Replay result"));
    box.setIcon(report.ok ? QMessageBox::Information : QMessageBox::Warning);
    box.setText(text);
    if (report.diagnostic.has_value()) box.setDetailedText(causes_text(*texts_, *report.diagnostic, 0));
    box.exec();
}

void MainWindow::chooseOpenExperiment() {
    if (busy_ || closing_) return;
    const QString path = QFileDialog::getOpenFileName(
        this, ui_text(*texts_, "workbench.action.open_experiment", "Open experiment…"), QString(),
        ui_text(*texts_, "workbench.filter.experiment", "Ascend experiment (*.aexp);;All files (*)"),
        nullptr, QFileDialog::DontUseNativeDialog);
    if (path.isEmpty()) return;
    pending_ = QStringLiteral("openExperiment");
    updateControls();
    QMetaObject::invokeMethod(controller_, "openExperiment", Qt::QueuedConnection, Q_ARG(QString, path));
}

void MainWindow::saveExperiment() {
    if (busy_ || closing_) return;
    if (current_file_.isEmpty()) {
        chooseSaveExperiment();
        return;
    }
    pending_ = QStringLiteral("saveExperiment");
    updateControls();
    QMetaObject::invokeMethod(controller_, "saveExperiment", Qt::QueuedConnection, Q_ARG(QString, current_file_));
}

void MainWindow::chooseSaveExperiment() {
    if (busy_ || closing_) return;
    QString path = QFileDialog::getSaveFileName(
        this, ui_text(*texts_, "workbench.action.save_experiment_as", "Save experiment as…"),
        current_file_.isEmpty() ? QStringLiteral("experiment.aexp") : current_file_,
        ui_text(*texts_, "workbench.filter.experiment", "Ascend experiment (*.aexp);;All files (*)"),
        nullptr, QFileDialog::DontUseNativeDialog);
    if (path.isEmpty()) return;
    if (!path.endsWith(QStringLiteral(".aexp"))) path += QStringLiteral(".aexp");
    pending_ = QStringLiteral("saveExperiment");
    updateControls();
    QMetaObject::invokeMethod(controller_, "saveExperiment", Qt::QueuedConnection, Q_ARG(QString, path));
}

void MainWindow::onExperimentSaved(const QString& path, bool ok) {
    if (!ok) {
        QString detail;
        if (!diagnostics_.empty()) detail = from_utf8(diagnostics_.front().message);
        QString text = ui_text(*texts_, "workbench.save.failed", "Could not save the experiment file: %1").arg(path);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.save.title", "Save experiment"), text);
        return;
    }
    current_file_ = path;
    file_dirty_ = false;
    updateWindowTitle();
    updateRecordSummary();
}

void MainWindow::onExperimentOpened(const QString& path, bool ok) {
    if (!ok) {
        QString detail;
        if (!diagnostics_.empty()) detail = from_utf8(diagnostics_.front().message);
        QString text = ui_text(*texts_, "workbench.open.failed", "Could not open the experiment file: %1").arg(path);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.open.title", "Open experiment"), text);
        return;
    }
    current_file_ = path;
    file_dirty_ = false;
    updateWindowTitle();
    updateRecordSummary();
}

void MainWindow::updateWindowTitle() {
    QString title = ui_text(*texts_, "workbench.app.window_title", "Ascend Causal Modeling Workbench: %1")
                        .arg(from_utf8(status_.model_name));
    if (!current_file_.isEmpty()) {
        title += ui_text(*texts_, "workbench.app.file_suffix", " — %1")
                     .arg(QFileInfo(current_file_).fileName());
        if (file_dirty_) title += QStringLiteral("*");
    }
    setWindowTitle(title);
}

void MainWindow::updateRecordSummary() {
    const QString file = current_file_.isEmpty()
                             ? ui_text(*texts_, "workbench.record.in_memory", "in-process only")
                             : QFileInfo(current_file_).fileName();
    record_label_->setText(ui_text(*texts_, "workbench.record.summary",
                                   "Branches: %1 · actual driven inputs: %2 · advance failures: %3 · file: %4")
                               .arg(record_view_.branches)
                               .arg(record_view_.inputs)
                               .arg(record_view_.failures)
                               .arg(file));
}

void MainWindow::onRecordChanged(const session::RecordView& record) {
    record_view_ = record;
    updateRecordSummary();
    record_table_->setRowCount(0);
    for (const auto& input : record.input_list) {
        const int row = record_table_->rowCount();
        record_table_->insertRow(row);
        record_table_->setItem(row, 0, new QTableWidgetItem(QString::number(input.frame)));
        record_table_->setItem(row, 1, new QTableWidgetItem(from_utf8(input.name)));
        record_table_->setItem(row, 2, new QTableWidgetItem(from_utf8(input.value)));
    }
}

void MainWindow::onDiagnostics(const std::vector<session::DiagnosticView>& diagnostics) {
    for (const auto& diagnostic : diagnostics) {
        diagnostics_.push_back(diagnostic);
        const int index = static_cast<int>(diagnostics_.size()) - 1;
        auto* item = new QTreeWidgetItem(diagnostics_tree_);
        item->setText(0, code_text(*texts_, diagnostic.code));
        item->setText(1, reference_text(diagnostic.target));
        item->setText(2, from_utf8(diagnostic.source));
        item->setText(3, from_utf8(diagnostic.message));
        item->setData(0, Qt::UserRole, index);
    }
    if (!diagnostics.empty()) {
        diagnostics_tree_->setCurrentItem(diagnostics_tree_->topLevelItem(diagnostics_tree_->topLevelItemCount() - 1));
        bottom_tabs_->setCurrentIndex(0);
    }
}

void MainWindow::onSampleDetailReady(int series, std::int64_t frame, const session::SampleDetailView& detail) {
    if (series != selected_series_ || frame != selected_frame_) return;
    sample_tree_->clear();
    if (!detail.found) {
        sample_label_->setText(ui_text(*texts_, "workbench.sample.none", "No sample to display at this frame."));
        return;
    }
    sample_label_->setText(ui_text(*texts_, "workbench.sample.caption", "Research truth and declared observations at frame %1 (shown separately)")
                               .arg(detail.frame));
    auto* truth = new QTreeWidgetItem(sample_tree_);
    truth->setText(0, ui_text(*texts_, "workbench.sample.truth", "Research truth (complete state)"));
    for (const auto& module : detail.truth) {
        auto* node = new QTreeWidgetItem(truth);
        node->setText(0, from_utf8(module.path));
        node->setText(1, module.stateless ? ui_text(*texts_, "workbench.sample.stateless", "stateless")
                                          : ui_text(*texts_, "workbench.sample.contract", "contract %1").arg(from_utf8(module.contract)));
        for (const auto& field : module.fields) {
            auto* leaf = new QTreeWidgetItem(node);
            leaf->setText(1, from_utf8(field.first));
            leaf->setText(2, from_utf8(field.second));
        }
        if (module.stateless) {
            auto* leaf = new QTreeWidgetItem(node);
            leaf->setText(1, ui_text(*texts_, "workbench.sample.no_own_state", "(no own state)"));
        }
    }
    auto* observations = new QTreeWidgetItem(sample_tree_);
    observations->setText(0, ui_text(*texts_, "workbench.sample.observations", "Declared observations (visible to the subject)"));
    for (const auto& entry : detail.observations) {
        auto* leaf = new QTreeWidgetItem(observations);
        leaf->setText(1, from_utf8(entry.first));
        leaf->setText(2, from_utf8(entry.second.display));
    }
    sample_tree_->expandAll();
}

void MainWindow::showDeclarationDetails(const session::DeclarationView& declaration) {
    QString html;
    html += QStringLiteral("<h3>%1</h3>").arg(reference_text(declaration.reference).toHtmlEscaped());
    html += ui_text(*texts_, "workbench.detail.kind_type_suffix", "<p><b>Kind:</b> %1 · <b>Type:</b> %2%3</p>")
                .arg(declaration.kind == ascend::SymbolKind::method ? ui_text(*texts_, "workbench.kind.public_method", "Public method")
                                                                     : ui_text(*texts_, "workbench.kind.value", "Value"))
                .arg(from_utf8(declaration.result_type).toHtmlEscaped())
                .arg(declaration.result_supported ? QString() : ui_text(*texts_, "workbench.detail.unsupported_suffix", " (no registered adapter)"));
    if (!declaration.parameters.empty()) {
        html += ui_text(*texts_, "workbench.detail.parameters", "<p><b>Parameters:</b><br>");
        for (const auto& parameter : declaration.parameters) {
            html += QStringLiteral("· %1：%2%3<br>")
                        .arg(from_utf8(parameter.name).toHtmlEscaped())
                        .arg(from_utf8(parameter.type).toHtmlEscaped())
                        .arg(parameter.supported ? QString() : ui_text(*texts_, "workbench.detail.unsupported_suffix", " (no registered adapter)"));
        }
        html += QStringLiteral("</p>");
    }
    if (!declaration.description.empty()) {
        html += ui_text(*texts_, "workbench.detail.description", "<p><b>Description:</b> %1</p>").arg(from_utf8(declaration.description).toHtmlEscaped());
    }
    if (!declaration.contract.empty()) {
        html += ui_text(*texts_, "workbench.detail.contract", "<p><b>Contract:</b> %1</p>").arg(from_utf8(declaration.contract).toHtmlEscaped());
    }
    if (!declaration.reads.empty() || !declaration.writes.empty()) {
        QString reads;
        for (const auto& item : declaration.reads) reads += reference_text(item).toHtmlEscaped() + " ";
        QString writes;
        for (const auto& item : declaration.writes) writes += reference_text(item).toHtmlEscaped() + " ";
        html += ui_text(*texts_, "workbench.detail.reads_writes", "<p><b>Declared reads:</b> %1<br><b>Declared writes:</b> %2</p>")
                    .arg(reads.isEmpty() ? QStringLiteral("—") : reads)
                    .arg(writes.isEmpty() ? QStringLiteral("—") : writes);
        html += ui_text(*texts_, "workbench.detail.declaration_note", "<p style='color:#666'>Declared relations describe the model structure; the execution path is shown by run records; declarations are not a proof of actual access.</p>");
    }
    details_view_->setHtml(html);
}

void MainWindow::showRequirementDetails(const session::RequirementView& requirement) {
    QString html;
    html += ui_text(*texts_, "workbench.detail.requirement_title", "<h3>Requirement %1</h3>")
                .arg(reference_text(requirement.reference).toHtmlEscaped());
    html += ui_text(*texts_, "workbench.detail.kind_type", "<p><b>Kind:</b> %1 · <b>Type:</b> %2</p>")
                .arg(requirement.kind == ascend::SymbolKind::method ? ui_text(*texts_, "workbench.kind.method", "Method")
                                                                    : ui_text(*texts_, "workbench.kind.value", "Value"))
                .arg(from_utf8(requirement.result_type).toHtmlEscaped());
    if (!requirement.contract.empty()) {
        html += ui_text(*texts_, "workbench.detail.contract", "<p><b>Contract:</b> %1</p>")
                    .arg(from_utf8(requirement.contract).toHtmlEscaped());
    }
    if (!requirement.description.empty()) {
        html += ui_text(*texts_, "workbench.detail.description", "<p><b>Description:</b> %1</p>")
                    .arg(from_utf8(requirement.description).toHtmlEscaped());
    }
    details_view_->setHtml(html);
}

void MainWindow::showDeclarationAt(int index) {
    int cursor = 0;
    for (const auto& module : model_.catalog.modules) {
        for (const auto& declaration : module.declarations) {
            if (cursor++ == index) {
                showDeclarationDetails(declaration);
                return;
            }
        }
    }
}

void MainWindow::showRequirementAt(int index) {
    int cursor = 0;
    for (const auto& module : model_.catalog.modules) {
        for (const auto& requirement : module.requirements) {
            if (cursor++ == index) {
                showRequirementDetails(requirement);
                return;
            }
        }
    }
}

void MainWindow::requestSampleDetail(std::int64_t frame) {
    if (closing_ || controller_ == nullptr || sample_series_combo_ == nullptr || series_.empty()) return;
    selected_frame_ = frame;
    selected_series_ = std::clamp(sample_series_combo_->currentIndex(), 0,
                                  static_cast<int>(series_.size()) - 1);
    QMetaObject::invokeMethod(controller_, "requestSampleDetail", Qt::QueuedConnection,
                              Q_ARG(int, selected_series_), Q_ARG(std::int64_t, selected_frame_));
}

void MainWindow::requestLatestSample() {
    if (series_.empty() || sample_series_combo_ == nullptr) return;
    const int series_index = std::clamp(sample_series_combo_->currentIndex(), 0,
                                        static_cast<int>(series_.size()) - 1);
    const auto& samples = series_[static_cast<std::size_t>(series_index)].samples;
    if (samples.empty()) return;
    requestSampleDetail(samples.back().frame);
}

void MainWindow::rebuildConfigForms() {
    // 复用已有编辑器，避免模型回显时打断输入。
    std::map<std::string, ConfigEditor*> editors;
    for (const auto& instance : model_.instances) {
        const std::string key = instance.scope + "/" + instance.name;
        auto found = config_editors_.find(key);
        if (found != config_editors_.end()) {
            editors.emplace(key, found->second);
            found->second->setConfig(instance.config);
            continue;
        }
        auto* box = new QGroupBox(
            ui_text(*texts_, "workbench.config.instance_box", "%1 (definition %2)").arg(from_utf8(instance.name), from_utf8(instance.definition)),
            config_container_);
        auto* layout = new QVBoxLayout(box);
        auto* editor = new ConfigEditor(*int_adapter_, *texts_, box);
        editor->setObjectName("configEditor_" + from_utf8(instance.name));
        editor->setConfig(instance.config);
        const std::string scope = instance.scope;
        const std::string name = instance.name;
        connect(editor, &ConfigEditor::configEdited, this, [this, scope, name](const ascend::Config& config) {
            if (busy_ || closing_ || controller_ == nullptr) return;
            busy_ = true;
            file_dirty_ = true;
            pending_ = QStringLiteral("config");
            updateControls();
            QMetaObject::invokeMethod(controller_, "setInstanceConfig", Qt::QueuedConnection,
                                      Q_ARG(QString, from_utf8(scope)), Q_ARG(QString, from_utf8(name)),
                                      Q_ARG(ascend::Config, config));
        });
        layout->addWidget(editor);
        box->setObjectName("instanceBox_" + from_utf8(instance.name));
        config_layout_->addWidget(box);
        editors.emplace(key, editor);
    }
    // 删除已经不在草稿中的实例编辑器。
    for (auto& item : config_editors_) {
        if (editors.count(item.first) != 0) continue;
        auto* editor = item.second;
        auto* box = editor->parentWidget();
        box->deleteLater();
    }
    config_editors_ = std::move(editors);
}


QTreeWidgetItem* MainWindow::module_node_for(QTreeWidget* tree, std::map<std::string, QTreeWidgetItem*>& nodes,
                                           const std::string& path) {
    const auto found = nodes.find(path);
    if (found != nodes.end()) return found->second;
    QTreeWidgetItem* parent = nullptr;
    if (!path.empty()) {
        const auto slash = path.rfind('/');
        parent = module_node_for(tree, nodes, slash == std::string::npos ? std::string{} : path.substr(0, slash));
    }
    auto* node = parent == nullptr ? new QTreeWidgetItem(tree) : new QTreeWidgetItem(parent);
    if (path.empty()) {
        node->setText(0, ui_text(*texts_, "workbench.tree.root_scope", "(root scope)"));
    } else {
        const auto slash = path.rfind('/');
        node->setText(0, from_utf8(slash == std::string::npos ? path : path.substr(slash + 1)));
        node->setToolTip(0, from_utf8(path));
    }
    node->setData(0, Qt::UserRole + 1, 0);
    nodes.emplace(path, node);
    return node;
}

void MainWindow::rebuildModuleTree() {
    module_tree_->clear();

    // 目录里每个公开符号只出现一次，按声明模块（reference.module）分组。
    struct Entry {
        int index;
        const session::DeclarationView* declaration;
    };
    std::map<std::string, std::vector<Entry>> by_module;
    int index = 0;
    for (const auto& module : model_.catalog.modules) {
        for (const auto& declaration : module.declarations) {
            by_module[declaration.reference.module].push_back(Entry{index++, &declaration});
        }
    }

    // 作用域按路径排序，父模块先于子模块；节点创建后先挂本模块的公开符号，
    // 子模块节点随后追加，形成“模块 → 符号 → 子模块”的结构。
    QFont method_font = module_tree_->font();
    method_font.setItalic(true);
    std::map<std::string, QTreeWidgetItem*> nodes;
    for (const auto& module : model_.catalog.modules) {
        auto* node = module_node_for(module_tree_, nodes, module.path);

        const auto found = by_module.find(module.path);
        if (found == by_module.end()) continue;
        const auto add_declaration = [&](const Entry& entry) {
            const auto& declaration = *entry.declaration;
            const bool method = declaration.kind == ascend::SymbolKind::method;
            auto* item = new QTreeWidgetItem(node);
            item->setText(0, method ? from_utf8(declaration.reference.symbol) + "()"
                                    : from_utf8(declaration.reference.symbol));
            if (method) item->setFont(0, method_font);
            item->setText(1, from_utf8(declaration.result_type));
            if (!declaration.description.empty()) item->setToolTip(0, from_utf8(declaration.description));
            item->setData(0, Qt::UserRole, entry.index);
            item->setData(0, Qt::UserRole + 1, 1);
        };
        for (const auto& entry : found->second) {
            if (entry.declaration->kind == ascend::SymbolKind::method) add_declaration(entry);
        }
        for (const auto& entry : found->second) {
            if (entry.declaration->kind != ascend::SymbolKind::method) add_declaration(entry);
        }
    }
    module_tree_->expandAll();
}

void MainWindow::rebuildSpec() {
    spec_tree_->clear();
    const auto add_group = [&](const char* key, const char* fallback) {
        auto* group = new QTreeWidgetItem(spec_tree_);
        group->setText(0, ui_text(*texts_, key, fallback));
        group->setData(0, Qt::UserRole + 1, 0);
        return group;
    };
    const auto add_entry = [&](QTreeWidgetItem* group, const QString& text) {
        auto* item = new QTreeWidgetItem(group);
        item->setText(0, text);
        item->setData(0, Qt::UserRole + 1, 1);
    };
    auto* advance_group = add_group("workbench.spec.advance", "Advance entry");
    add_entry(advance_group, reference_text(model_.spec.advance));
    auto* inputs_group =
        add_group("workbench.spec.inputs", "Inputs (driven in spec order before each advance)");
    for (const auto& entry : model_.spec.inputs) {
        add_entry(inputs_group, QStringLiteral("%1 → %2").arg(from_utf8(entry.first), reference_text(entry.second)));
    }
    auto* observations_group = add_group("workbench.spec.observations", "Observations");
    for (const auto& entry : model_.spec.observations) {
        add_entry(observations_group,
                  QStringLiteral("%1 → %2").arg(from_utf8(entry.first), reference_text(entry.second)));
    }
    spec_tree_->expandAll();
}

void MainWindow::rebuildRequirements() {
    requirements_tree_->clear();
    std::map<std::string, QTreeWidgetItem*> nodes;
    int index = 0;
    for (const auto& module : model_.catalog.modules) {
        for (const auto& requirement : module.requirements) {
            auto* node = module_node_for(requirements_tree_, nodes, requirement.reference.module);
            auto* item = new QTreeWidgetItem(node);
            item->setText(0, from_utf8(requirement.reference.symbol));
            item->setText(1, from_utf8(requirement.result_type));
            item->setText(2, from_utf8(requirement.description));
            if (!requirement.description.empty()) item->setToolTip(2, from_utf8(requirement.description));
            item->setData(0, Qt::UserRole, index++);
            item->setData(0, Qt::UserRole + 1, 1);
        }
    }
    requirements_tree_->expandAll();
}

void MainWindow::rebuildConnections() {
    connections_tree_->clear();
    std::map<std::string, QTreeWidgetItem*> nodes;
    for (const auto& module : model_.catalog.modules) {
        for (const auto& connection : module.connections) {
            auto* node = module_node_for(connections_tree_, nodes, module.path);
            auto* item = new QTreeWidgetItem(node);
            item->setText(0, local_reference_text(connection.requirement, module.path));
            item->setText(1, local_reference_text(connection.provider, module.path));
            item->setText(2, connection.forwarded ? ui_text(*texts_, "workbench.value.yes", "Yes")
                                                  : ui_text(*texts_, "workbench.value.no", "No"));
            item->setToolTip(0, reference_text(connection.requirement));
            item->setToolTip(1, reference_text(connection.provider));
            item->setData(0, Qt::UserRole + 1, 1);
        }
    }
    connections_tree_->expandAll();
}

void MainWindow::rebuildWaveform() {
    if (waveform_rebuilding_) return;
    waveform_rebuilding_ = true;

    if (series_checks_.size() != series_.size() ||
        series_checks_host_->findChildren<QCheckBox*>().size() != static_cast<int>(series_.size())) {
        auto* layout = series_checks_host_->layout();
        while (auto* item = layout->takeAt(0)) {
            delete item->widget();
            delete item;
        }
        series_checks_.clear();
        for (std::size_t index = 0; index < series_.size(); ++index) {
            auto* check = new QCheckBox(series_[index].label, series_checks_host_);
            check->setObjectName("seriesCheck_" + QString::number(index));
            check->setChecked(true);
            connect(check, &QCheckBox::toggled, this, [this] { rebuildWaveform(); });
            layout->addWidget(check);
            series_checks_.push_back(check);
        }
    }

    // 信号树：观测组（规格观测）与差值组（两分支存在时）。
    if (observation_group_ == nullptr && !model_.observations.empty()) {
        const QSignalBlocker blocker(signal_tree_);
        observation_group_ = new QTreeWidgetItem(signal_tree_);
        observation_group_->setText(0, ui_text(*texts_, "workbench.waveform.observations", "Observations"));
        observation_group_->setFlags(Qt::ItemIsEnabled);
        observation_group_->setExpanded(true);
        for (std::size_t index = 0; index < model_.observations.size(); ++index) {
            auto* item = new QTreeWidgetItem(observation_group_);
            item->setText(0, from_utf8(model_.observations[index]));
            item->setData(0, Qt::UserRole, static_cast<int>(index));
            item->setData(0, Qt::UserRole + 1, 0);
            item->setFlags(Qt::ItemIsUserCheckable | Qt::ItemIsEnabled | Qt::ItemIsSelectable);
            item->setCheckState(0, Qt::Checked);
        }
        signal_tree_->expandAll();
    }
    if (series_.size() > 2 && !model_.observations.empty()) {
        if (diff_group_ == nullptr) {
            const QSignalBlocker blocker(signal_tree_);
            diff_group_ = new QTreeWidgetItem(signal_tree_);
            diff_group_->setText(0, ui_text(*texts_, "workbench.waveform.diff_group", "Difference (treated − control)"));
            diff_group_->setFlags(Qt::ItemIsEnabled);
            diff_group_->setExpanded(true);
            for (std::size_t index = 0; index < model_.observations.size(); ++index) {
                auto* item = new QTreeWidgetItem(diff_group_);
                item->setText(0, from_utf8(model_.observations[index]));
                item->setData(0, Qt::UserRole, static_cast<int>(index));
                item->setData(0, Qt::UserRole + 1, 1);
                item->setFlags(Qt::ItemIsUserCheckable | Qt::ItemIsEnabled | Qt::ItemIsSelectable);
                item->setCheckState(0, Qt::Checked);
            }
            signal_tree_->expandAll();
        }
    } else if (diff_group_ != nullptr) {
        const QSignalBlocker blocker(signal_tree_);
        delete diff_group_;
        diff_group_ = nullptr;
    }

    QVector<WaveformWidget::Signal> rows;
    for (const auto& selection : checked_signals()) {
        WaveformWidget::Signal signal;
        if (selection.difference) {
            signal.name = ui_text(*texts_, "workbench.waveform.diff_name", "%1 (difference)")
                              .arg(selection.name);
            WaveformWidget::Series line;
            line.label = ui_text(*texts_, "workbench.waveform.diff_series", "Treated − control");
            line.color = QColor(0x8e, 0x44, 0xad);
            for (const auto& row : current_comparison_.rows) {
                if (selection.index < 0 ||
                    static_cast<std::size_t>(selection.index) >= row.cells.size()) {
                    continue;
                }
                const auto& cell = row.cells[static_cast<std::size_t>(selection.index)];
                if (!cell.integer.has_value()) continue;
                line.points.append(WaveformWidget::Point{
                    row.frame, true, static_cast<double>(*cell.integer), true});
            }
            signal.series.push_back(line);
            rows.push_back(signal);
            continue;
        }
        signal.name = selection.name;
        const auto signal_index = static_cast<std::size_t>(selection.index);
        for (std::size_t index = 0; index < series_.size(); ++index) {
            if (index >= series_checks_.size() || !series_checks_[index]->isChecked()) continue;
            WaveformWidget::Series line;
            line.label = series_[index].label;
            line.color = series_color(static_cast<int>(index));
            for (const auto& sample : series_[index].samples) {
                if (signal_index >= sample.observations.size()) continue;
                const auto& cell = sample.observations[signal_index];
                line.points.append(
                    WaveformWidget::Point{sample.frame, cell.numeric, cell.value, cell.exact});
            }
            signal.series.push_back(line);
        }
        rows.push_back(signal);
    }
    waveform_->setSignals(rows);

    // 事件轨：分支起点、干预、检查点、失败与停止。
    QVector<WaveformWidget::Event> events;
    for (std::size_t index = 0; index < series_.size(); ++index) {
        const auto& series_data = series_[index];
        if (index > 0) {
            events.append(WaveformWidget::Event{
                series_data.origin,
                ui_text(*texts_, "workbench.waveform.event.branch", "Branch start") + " " + series_data.label,
                QColor(0x8e, 0x44, 0xad), WaveformWidget::EventKind::branch});
        }
        for (const auto& intervention : series_data.interventions) {
            QString text = ui_text(*texts_, "workbench.waveform.event.intervention", "Intervention");
            text += QStringLiteral(" ") + from_utf8(intervention.module);
            if (!intervention.field.empty()) text += "/" + from_utf8(intervention.field);
            text += QStringLiteral(": %1 → %2")
                        .arg(from_utf8(intervention.previous), from_utf8(intervention.replacement));
            events.append(WaveformWidget::Event{series_data.origin, text, QColor(0xe6, 0x7e, 0x22),
                                                WaveformWidget::EventKind::intervention});
        }
        for (const auto& step_event : series_data.events) {
            QString text;
            QColor color(0xc0, 0x39, 0x2b);
            auto kind = WaveformWidget::EventKind::failure;
            switch (step_event.kind) {
                case session::StepEvent::Kind::stopped:
                    text = ui_text(*texts_, "workbench.waveform.event.stopped", "Stopped");
                    color = QColor(0x77, 0x77, 0x77);
                    kind = WaveformWidget::EventKind::stop;
                    break;
                case session::StepEvent::Kind::input_failed:
                    text = ui_text(*texts_, "workbench.waveform.event.input_failed", "Input failed");
                    break;
                case session::StepEvent::Kind::advance_failed:
                    text = ui_text(*texts_, "workbench.waveform.event.advance_failed", "Advance failed");
                    break;
                case session::StepEvent::Kind::sample_failed:
                    text = ui_text(*texts_, "workbench.waveform.event.sample_failed", "Sample failed");
                    break;
                case session::StepEvent::Kind::completed:
                    continue;
            }
            events.append(WaveformWidget::Event{step_event.frame, text, color, kind});
        }
    }
    if (status_.has_checkpoint) {
        events.append(WaveformWidget::Event{
            status_.checkpoint_frame,
            ui_text(*texts_, "workbench.waveform.event.checkpoint", "Checkpoint"),
            QColor(0x17, 0xa2, 0xb8), WaveformWidget::EventKind::checkpoint});
    }
    waveform_->setEvents(events);
    waveform_->setCheckpoint(
        status_.has_checkpoint ? std::optional<std::int64_t>(status_.checkpoint_frame) : std::nullopt,
        series_.size() > 2);

    waveform_rebuilding_ = false;
    updateCursorTable();
}

std::vector<MainWindow::SignalSelection> MainWindow::checked_signals() const {
    std::vector<SignalSelection> result;
    for (int group_index = 0; group_index < signal_tree_->topLevelItemCount(); ++group_index) {
        auto* group = signal_tree_->topLevelItem(group_index);
        for (int child = 0; child < group->childCount(); ++child) {
            auto* item = group->child(child);
            if (item->checkState(0) != Qt::Checked) continue;
            result.push_back(SignalSelection{item->data(0, Qt::UserRole).toInt(),
                                            item->data(0, Qt::UserRole + 1).toInt() == 1,
                                            item->text(0)});
        }
    }
    return result;
}

void MainWindow::updateCursorTable() {
    cursor_table_->setRowCount(0);
    const std::int64_t a = waveform_->cursorA();
    const std::int64_t b = waveform_->cursorB();
    const auto cell_at = [](const SeriesData& series_data, std::int64_t frame, std::size_t signal_index)
        -> const session::CellView* {
        for (const auto& sample : series_data.samples) {
            if (sample.frame != frame) continue;
            if (signal_index >= sample.observations.size()) return nullptr;
            return &sample.observations[signal_index];
        }
        return nullptr;
    };
    const auto subtract_checked = [](std::int64_t value_a, std::int64_t value_b,
                                     std::int64_t& difference) {
        if (value_a > 0 && value_b < std::numeric_limits<std::int64_t>::min() + value_a) return false;
        if (value_a < 0 && value_b > std::numeric_limits<std::int64_t>::max() + value_a) return false;
        difference = value_b - value_a;
        return true;
    };
    const auto diff_cell_at = [this](std::int64_t frame, int signal_index) -> const session::DiffCellView* {
        for (const auto& row : current_comparison_.rows) {
            if (row.frame != frame) continue;
            if (signal_index < 0 || static_cast<std::size_t>(signal_index) >= row.cells.size()) return nullptr;
            return &row.cells[static_cast<std::size_t>(signal_index)];
        }
        return nullptr;
    };
    const auto delta_text = [&](std::optional<std::int64_t> value_a, std::optional<std::int64_t> value_b) {
        if (!value_a.has_value() || !value_b.has_value()) return QStringLiteral("—");
        std::int64_t difference = 0;
        return subtract_checked(*value_a, *value_b, difference)
                   ? QString::number(difference)
                   : ui_text(*texts_, "workbench.waveform.overflow", "overflow");
    };
    for (const auto& selection : checked_signals()) {
        if (selection.difference) {
            const int row = cursor_table_->rowCount();
            cursor_table_->insertRow(row);
            cursor_table_->setItem(row, 0, new QTableWidgetItem(
                ui_text(*texts_, "workbench.waveform.diff_name", "%1 (difference)").arg(selection.name)));
            cursor_table_->setItem(row, 1, new QTableWidgetItem(
                ui_text(*texts_, "workbench.waveform.diff_series", "Treated − control")));
            const auto* cell_a = diff_cell_at(a, selection.index);
            const auto* cell_b = diff_cell_at(b, selection.index);
            cursor_table_->setItem(row, 2,
                                   new QTableWidgetItem(cell_a != nullptr ? from_utf8(cell_a->difference)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 3,
                                   new QTableWidgetItem(cell_b != nullptr ? from_utf8(cell_b->difference)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 4, new QTableWidgetItem(delta_text(
                cell_a != nullptr ? cell_a->integer : std::nullopt,
                cell_b != nullptr ? cell_b->integer : std::nullopt)));
            continue;
        }
        for (std::size_t index = 0; index < series_.size(); ++index) {
            if (index >= series_checks_.size() || !series_checks_[index]->isChecked()) continue;
            const int row = cursor_table_->rowCount();
            cursor_table_->insertRow(row);
            cursor_table_->setItem(row, 0, new QTableWidgetItem(selection.name));
            cursor_table_->setItem(row, 1, new QTableWidgetItem(series_[index].label));
            const auto signal = static_cast<std::size_t>(selection.index);
            const session::CellView* cell_a = cell_at(series_[index], a, signal);
            const session::CellView* cell_b = cell_at(series_[index], b, signal);
            cursor_table_->setItem(row, 2,
                                   new QTableWidgetItem(cell_a != nullptr ? from_utf8(cell_a->display)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 3,
                                   new QTableWidgetItem(cell_b != nullptr ? from_utf8(cell_b->display)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 4, new QTableWidgetItem(delta_text(
                cell_a != nullptr ? cell_a->integer : std::nullopt,
                cell_b != nullptr ? cell_b->integer : std::nullopt)));
        }
    }
}

void MainWindow::rebuildDiff() {
    const auto& comparison = current_comparison_;
    diff_table_->clear();
    diff_table_->setRowCount(0);
    diff_table_->setColumnCount(static_cast<int>(comparison.variables.size()) + 1);
    QStringList headers{ui_text(*texts_, "workbench.table.frame", "Boundary")};
    for (const auto& name : comparison.variables) headers << from_utf8(name);
    diff_table_->setHorizontalHeaderLabels(headers);
    for (const auto& row : comparison.rows) {
        const int position = diff_table_->rowCount();
        diff_table_->insertRow(position);
        diff_table_->setItem(position, 0, new QTableWidgetItem(QString::number(row.frame)));
        for (std::size_t index = 0; index < row.cells.size(); ++index) {
            const auto& cell = row.cells[index];
            auto* item = new QTableWidgetItem(from_utf8(cell.difference));
            item->setToolTip(ui_text(*texts_, "workbench.diff.tooltip", "treated %1 − control %2").arg(from_utf8(cell.treated),
                                                                      from_utf8(cell.control)));
            if (!cell.comparable) item->setForeground(QColor(0xb4, 0x53, 0x09));
            diff_table_->setItem(position, static_cast<int>(index) + 1, item);
        }
    }
    QString note = ui_text(*texts_, "workbench.diff.note", "Differences are treated − control using exact integer arithmetic; only shared frames with samples on both branches are compared.");
    if (!comparison.unpaired.empty()) {
        QStringList frames;
        for (const auto frame : comparison.unpaired) frames << QString::number(frame);
        note += ui_text(*texts_, "workbench.diff.unpaired", " Boundaries with samples on one side only: %1 (unpaired; not used for differences).").arg(frames.join(", "));
    }
    diff_note_->setText(note);
}

void MainWindow::rebuildStateFields() {
    state_table_->setRowCount(0);
    if (status_.has_checkpoint) {
        QString text = ui_text(*texts_, "workbench.state.checkpoint_hint", "Checkpoint frame %1; interventions target stateful modules at the checkpoint (the first version supports integer fields).")
                           .arg(status_.checkpoint_frame);
        for (const auto& track : status_.tracks) {
            for (const auto& intervention : track.interventions) {
                text += ui_text(*texts_, "workbench.intervention.line", "\nBranch \"%1\" intervention: %2#%3 changed from %4 to %5")
                            .arg(from_utf8(track.label), from_utf8(intervention.module),
                                 from_utf8(intervention.field), from_utf8(intervention.previous),
                                 from_utf8(intervention.replacement));
            }
        }
        checkpoint_label_->setText(text);
    } else {
        checkpoint_label_->setText(ui_text(*texts_, "workbench.state.no_checkpoint_period", "No checkpoint yet."));
    }
    for (const auto& field : model_.state_fields) {
        const int row = state_table_->rowCount();
        state_table_->insertRow(row);
        state_table_->setItem(row, 0, new QTableWidgetItem(from_utf8(field.module)));
        state_table_->setItem(row, 1, new QTableWidgetItem(field.field.empty()
                                                               ? ui_text(*texts_, "workbench.value.whole_state", "(whole state object)")
                                                               : from_utf8(field.field)));
        state_table_->setItem(row, 2, new QTableWidgetItem(from_utf8(field.display)));
        state_table_->setItem(row, 3, new QTableWidgetItem(field.integer ? ui_text(*texts_, "workbench.value.yes", "Yes")
                                                                         : ui_text(*texts_, "workbench.value.no", "No")));
    }
}

void MainWindow::rebuildSampleSelectors() {
    const int previous = sample_series_combo_->currentIndex();
    sample_series_combo_->blockSignals(true);
    sample_series_combo_->clear();
    for (const auto& label : model_.series_labels) sample_series_combo_->addItem(from_utf8(label));
    if (previous >= 0 && previous < sample_series_combo_->count()) sample_series_combo_->setCurrentIndex(previous);
    sample_series_combo_->blockSignals(false);
    sample_tree_->clear();
}

}  // namespace ascend::workbench
