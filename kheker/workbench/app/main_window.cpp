#include "main_window.hpp"

#include "branch_dialog.hpp"
#include "panels/config_panel.hpp"
#include "panels/diagnostics_panel.hpp"
#include "panels/diff_panel.hpp"
#include "panels/modules_panel.hpp"
#include "panels/package_library_panel.hpp"
#include "panels/start_page_panel.hpp"
#include "panels/state_panel.hpp"
#include "panels/system_editor_panel.hpp"
#include "panels/timeline_panel.hpp"

#include "ui_format.hpp"

#include "workspace/workspace_model.hpp"

#include <optional>
#include <QAction>
#include <QCloseEvent>
#include <QDialog>
#include <QDialogButtonBox>
#include <QDir>
#include <QDockWidget>
#include <QFileDialog>
#include <QFileInfo>
#include <QFormLayout>
#include <QHBoxLayout>
#include <QInputDialog>
#include <QKeyEvent>
#include <QKeySequence>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QMouseEvent>
#include <QPushButton>
#include <QRadioButton>
#include <QSignalBlocker>
#include <QSpinBox>
#include <QStatusBar>
#include <QTabBar>
#include <QTabWidget>
#include <QVBoxLayout>

#include <algorithm>
#include <utility>

namespace ascend::workbench {
namespace {

// 文件夹内的模块包（`*.amod` 直接子文件，按名称排序；不含子文件夹）。
QStringList module_package_files_in(const QString& directory) {
    const QFileInfoList entries = QDir(directory).entryInfoList(QDir::Files | QDir::Readable, QDir::Name);
    QStringList paths;
    for (const QFileInfo& entry : entries) {
        if (entry.suffix().compare(QStringLiteral("amod"), Qt::CaseInsensitive) == 0) {
            paths << entry.absoluteFilePath();
        }
    }
    return paths;
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
    workspace_ = new WorkspaceModel(this);
    // 面板上下文：值来源 + 命令/对话框回调（面板之间不互相引用）。
    context_.texts = texts_.get();
    context_.workspace = workspace_;
    context_.adapters = adapters_.get();
    context_.int_adapter = int_adapter_;
    context_.new_research = [this] { startNewResearch(); };
    context_.open_research = [this] { startOpenResearch(); };
    context_.open_example = [this] { startOpenExample(); };
    context_.new_system = [this] { chooseNewSystem(); };
    context_.open_system = [this] { chooseOpenSystem(); };
    context_.save_system = [this] { saveSystem(); };
    context_.save_system_as = [this] { chooseSaveSystemAs(); };
    // 系统编辑器：面板从控件读出参数，外壳方法保持忙碌簿记与队列提交。
    context_.add_module = [this](const QString& definition, const QString& instance) {
        addSystemModule(definition, instance);
    };
    context_.remove_module = [this](const QString& instance) { removeSystemModule(instance); };
    context_.connect_requirement = [this](const QString& module, const QString& symbol,
                                          const QString& provider_module, const QString& provider_symbol) {
        connectSystemRequirement(module, symbol, provider_module, provider_symbol);
    };
    context_.disconnect_requirement = [this](const QString& module, const QString& symbol) {
        disconnectSystemRequirement(module, symbol);
    };
    context_.set_spec_advance = [this](const QString& module, const QString& symbol) {
        onSpecAdvanceChanged(module, symbol);
    };
    context_.set_spec_observations = [this](const QStringList& references) {
        onSpecObservationsChanged(references);
    };
    // 配置编辑：面板提交实例配置，外壳保持忙碌簿记与队列提交。
    context_.set_instance_config = [this](const QString& scope, const QString& name, const ascend::Config& config) {
        setInstanceConfig(scope, name, config);
    };
    context_.load_packages = [this] { chooseLoadModulePackage(); };
    context_.load_package_folder = [this] { chooseLoadModulePackageFolder(); };
    context_.unload_package = [this](const QString& definition) {
        if (state().busy || closing_) return;
        unloadModulePackage(definition);
    };
    context_.dispatch = [this](const char* method) { dispatch(method); };
    context_.dispatch_readonly = [this](const char* method) { dispatchReadonly(method); };
    context_.show_details = [this](const QString& html) { state_panel_->show_details_html(html); };
    context_.request_sample_detail = [this](int, std::int64_t frame) { requestSampleDetail(frame); };
    connect(workspace_, &WorkspaceModel::changed, this, &MainWindow::onWorkspaceChanged);
    setWindowTitle(ui_text(*texts_, "workbench.app.display_name", "Ascend Research Platform · Causal Modeling Workbench"));
    resize(1280, 820);
    buildLayout();
    buildDiagnosticsDock();
    // 中央页面板自持控件：开始页与模块库（可见性由状态层与注册表统一管理）。
    start_panel_ = new StartPagePanel(context_, results_tabs_);
    package_panel_ = new PackageLibraryPanel(context_, results_tabs_);
    package_panel_->hide();  // 模块库默认不打开（未加入页签前保持隐藏）
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
    connect(controller_, &SessionController::modulePackagesChanged, this, &MainWindow::onModulePackages);
    connect(controller_, &SessionController::modulePackagesLoaded, this, &MainWindow::onModulePackagesLoaded);
    connect(controller_, &SessionController::modulePackageUnloaded, this, &MainWindow::onModulePackageUnloaded);
    connect(controller_, &SessionController::systemOpened, this, &MainWindow::onSystemOpened);
    connect(controller_, &SessionController::systemSaved, this, &MainWindow::onSystemSaved);
    QMetaObject::invokeMethod(controller_, "requestModulePackages", Qt::QueuedConnection);
    QMetaObject::invokeMethod(controller_, "requestModel", Qt::QueuedConnection);

    for (const auto& error : texts_->load_errors) {
        bottom_panel_->append_resource_error(from_utf8(error));
    }

    // 开始页默认打开并置前（状态层记录打开集合并由注册表套用到页签与查看菜单动作）。
    showStartPage();
    updateControls();
    updateStatusLabels();
}

const WorkspaceState& MainWindow::state() const noexcept { return workspace_->state(); }

QString MainWindow::statusLine() const {
    QString text = phase_text(*texts_, state().status.phase);
    if (state().status.run_id.has_value()) {
        text += ui_text(*texts_, "workbench.status.run_suffix", " · run #%1 · source revision %2").arg(*state().status.run_id).arg(state().status.run_revision);
    }
    if (!state().status.tracks.empty()) {
        text += ui_text(*texts_, "workbench.status.frame_suffix", " · frame");
        if (state().status.tracks.size() == 1) {
            text += QStringLiteral(" %1").arg(state().status.tracks.front().frame);
        } else {
            for (const auto& track : state().status.tracks) {
                text += QStringLiteral(" %1 %2").arg(from_utf8(track.label)).arg(track.frame);
            }
        }
    }
    if (state().status.has_checkpoint) {
        text += ui_text(*texts_, "workbench.status.checkpoint_suffix", " · checkpoint %1").arg(state().status.checkpoint_frame);
    }
    return text;
}

void MainWindow::buildLayout() {
    // 中央为主工作区：命令栏 + 浏览器式结果页签（可拖动重排、可关闭、从查看菜单重开）；
    // 其余面板做成停靠窗口，可浮动、嵌套与合并。
    auto* central = new QWidget(this);
    auto* root = new QVBoxLayout(central);
    root->setContentsMargins(6, 6, 6, 6);
    root->setSpacing(6);
    buildTopBar(root);
    buildResultsPane();
    root->addWidget(results_tabs_, 1);
    setCentralWidget(central);

    // 停靠面板自持控件与渲染：模块管理器、配置与状态；选中帧仍在窗口侧。
    modules_panel_ = new ModulesPanel(context_, this);
    config_panel_ = new ConfigPanel(context_, this);
    state_panel_ = new StatePanel(context_, this);
    connect(state_panel_, &StatePanel::sample_series_changed, this, [this] {
        if (selected_frame_ >= 0) requestSampleDetail(selected_frame_);
        else requestLatestSample();
    });

    const auto make_dock = [this](const char* object, const char* title_key, const char* fallback,
                                  QWidget* content) {
        auto* dock = new QDockWidget(ui_text(*texts_, title_key, fallback), this);
        dock->setObjectName(object);
        dock->setWidget(content);
        return dock;
    };
    modules_dock_ = make_dock("modulesDock", "workbench.view.left_panel", "Module manager", modules_panel_);
    config_dock_ = make_dock("configDock", "workbench.view.config", "Configuration", config_panel_);
    state_dock_ = make_dock("stateDock", "workbench.view.right_panel", "State", state_panel_);

    setDockNestingEnabled(true);
    // 停靠面板可浮动、嵌套并排、标签化合并与浮动分组，能力保持完整。
    // 已知 Qt 回归：6.10.2～6.11.2 的停靠区标签栏释放后仍被布局裸指针引用
    // （QTBUG-143776 "tabifyDockWidget() crashes"，6.11.3 已修复），极端拖拽/
    // 标签化操作下可能崩溃；升级 Qt 后无需改码即消除。
    setDockOptions(dockOptions() | QMainWindow::GroupedDragging);
    addDockWidget(Qt::LeftDockWidgetArea, modules_dock_);
    addDockWidget(Qt::RightDockWidgetArea, state_dock_);
    addDockWidget(Qt::RightDockWidgetArea, config_dock_);
    tabifyDockWidget(state_dock_, config_dock_);  // 右侧：状态与配置同区标签化
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
        workspace_->applyBusy(true);
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
        if (state().model.state_fields.empty()) {
            QMessageBox::information(this, ui_text(*texts_, "workbench.branch.title_short", "Build branches"),
                                     ui_text(*texts_, "workbench.branch.need_checkpoint", "Run to a non-initial frame and create a checkpoint first."));
            return;
        }
        BranchDialog dialog(state().status.checkpoint_frame, state().model.state_fields, *int_adapter_, *texts_, this);
        if (dialog.exec() != QDialog::Accepted) return;
        std::vector<session::BranchRequest> branches;
        // 标签留空，由会话按其文本域给出“对照／干预”。
        branches.push_back(session::BranchRequest{{}, {}});
        branches.push_back(session::BranchRequest{{}, dialog.interventions()});
        workspace_->applyBusy(true);
        pending_ = QStringLiteral("branches");
        updateControls();
        QMetaObject::invokeMethod(controller_, "createBranches", Qt::QueuedConnection,
                                  Q_ARG(std::vector<ascend::session::BranchRequest>, branches));
    });
}

void MainWindow::buildResultsPane() {
    results_tabs_ = new QTabWidget;
    results_tabs_->setObjectName("resultsTabs");
    // 浏览器式页签：可拖动重排、带关闭按钮、扁平外观；至少保留一个页签。
    results_tabs_->setMovable(true);
    results_tabs_->setTabsClosable(true);
    results_tabs_->setDocumentMode(true);
    results_tabs_->setElideMode(Qt::ElideRight);
    results_tabs_->tabBar()->setExpanding(false);

    // 时间轴与逻辑帧差异页自持控件（默认不打开，出现运行/记录时由状态层规则加入页签）。
    timeline_panel_ = new TimelinePanel(context_, results_tabs_);
    diff_panel_ = new DiffPanel(context_, results_tabs_);
    // 系统编辑器页自持控件（默认不打开，编辑菜单与查看菜单打开；文件字段留在外壳）。
    system_panel_ = new SystemEditorPanel(context_, results_tabs_);

    connect(results_tabs_, &QTabWidget::currentChanged, this, [this](int index) {
        if (results_tabs_->widget(index) == diff_panel_) dispatchReadonly("requestComparison");
    });
    connect(results_tabs_, &QTabWidget::tabCloseRequested, this, [this](int index) {
        if (results_tabs_->count() <= 1) return;  // 浏览器式：保留最后一个视图
        QWidget* page = results_tabs_->widget(index);
        const PanelDescriptor* panel = panels_.findByPage(page);
        if (panel == nullptr) return;
        workspace_->noteCentralClosed(panel->id.toStdString());
        applyPanelState();
    });
}

void MainWindow::showSystemEditor() {
    if (system_panel_ == nullptr) return;
    system_panel_->render();
    showPanel(panel_id::editor);
}

void MainWindow::beginSystemCommand(const char* method, bool edits_draft) {
    workspace_->applyBusy(true);
    if (edits_draft) {
        // 编辑改变会话（研究文件与因果系统草稿都可能未保存）；打开/保存不在此列。
        workspace_->set_research_dirty(true);
        workspace_->set_system_dirty(true);
    }
    pending_ = QString::fromLatin1(method);
    updateControls();
}

void MainWindow::setInstanceConfig(const QString& scope, const QString& name, const ascend::Config& config) {
    // ConfigPanel 的编辑回调：忙碌守卫与忙碌簿记仍在外壳，值经队列提交给控制层。
    if (state().busy || closing_ || controller_ == nullptr) return;
    workspace_->applyBusy(true);
    workspace_->set_research_dirty(true);
    pending_ = QStringLiteral("config");
    updateControls();
    QMetaObject::invokeMethod(controller_, "setInstanceConfig", Qt::QueuedConnection, Q_ARG(QString, scope),
                              Q_ARG(QString, name), Q_ARG(ascend::Config, config));
}

void MainWindow::chooseNewSystem() {
    if (state().busy || closing_) return;
    bool accepted = false;
    const QString name = QInputDialog::getText(this, ui_text(*texts_, "workbench.editor.new_system", "New system…"),
                                               ui_text(*texts_, "workbench.editor.new_system_prompt", "System name:"),
                                               QLineEdit::Normal, QString(), &accepted);
    if (!accepted || name.trimmed().isEmpty()) return;
    workspace_->beginIdentity(IdentityIntent::new_research);
    beginSystemCommand("newSystem");
    QMetaObject::invokeMethod(controller_, "newSystem", Qt::QueuedConnection, Q_ARG(QString, name.trimmed()));
}

void MainWindow::addSystemModule(const QString& definition, const QString& instance) {
    if (state().busy || closing_) return;
    beginSystemCommand("addModule");
    QMetaObject::invokeMethod(controller_, "addModule", Qt::QueuedConnection, Q_ARG(QString, definition),
                              Q_ARG(QString, instance));
}

void MainWindow::removeSystemModule(const QString& instance) {
    if (state().busy || closing_) return;
    const auto answer = QMessageBox::question(
        this, ui_text(*texts_, "workbench.editor.remove", "Remove instance"),
        ui_text(*texts_, "workbench.editor.remove_confirm",
                "Remove instance \"%1\"? Connections referencing it are removed as well.").arg(instance));
    if (answer != QMessageBox::Yes) return;
    beginSystemCommand("removeModule");
    QMetaObject::invokeMethod(controller_, "removeModule", Qt::QueuedConnection, Q_ARG(QString, instance));
}

void MainWindow::connectSystemRequirement(const QString& module, const QString& symbol, const QString& provider_module,
                                          const QString& provider_symbol) {
    if (state().busy || closing_) return;
    beginSystemCommand("connectRequirement");
    QMetaObject::invokeMethod(controller_, "connectRequirement", Qt::QueuedConnection, Q_ARG(QString, module),
                              Q_ARG(QString, symbol), Q_ARG(QString, provider_module), Q_ARG(QString, provider_symbol));
}

void MainWindow::disconnectSystemRequirement(const QString& module, const QString& symbol) {
    if (state().busy || closing_) return;
    beginSystemCommand("disconnectRequirement");
    QMetaObject::invokeMethod(controller_, "disconnectRequirement", Qt::QueuedConnection, Q_ARG(QString, module),
                              Q_ARG(QString, symbol));
}

void MainWindow::onSpecAdvanceChanged(const QString& module, const QString& symbol) {
    beginSystemCommand("setSpecAdvance");
    QMetaObject::invokeMethod(controller_, "setSpecAdvance", Qt::QueuedConnection, Q_ARG(QString, module),
                              Q_ARG(QString, symbol));
}

void MainWindow::onSpecObservationsChanged(const QStringList& references) {
    beginSystemCommand("setSpecObservations");
    QMetaObject::invokeMethod(controller_, "setSpecObservations", Qt::QueuedConnection, Q_ARG(QStringList, references));
}

void MainWindow::showStartPage() {
    if (start_panel_ == nullptr) return;
    showPanel(panel_id::start);
}

void MainWindow::hideStartPage() {
    hidePanel(panel_id::start);
}

void MainWindow::startNewResearch() {
    if (state().busy || closing_) return;
    // 新建研究：研究名称 + 创建或打开因果系统。
    QDialog dialog(this);
    dialog.setObjectName("newResearchDialog");
    dialog.setWindowTitle(ui_text(*texts_, "workbench.research.title", "New research"));
    auto* layout = new QVBoxLayout(&dialog);
    auto* form = new QFormLayout();
    auto* name_edit = new QLineEdit(ui_text(*texts_, "workbench.research.default_name", "Untitled research"), &dialog);
    name_edit->setObjectName("researchNameEdit");
    form->addRow(ui_text(*texts_, "workbench.research.name", "Research name:"), name_edit);
    layout->addLayout(form);
    auto* create_radio =
        new QRadioButton(ui_text(*texts_, "workbench.research.create_system", "Create a new causal system"), &dialog);
    create_radio->setObjectName("researchCreateSystemRadio");
    create_radio->setChecked(true);
    layout->addWidget(create_radio);
    auto* open_radio = new QRadioButton(
        ui_text(*texts_, "workbench.research.open_system", "Open an existing causal system (.aasm)"), &dialog);
    open_radio->setObjectName("researchOpenSystemRadio");
    layout->addWidget(open_radio);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, &dialog);
    buttons->setObjectName("researchDialogButtons");
    connect(buttons, &QDialogButtonBox::accepted, &dialog, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, &dialog, &QDialog::reject);
    layout->addWidget(buttons);
    if (dialog.exec() != QDialog::Accepted) return;

    QString name = name_edit->text().trimmed();
    if (name.isEmpty()) name = ui_text(*texts_, "workbench.research.default_name", "Untitled research");
    if (open_radio->isChecked()) {
        const QString path = QFileDialog::getOpenFileName(
            this, ui_text(*texts_, "workbench.system.open_title", "Open causal system"), QDir::homePath(),
            ui_text(*texts_, "workbench.system.filter", "Ascend causal system (*.aasm);;All files (*)"), nullptr,
            QFileDialog::DontUseNativeDialog);
        if (path.isEmpty()) return;  // 取消选择：留在开始页
        hideStartPage();
        workspace_->set_research_dirty(true);
        beginSystemCommand("openSystem", false);
        QMetaObject::invokeMethod(controller_, "openSystem", Qt::QueuedConnection, Q_ARG(QString, path),
                                  Q_ARG(QString, name));
        return;
    }
    hideStartPage();
    workspace_->beginIdentity(IdentityIntent::new_research);
    beginSystemCommand("newSystem");
    QMetaObject::invokeMethod(controller_, "newSystem", Qt::QueuedConnection, Q_ARG(QString, name));
    showSystemEditor();
}

void MainWindow::startOpenResearch() {
    if (state().busy || closing_) return;
    hideStartPage();
    chooseOpenExperiment();
}

void MainWindow::startOpenExample() {
    if (state().busy || closing_) return;
    hideStartPage();
    workspace_->beginIdentity(IdentityIntent::open_example);
    dispatch("load");
}

void MainWindow::chooseOpenSystem() {
    if (state().busy || closing_) return;
    const QString system_file = from_utf8(state().system_file);
    const QString path = QFileDialog::getOpenFileName(
        this, ui_text(*texts_, "workbench.system.open_title", "Open causal system"),
        system_file.isEmpty() ? QDir::homePath() : QFileInfo(system_file).absolutePath(),
        ui_text(*texts_, "workbench.system.filter", "Ascend causal system (*.aasm);;All files (*)"), nullptr,
        QFileDialog::DontUseNativeDialog);
    if (path.isEmpty()) return;
    workspace_->set_research_dirty(true);
    beginSystemCommand("openSystem", false);
    QMetaObject::invokeMethod(controller_, "openSystem", Qt::QueuedConnection, Q_ARG(QString, path),
                              Q_ARG(QString, QFileInfo(path).completeBaseName()));
}

void MainWindow::saveSystem() {
    if (state().busy || closing_) return;
    const QString system_file = from_utf8(state().system_file);
    if (system_file.isEmpty()) {
        chooseSaveSystemAs();
        return;
    }
    beginSystemCommand("saveSystem", false);
    QMetaObject::invokeMethod(controller_, "saveSystem", Qt::QueuedConnection, Q_ARG(QString, system_file));
}

void MainWindow::chooseSaveSystemAs() {
    if (state().busy || closing_) return;
    const QString system_file = from_utf8(state().system_file);
    QString suggested = system_file.isEmpty() ? from_utf8(state().status.model_name) + QStringLiteral(".aasm")
                                              : system_file;
    if (suggested.isEmpty() || suggested == QStringLiteral(".aasm")) suggested = QStringLiteral("system.aasm");
    const QString path = QFileDialog::getSaveFileName(
        this, ui_text(*texts_, "workbench.system.save_title", "Save causal system"),
        QDir::homePath() + QLatin1Char('/') + suggested,
        ui_text(*texts_, "workbench.system.filter", "Ascend causal system (*.aasm);;All files (*)"), nullptr,
        QFileDialog::DontUseNativeDialog);
    if (path.isEmpty()) return;
    const QString target = path.endsWith(QStringLiteral(".aasm")) ? path : path + QStringLiteral(".aasm");
    beginSystemCommand("saveSystem", false);
    QMetaObject::invokeMethod(controller_, "saveSystem", Qt::QueuedConnection, Q_ARG(QString, target));
}

void MainWindow::onSystemOpened(const QString& path, bool ok, const QString& detail) {
    if (!ok) {
        QString text = ui_text(*texts_, "workbench.system.open_failed", "Could not open the causal system: %1").arg(path);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.system.open_title", "Open causal system"), text);
        return;
    }
    workspace_->beginIdentity(IdentityIntent::open_system, path.toStdString());
    showSystemEditor();
}

void MainWindow::onSystemSaved(const QString& path, bool ok, const QString& detail) {
    if (!ok) {
        QString text = ui_text(*texts_, "workbench.system.save_failed", "Could not save the causal system: %1").arg(path);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.system.save_title", "Save causal system"), text);
        return;
    }
    workspace_->note_system_saved(path.toStdString());
    system_panel_->render();
}

void MainWindow::clearIdentityViews() {
    // 身份切换：清掉与上一身份绑定的瞬时视图（检查报告、采样详情与波形状态）。
    state_panel_->clear_transient();
    selected_frame_ = -1;
    timeline_panel_->clear();
    diff_panel_->clear();
}

void MainWindow::buildDiagnosticsDock() {
    auto* dock = new QDockWidget(ui_text(*texts_, "workbench.pane.diagnostics", "Diagnostics and records"), this);
    dock->setObjectName("diagnosticsDock");
    diagnostics_dock_ = dock;
    bottom_panel_ = new DiagnosticsPanel(*texts_, dock);
    connect(bottom_panel_, &DiagnosticsPanel::record_requested, this, [this] { dispatchReadonly("requestRecord"); });
    dock->setWidget(bottom_panel_);
    addDockWidget(Qt::BottomDockWidgetArea, dock);
    dock->setMinimumHeight(180);

    status_label_ = new QLabel(ui_text(*texts_, "workbench.status.empty", "Empty session"), this);
    status_label_->setObjectName("statusLabel");
    revision_label_ = new QLabel(this);
    revision_label_->setObjectName("revisionLabel");
    statusBar()->addWidget(status_label_, 1);
    statusBar()->addPermanentWidget(revision_label_);
}

void MainWindow::updateControls() {
    if (workspace_ == nullptr) return;
    const ControlsView controls = workspace_->controls();
    check_button_->setEnabled(controls.editable);
    apply_button_->setEnabled(controls.editable);
    checkpoint_button_->setEnabled(controls.can_checkpoint);
    branch_button_->setEnabled(controls.can_branch);
    reset_branches_button_->setEnabled(controls.can_reset_branches);
    replay_button_->setEnabled(controls.can_replay);
    step_button_->setEnabled(controls.runnable);
    run_button_->setEnabled(controls.runnable);
    steps_spin_->setEnabled(controls.runnable);
    stop_button_->setEnabled(state().busy && pending_ == QStringLiteral("run"));
    if (config_panel_ != nullptr) config_panel_->set_editable(controls.editable);
    for (const auto& [action, button] : action_buttons_) action->setEnabled(button->isEnabled());
    if (open_action_ != nullptr) open_action_->setEnabled(controls.can_open_file && !closing_);
    if (open_experiment_action_ != nullptr) open_experiment_action_->setEnabled(controls.can_open_file && !closing_);
    if (save_action_ != nullptr) save_action_->setEnabled(controls.can_save_experiment && !closing_);
    if (save_as_action_ != nullptr) save_as_action_->setEnabled(controls.can_save_experiment && !closing_);
    if (load_module_action_ != nullptr) load_module_action_->setEnabled(controls.can_manage_packages && !closing_);
    if (load_module_folder_action_ != nullptr) {
        load_module_folder_action_->setEnabled(controls.can_manage_packages && !closing_);
    }
    if (unload_module_action_ != nullptr) unload_module_action_->setEnabled(controls.can_unload_package && !closing_);
    if (package_panel_ != nullptr) {
        package_panel_->set_load_enabled(controls.can_manage_packages && !closing_);
        package_panel_->set_unload_enabled(controls.can_unload_package && !closing_);
    }
    if (new_research_action_ != nullptr) new_research_action_->setEnabled(controls.can_open_file && !closing_);
    if (start_panel_ != nullptr) start_panel_->set_open_enabled(controls.can_open_file && !closing_);
    system_panel_->update_controls(closing_);
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
    new_research_action_ = file->addAction(ui_text(*texts_, "workbench.action.new_research", "New research…"));
    new_research_action_->setObjectName("newResearchAction");
    connect(new_research_action_, &QAction::triggered, this, &MainWindow::startNewResearch);
    file->addSeparator();
    open_action_ = file->addAction(ui_text(*texts_, "workbench.action.open_example", "Open example"));
    open_action_->setObjectName("openExampleAction");
    connect(open_action_, &QAction::triggered, this, [this] {
        workspace_->beginIdentity(IdentityIntent::open_example);
        dispatch("load");
    });
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
    load_module_action_ = file->addAction(ui_text(*texts_, "workbench.action.load_module_package", "Load module package…"));
    load_module_action_->setObjectName("loadModulePackageAction");
    connect(load_module_action_, &QAction::triggered, this, &MainWindow::chooseLoadModulePackage);
    load_module_folder_action_ = file->addAction(
        ui_text(*texts_, "workbench.action.load_module_package_folder", "Load module package folder…"));
    load_module_folder_action_->setObjectName("loadModulePackageFolderAction");
    connect(load_module_folder_action_, &QAction::triggered, this, &MainWindow::chooseLoadModulePackageFolder);
    unload_module_action_ = file->addAction(ui_text(*texts_, "workbench.action.unload_module_package", "Unload module package…"));
    unload_module_action_->setObjectName("unloadModulePackageAction");
    connect(unload_module_action_, &QAction::triggered, this, &MainWindow::chooseUnloadModulePackage);
    file->addSeparator();
    QAction* quit = file->addAction(ui_text(*texts_, "workbench.action.quit", "Quit"));
    connect(quit, &QAction::triggered, this, &QWidget::close);

    QMenu* edit = bar->addMenu(ui_text(*texts_, "workbench.menu.edit", "Edit"));
    edit->setToolTipsVisible(true);
    placeholder(edit, "workbench.action.undo", "Undo");
    placeholder(edit, "workbench.action.redo", "Redo");
    edit->addSeparator();
    QAction* editor_action = edit->addAction(ui_text(*texts_, "workbench.action.system_editor", "System editor…"));
    editor_action->setObjectName("systemEditorAction");
    connect(editor_action, &QAction::triggered, this, &MainWindow::showSystemEditor);

    auto* view = new StayOpenMenu(ui_text(*texts_, "workbench.menu.view", "View"), this);
    view->setToolTipsVisible(true);
    bar->addMenu(view);
    // 查看菜单：面板注册表只声明一次，菜单项、页签勾选与停靠开关由此生成（WB-19）。
    const auto already_open = [this](QWidget* page) { return results_tabs_->indexOf(page) >= 0; };
    panels_.addCentral(panel_id::start, start_panel_, ui_text(*texts_, "workbench.pane.start", "Start"),
                       "startViewAction", already_open(start_panel_), this);
    panels_.addDock("modules", modules_dock_, ui_text(*texts_, "workbench.view.left_panel", "Module manager"));
    panels_.addCentral(panel_id::package_library, package_panel_,
                       ui_text(*texts_, "workbench.view.package_library", "Module library"), "packageLibraryAction",
                       already_open(package_panel_), this);
    panels_.addCentral(panel_id::editor, system_panel_,
                       ui_text(*texts_, "workbench.pane.system_editor", "System editor"), "systemEditorViewAction",
                       already_open(system_panel_), this);
    panels_.addDock("config", config_dock_, ui_text(*texts_, "workbench.view.config", "Configuration"));
    panels_.addCentral(panel_id::timeline, timeline_panel_, ui_text(*texts_, "workbench.pane.timeline", "Timeline"),
                       "timelineViewAction", already_open(timeline_panel_), this);
    panels_.addCentral(panel_id::differences, diff_panel_,
                       ui_text(*texts_, "workbench.pane.differences", "Frame differences"), "differencesViewAction",
                       already_open(diff_panel_), this);
    panels_.addDock("state", state_dock_, ui_text(*texts_, "workbench.view.right_panel", "State"));
    panels_.addDock("diagnostics", diagnostics_dock_,
                    ui_text(*texts_, "workbench.view.diagnostics", "Bottom panel (diagnostics and records)"));
    for (const auto& panel : panels_.panels()) view->addAction(panel.action);
    for (const auto& panel : panels_.panels()) {
        if (!panel.central) continue;
        const QString id = panel.id;
        connect(panel.action, &QAction::toggled, this, [this, id](bool visible) {
            if (applying_panels_) return;
            if (visible) workspace_->noteCentralOpened(id.toStdString());
            else workspace_->noteCentralClosed(id.toStdString());
            applyPanelState();
        });
    }
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
    updateWindowTitle();
    status_label_->setText(statusLine());
    if (state().status.phase == session::Phase::empty) {
        revision_label_->clear();  // 空会话没有草稿修订可言
        return;
    }
    QString revision = ui_text(*texts_, "workbench.status.draft_revision", "Draft revision %1").arg(state().status.draft_revision);
    if (state().status.dirty) {
        revision += ui_text(*texts_, "workbench.status.dirty", "(not applied; the run comes from revision %1)").arg(state().status.run_revision);
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
    workspace_->applyBusy(false);
    pending_.clear();
    event->accept();
}

void MainWindow::dispatch(const char* method) {
    if (closing_ || controller_ == nullptr) return;
    workspace_->applyBusy(true);
    workspace_->set_research_dirty(true);
    pending_ = QString::fromLatin1(method);
    updateControls();
    QMetaObject::invokeMethod(controller_, method, Qt::QueuedConnection);
}

void MainWindow::dispatchReadonly(const char* method) {
    if (closing_ || controller_ == nullptr) return;
    // 只读快照请求不占用忙碌标记，运行命令进行中仍可请求并保持停止可用。
    QMetaObject::invokeMethod(controller_, method, Qt::QueuedConnection);
}

void MainWindow::onBusyChanged(bool busy) {
    if (!busy) pending_.clear();
    workspace_->applyBusy(busy);
}

void MainWindow::onStatusChanged(const session::Status& status) {
    workspace_->applyStatus(status);
}

void MainWindow::onModelChanged(const ModelSnapshot& model) {
    workspace_->applyModel(model);
}

void MainWindow::onTracesReset(const std::vector<session::TrackTraceView>& traces) {
    workspace_->applyTracesReset(traces);
}

void MainWindow::onWorkspaceChanged(WorkspaceChange changed) {
    if (has(changed, WorkspaceChange::identity)) {
        clearIdentityViews();
    }
    if (has(changed, WorkspaceChange::catalog) || has(changed, WorkspaceChange::assembly) ||
        has(changed, WorkspaceChange::spec)) {
        config_panel_->render_instances(state().model.instances);
        modules_panel_->render();
        state_panel_->render_state_fields();
        state_panel_->render_sample_selectors(state().model.series_labels);
        if (results_tabs_->indexOf(system_panel_) >= 0) system_panel_->render();
        updateControls();
        updateStatusLabels();
    }
    if (has(changed, WorkspaceChange::run)) {
        updateStatusLabels();
        updateControls();
        timeline_panel_->render_waveform();
    }
    if (has(changed, WorkspaceChange::results)) {
        timeline_panel_->render_waveform();
        state_panel_->render_sample_selectors(state().model.series_labels);
        diff_panel_->clear();
        requestLatestSample();
    }
    if (has(changed, WorkspaceChange::samples)) {
        timeline_panel_->render_waveform();
        requestLatestSample();
    }
    if (has(changed, WorkspaceChange::comparison)) {
        diff_panel_->render_comparison();
        timeline_panel_->render_waveform();
    }
    if (has(changed, WorkspaceChange::observations)) {
        timeline_panel_->render_waveform();
    }
    if (has(changed, WorkspaceChange::check)) {
        bottom_panel_->render_check(state().check, state().has_check);
        if (state().has_check) bottom_panel_->show_check_tab();  // 检查完成切到检查页
    }
    if (has(changed, WorkspaceChange::diagnostics)) {
        bottom_panel_->render_diagnostics(state().diagnostics);
    }
    if (has(changed, WorkspaceChange::packages)) {
        if (package_panel_ != nullptr) package_panel_->render(state().packages);
    }
    if (has(changed, WorkspaceChange::record)) {
        updateRecordSummary();
    }
    if (has(changed, WorkspaceChange::panels)) {
        applyPanelState();
    }
    if (has(changed, WorkspaceChange::controls)) {
        updateControls();
    }
}

void MainWindow::onSamplesAppended(int series, const std::vector<session::SampleView>& samples,
                                   const std::vector<session::StepEvent>& events) {
    workspace_->applySamplesAppended(series, samples, events);
}

void MainWindow::onComparisonReady(const session::ComparisonView& comparison) {
    workspace_->applyComparison(comparison);
}

void MainWindow::onCheckFinished(const session::CheckReport& report) {
    workspace_->applyCheck(report);
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
    if (state().busy || closing_) return;
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
    if (state().busy || closing_) return;
    const QString research_file = from_utf8(state().research_file);
    if (research_file.isEmpty()) {
        chooseSaveExperiment();
        return;
    }
    pending_ = QStringLiteral("saveExperiment");
    updateControls();
    QMetaObject::invokeMethod(controller_, "saveExperiment", Qt::QueuedConnection, Q_ARG(QString, research_file));
}

void MainWindow::chooseSaveExperiment() {
    if (state().busy || closing_) return;
    QString path = QFileDialog::getSaveFileName(
        this, ui_text(*texts_, "workbench.action.save_experiment_as", "Save experiment as…"),
        state().research_file.empty() ? QStringLiteral("experiment.aexp") : from_utf8(state().research_file),
        ui_text(*texts_, "workbench.filter.experiment", "Ascend experiment (*.aexp);;All files (*)"),
        nullptr, QFileDialog::DontUseNativeDialog);
    if (path.isEmpty()) return;
    if (!path.endsWith(QStringLiteral(".aexp"))) path += QStringLiteral(".aexp");
    pending_ = QStringLiteral("saveExperiment");
    updateControls();
    QMetaObject::invokeMethod(controller_, "saveExperiment", Qt::QueuedConnection, Q_ARG(QString, path));
}

void MainWindow::onExperimentSaved(const QString& path, bool ok, const QString& detail) {
    if (!ok) {
        QString text = ui_text(*texts_, "workbench.save.failed", "Could not save the experiment file: %1").arg(path);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.save.title", "Save experiment"), text);
        return;
    }
    workspace_->note_research_saved(path.toStdString());
    updateWindowTitle();
    updateRecordSummary();
}

void MainWindow::chooseLoadModulePackage() {
    if (state().busy || closing_) return;
    // 多选：一次选择多个模块包；每个包独立载入，结果汇总提示。
    const QStringList paths = QFileDialog::getOpenFileNames(
        this, ui_text(*texts_, "workbench.module.load_title", "Load module package"),
        state().research_file.empty() ? QString() : QFileInfo(from_utf8(state().research_file)).absolutePath(),
        ui_text(*texts_, "workbench.module.filter", "Ascend module package (*.amod);;All files (*)"),
        nullptr, QFileDialog::DontUseNativeDialog);
    if (paths.isEmpty()) return;
    loadModulePackages(paths);
}

void MainWindow::chooseLoadModulePackageFolder() {
    if (state().busy || closing_) return;
    // 选择文件夹：载入其中的全部模块包（`*.amod` 直接子文件；不含子文件夹）。
    const QString directory = QFileDialog::getExistingDirectory(
        this, ui_text(*texts_, "workbench.module.folder_title", "Load module package folder"),
        state().research_file.empty() ? QString() : QFileInfo(from_utf8(state().research_file)).absolutePath(),
        QFileDialog::DontUseNativeDialog);
    if (directory.isEmpty()) return;
    const QStringList paths = module_package_files_in(directory);
    if (paths.isEmpty()) {
        QMessageBox::information(this, ui_text(*texts_, "workbench.module.folder_title", "Load module package folder"),
                                 ui_text(*texts_, "workbench.module.folder_empty",
                                         "The selected folder contains no module packages (*.amod)."));
        return;
    }
    loadModulePackages(paths);
}

void MainWindow::loadModulePackages(const QStringList& paths) {
    if (paths.isEmpty()) return;
    pending_ = QStringLiteral("loadModulePackages");
    updateControls();
    QMetaObject::invokeMethod(controller_, "loadModulePackages", Qt::QueuedConnection, Q_ARG(QStringList, paths));
}

void MainWindow::chooseUnloadModulePackage() {
    if (state().busy || closing_) return;
    if (state().packages.empty()) {
        QMessageBox::information(this, ui_text(*texts_, "workbench.module.unload_title", "Unload module package"),
                                 ui_text(*texts_, "workbench.module.none", "No module package is loaded."));
        return;
    }
    QStringList items;
    items.reserve(static_cast<int>(state().packages.size()));
    for (const auto& package : state().packages) items << from_utf8(package.definition);
    bool accepted = false;
    const QString definition = QInputDialog::getItem(
        this, ui_text(*texts_, "workbench.module.unload_title", "Unload module package"),
        ui_text(*texts_, "workbench.module.unload_pick", "Select a module package to unload"),
        items, 0, false, &accepted);
    if (!accepted || definition.isEmpty()) return;
    unloadModulePackage(definition);
}

void MainWindow::unloadModulePackage(const QString& definition) {
    pending_ = QStringLiteral("unloadModulePackage");
    updateControls();
    QMetaObject::invokeMethod(controller_, "unloadModulePackage", Qt::QueuedConnection, Q_ARG(QString, definition));
}

void MainWindow::onModulePackages(const std::vector<session::ModulePackageView>& packages) {
    // 已载入模块包列表由状态层持有；表格由 packages 变更经唯一渲染入口重建。
    workspace_->applyPackages(packages);
}

void MainWindow::hidePackageLibrary() {
    hidePanel(panel_id::package_library);
}

void MainWindow::showPackageLibrary() {
    if (package_panel_ == nullptr) return;
    // 载入成功：打开模块库页签并切换过去；已打开时只切换（浏览器式页签可手动关闭）。
    showPanel(panel_id::package_library);
}

void MainWindow::applyPanelState() {
    if (applying_panels_ || results_tabs_ == nullptr) return;
    applying_panels_ = true;
    const PanelState& panels_state = state().panels;
    for (const auto& panel : panels_.panels()) {
        if (!panel.central || panel.page == nullptr) continue;
        const bool should_open = panel_open(panels_state, panel.id.toUtf8().constData());
        const int index = results_tabs_->indexOf(panel.page);
        if (should_open && index < 0) {
            results_tabs_->addTab(panel.page, panel.action != nullptr ? panel.action->text() : QString());
        } else if (!should_open && index >= 0) {
            results_tabs_->removeTab(index);
        }
        if (panel.action != nullptr) {
            QSignalBlocker blocker(panel.action);
            panel.action->setChecked(should_open);
        }
    }
    if (!panels_state.central_focus.empty()) {
        const PanelDescriptor* panel = panels_.find(from_utf8(panels_state.central_focus));
        if (panel != nullptr && panel->page != nullptr) results_tabs_->setCurrentWidget(panel->page);
        workspace_->clearCentralFocus();
    }
    applying_panels_ = false;
}

void MainWindow::showPanel(const char* id) {
    workspace_->noteCentralOpened(id);
    applyPanelState();
}

void MainWindow::hidePanel(const char* id) {
    workspace_->noteCentralClosed(id);
    applyPanelState();
}

void MainWindow::onModulePackagesLoaded(int loaded, int failed, const QString& detail) {
    if (loaded > 0) showPackageLibrary();
    if (failed <= 0) return;
    QString text = ui_text(*texts_, "workbench.module.load_failed_many",
                           "Could not load the following module packages:");
    if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
    QMessageBox::warning(this, ui_text(*texts_, "workbench.module.load_title", "Load module package"), text);
}

void MainWindow::onModulePackageUnloaded(const QString& definition, bool ok, const QString& detail) {
    if (!ok) {
        QString text = ui_text(*texts_, "workbench.module.unload_failed", "Could not unload the module package: %1").arg(definition);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.module.unload_title", "Unload module package"), text);
    }
}

void MainWindow::onExperimentOpened(const QString& path, bool ok, const QString& detail) {
    if (!ok) {
        QString text = ui_text(*texts_, "workbench.open.failed", "Could not open the experiment file: %1").arg(path);
        if (!detail.isEmpty()) text += QStringLiteral("\n") + detail;
        QMessageBox::warning(this, ui_text(*texts_, "workbench.open.title", "Open experiment"), text);
        return;
    }
    workspace_->beginIdentity(IdentityIntent::open_experiment, path.toStdString());
    updateWindowTitle();
    updateRecordSummary();
}

void MainWindow::updateWindowTitle() {
    QString title = ui_text(*texts_, "workbench.app.display_name", "Ascend Causal Modeling Workbench");
    if (!state().status.model_name.empty()) {
        title = ui_text(*texts_, "workbench.app.window_title", "Ascend Causal Modeling Workbench: %1")
                    .arg(from_utf8(state().status.model_name));
    }
    const QString research_file = from_utf8(state().research_file);
    if (!research_file.isEmpty()) {
        title += ui_text(*texts_, "workbench.app.file_suffix", " — %1")
                     .arg(QFileInfo(research_file).fileName());
        if (state().research_dirty) title += QStringLiteral("*");
    }
    setWindowTitle(title);
}

void MainWindow::updateRecordSummary() {
    if (bottom_panel_ == nullptr) return;
    bottom_panel_->render_record(state().record, from_utf8(state().research_file));
}

void MainWindow::onRecordChanged(const session::RecordView& record) {
    workspace_->applyRecord(record);
}

void MainWindow::onDiagnostics(const std::vector<session::DiagnosticView>& diagnostics) {
    workspace_->appendDiagnostics(diagnostics);
}

void MainWindow::onSampleDetailReady(int series, std::int64_t frame, const session::SampleDetailView& detail) {
    // 序列／帧匹配仍在外壳（选中帧由窗口持有）；渲染主体在状态面板。
    if (series != selected_series_ || frame != selected_frame_) return;
    state_panel_->render_sample_detail(detail, frame);
}

void MainWindow::requestSampleDetail(std::int64_t frame) {
    if (closing_ || controller_ == nullptr || state_panel_ == nullptr || state().series.empty()) return;
    selected_frame_ = frame;
    selected_series_ = std::clamp(state_panel_->selected_series(), 0,
                                  static_cast<int>(state().series.size()) - 1);
    QMetaObject::invokeMethod(controller_, "requestSampleDetail", Qt::QueuedConnection,
                              Q_ARG(int, selected_series_), Q_ARG(std::int64_t, selected_frame_));
}

void MainWindow::requestLatestSample() {
    if (state().series.empty() || state_panel_ == nullptr) return;
    const int series_index = std::clamp(state_panel_->selected_series(), 0,
                                        static_cast<int>(state().series.size()) - 1);
    const auto& samples = state().series[static_cast<std::size_t>(series_index)].samples;
    if (samples.empty()) return;
    requestSampleDetail(samples.back().frame);
}

}  // namespace ascend::workbench
