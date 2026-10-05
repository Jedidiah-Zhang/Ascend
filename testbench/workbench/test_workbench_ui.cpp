#include "config_editor.hpp"
#include <ascend/example/experiment_model.hpp>
#include <ascend/module_package.hpp>
#include "main_window.hpp"
#include "waveform_widget.hpp"
#include "workbench.hpp"

#include <QAction>
#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QCoreApplication>
#include <QDialog>
#include <QDir>
#include <QFileDialog>
#include <QDockWidget>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QFileSystemModel>
#include <QGroupBox>
#include <QInputDialog>
#include <QItemSelectionModel>
#include <QScrollArea>
#include <QScrollBar>
#include <QLabel>
#include <QLineEdit>
#include <QListView>
#include <QListWidget>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QPushButton>
#include <QRadioButton>
#include <QSpinBox>
#include <QSet>
#include <QTabBar>
#include <QTabWidget>
#include <QTableWidget>
#include <QTest>
#include <QTimer>
#include <QTreeWidget>
#include <QtTest>

#include <functional>
#include <limits>
#include <memory>
#include <utility>

using namespace ascend::workbench;

namespace {

bool wait_until(const std::function<bool()>& condition, int timeout_ms = 8000) {
    QElapsedTimer timer;
    timer.start();
    while (!condition() && timer.elapsed() < timeout_ms) QTest::qWait(5);
    return condition();
}

bool wait_idle(MainWindow* window, int timeout_ms = 8000) {
    return wait_until([window] { return !window->busy(); }, timeout_ms);
}

// 经「文件 → 打开示例」加载内置示例并等待会话离开空状态。
bool open_example(MainWindow* window) {
    auto* action = window->findChild<QAction*>("openExampleAction");
    if (action == nullptr) return false;
    action->trigger();
    return wait_until([window] {
        return window->currentStatus().phase != ascend::session::Phase::empty;
    });
}

// 启动工作台；默认经文件菜单打开内置示例，测试空白状态时传 false。
std::unique_ptr<Workbench> start_workbench(bool with_example = true) {
    auto workbench =
        std::make_unique<Workbench>(example_template(default_resource_paths()));
    workbench->start();
    if (with_example) open_example(workbench->window());
    return workbench;
}

// 生成一个示例模块包文件（默认无状态值模块 library.source），返回路径。
QString write_example_package(const QString& path,
                              const std::string& definition = ascend::example::library_source_definition) {
    ascend::Engine engine;
    ascend::ModulePackage package;
    if (definition == ascend::example::library_accumulator_definition) {
        engine.add(ascend::example::module_library_accumulator("probe", {}));
        package.manifest = ascend::export_module_manifest(engine, "", "probe");
        package.manifest.implementation = ascend::example::library_accumulator_implementation;
    } else {
        engine.add(ascend::example::module_library_source("probe", {}));
        package.manifest = ascend::export_module_manifest(engine, "", "probe");
        package.manifest.implementation = ascend::example::library_source_implementation;
    }
    package.manifest.definition = definition;
    package.manifest.version = "1.0";
    const auto bytes = ascend::encode_module_package(package);
    QFile file(path);
    file.open(QIODevice::WriteOnly);
    file.write(bytes.data(), static_cast<qint64>(bytes.size()));
    return path;
}

void drive_file_dialog(const QString& path) {
    QWidget* modal = QApplication::activeModalWidget();
    if (auto* box = qobject_cast<QMessageBox*>(modal)) {
        if (auto* yes = box->button(QMessageBox::Yes)) {
            yes->click();
        } else {
            box->accept();
        }
        return;
    }
    auto* dialog = qobject_cast<QFileDialog*>(modal);
    if (dialog == nullptr) return;
    dialog->setDirectory(QFileInfo(path).absolutePath());
    dialog->setFocus();  // 让文件名输入框失焦：Qt 在输入框有焦点时不接受 selectFile 的写入
    dialog->selectFile(path);
    if (dialog->selectedFiles().value(0) != path) return;  // 模型未就绪，下个周期重试
    QMetaObject::invokeMethod(dialog, "accept", Qt::DirectConnection);
}

// 驱动多选文件或文件夹对话框：文件在列表视图中选中、文件夹直接定位；条件满足后接受。
// 顺带关掉确认框，避免用例挂起。
void drive_choose_paths_dialog(const QStringList& paths) {
    QWidget* modal = QApplication::activeModalWidget();
    if (auto* box = qobject_cast<QMessageBox*>(modal)) {
        if (auto* yes = box->button(QMessageBox::Yes)) {
            yes->click();
        } else {
            box->accept();
        }
        return;
    }
    auto* dialog = qobject_cast<QFileDialog*>(modal);
    if (dialog == nullptr || paths.isEmpty()) return;
    const QString first = QFileInfo(paths.front()).absoluteFilePath();
    if (QFileInfo(first).isDir()) {
        dialog->setDirectory(first);
        if (dialog->selectedFiles().value(0) != first) return;  // 模型未就绪，下个周期重试
        QMetaObject::invokeMethod(dialog, "accept", Qt::DirectConnection);
        return;
    }
    dialog->setDirectory(QFileInfo(first).absolutePath());
    dialog->setFocus();
    auto* view = dialog->findChild<QListView*>("listView");
    if (view == nullptr || view->model() == nullptr || view->selectionModel() == nullptr) return;
    QSet<QString> wanted;
    for (const QString& path : paths) wanted.insert(QFileInfo(path).absoluteFilePath());
    view->selectionModel()->clearSelection();
    int matched = 0;
    const QModelIndex root = view->rootIndex();  // 文件视图的当前目录；行是它的子项
    for (int row = 0; row < view->model()->rowCount(root); ++row) {
        const QModelIndex index = view->model()->index(row, 0, root);
        if (wanted.contains(index.data(QFileSystemModel::FilePathRole).toString())) {
            view->selectionModel()->select(index, QItemSelectionModel::Select | QItemSelectionModel::Rows);
            ++matched;
        }
    }
    if (matched != wanted.size()) return;  // 列表尚未加载出全部目标，下个周期重试
    QMetaObject::invokeMethod(dialog, "accept", Qt::DirectConnection);
}

}  // namespace

class TestWorkbench : public QObject {
    Q_OBJECT

private slots:
    void windowLoadsExample();
    void stepAndVariableSwitch();
    void stopDuringRun();
    void runNThroughput();
    void branchComparisonAndReplay();
    void failedStateControls();
    void closeWhileRunning();
    void abandonedWorkbench();
    void uiTextFallback();
    void resourceResolution();
    void menuBarStructure();
    void captureScreenshot();
    void fileSaveOpen();
    void fileOpenFailure();
    void modulePackageLoadUnload();
    void modulePackageMultiLoad();
    void modulePackageFolderLoad();
    void systemEditorAssembly();
    void startPageResearch();
    void dockTitleDoubleClick();
    void dockFloatDockStress();
};

void TestWorkbench::windowLoadsExample() {
    // 新窗口从空白打开：空会话、模块树为空、推进不可用。
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::empty);
    QVERIFY(!window->findChild<QPushButton*>("stepButton")->isEnabled());
    auto* blank_tree = window->findChild<QTreeWidget*>("moduleTree");
    QVERIFY(blank_tree != nullptr);
    QCOMPARE(blank_tree->topLevelItemCount(), 0);

    // 文件 → 打开示例：加载内置确定性三变量示例。
    QVERIFY(open_example(window));
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::runnable);
    QVERIFY(window->statusLine().contains(QStringLiteral("可运行")));
    // 窗口标题展示应用名与模型名；窗口内不再重复标题行。
    QCOMPARE(window->windowTitle(), QStringLiteral("Ascend 因果建模工作台：确定性三变量"));

    // 模块树只保留结构与公开符号：符号挂在声明模块下，方法在前（斜体、带括号）；
    // 需求与连接不在树中。
    auto* tree = window->findChild<QTreeWidget*>("moduleTree");
    QVERIFY(tree != nullptr);
    QVERIFY(tree->topLevelItemCount() >= 1);
    auto* root = tree->topLevelItem(0);
    QVERIFY(root != nullptr);
    const auto find_item = [](QTreeWidgetItem* parent, const QString& text) -> QTreeWidgetItem* {
        for (int index = 0; index < parent->childCount(); ++index) {
            if (parent->child(index)->text(0) == text) return parent->child(index);
        }
        return nullptr;
    };
    auto* input_node = find_item(root, QStringLiteral("input"));
    auto* plant_node = find_item(root, QStringLiteral("plant"));
    QVERIFY(input_node != nullptr && plant_node != nullptr);
    QCOMPARE(input_node->child(0)->text(0), QStringLiteral("drive()"));
    QVERIFY(input_node->child(0)->font(0).italic());
    QCOMPARE(input_node->child(1)->text(0), QStringLiteral("value"));
    QVERIFY(!input_node->child(1)->font(0).italic());
    QCOMPARE(plant_node->child(0)->text(0), QStringLiteral("advance()"));
    QVERIFY(plant_node->child(0)->font(0).italic());
    auto* state_node = find_item(plant_node, QStringLiteral("state"));
    auto* update_node = find_item(plant_node, QStringLiteral("update"));
    QVERIFY(state_node != nullptr && update_node != nullptr);
    QCOMPARE(state_node->child(0)->text(0), QStringLiteral("write()"));
    QVERIFY(state_node->child(0)->font(0).italic());
    QCOMPARE(update_node->child(0)->text(0), QStringLiteral("advance()"));
    QVERIFY(update_node->child(0)->font(0).italic());
    std::function<void(QTreeWidgetItem*)> verify_catalog_tree = [&](QTreeWidgetItem* item) {
        const int kind = item->data(0, Qt::UserRole + 1).toInt();
        QVERIFY(kind == 0 || kind == 1);
        for (int index = 0; index < item->childCount(); ++index) verify_catalog_tree(item->child(index));
    };
    for (int index = 0; index < tree->topLevelItemCount(); ++index) verify_catalog_tree(tree->topLevelItem(index));

    // 需求与连接按声明模块分组，组内引用去掉模块前缀。
    auto* requirements = window->findChild<QTreeWidget*>("requirementsTree");
    QVERIFY(requirements != nullptr);
    auto* requirement_root = find_item(requirements->invisibleRootItem(), QStringLiteral("（根作用域）"));
    QVERIFY(requirement_root != nullptr);
    auto* requirement_group = find_item(requirement_root, QStringLiteral("plant"));
    QVERIFY(requirement_group != nullptr);
    QVERIFY(find_item(requirement_group, QStringLiteral("input")) != nullptr);
    auto* update_group = find_item(requirement_group, QStringLiteral("update"));
    QVERIFY(update_group != nullptr && update_group->childCount() >= 4);
    auto* connections = window->findChild<QTreeWidget*>("connectionsTree");
    QVERIFY(connections != nullptr);
    auto* connection_root = find_item(connections->invisibleRootItem(), QStringLiteral("（根作用域）"));
    QVERIFY(connection_root != nullptr && connection_root->childCount() >= 1);
    auto* plant_connections = find_item(connection_root, QStringLiteral("plant"));
    QVERIFY(plant_connections != nullptr && plant_connections->childCount() >= 4);

    // 规格页签列出推进入口、输入与观测；共同输入标签显示规格驱动目标。
    auto* spec_tree = window->findChild<QTreeWidget*>("specTree");
    QVERIFY(spec_tree != nullptr && spec_tree->topLevelItemCount() == 3);
    auto* spec_inputs = spec_tree->topLevelItem(1);
    QVERIFY(spec_inputs != nullptr && spec_inputs->childCount() == 1);
    QCOMPARE(spec_inputs->child(0)->text(0), QStringLiteral("a → input/drive"));

    // 时间轴视图：信号树列出观测，游标读数表按信号与序列给出 A／B 值。
    auto* signal_tree = window->findChild<QTreeWidget*>("signalTree");
    QVERIFY(signal_tree != nullptr && signal_tree->topLevelItemCount() == 1);
    QVERIFY(signal_tree->topLevelItem(0)->childCount() == 3);
    auto* cursor_table = window->findChild<QTableWidget*>("cursorTable");
    QVERIFY(cursor_table != nullptr && cursor_table->rowCount() >= 3);
    auto* series_combo = window->findChild<QComboBox*>("sampleSeriesCombo");
    QVERIFY(series_combo != nullptr && series_combo->count() == 1);
    QCOMPARE(window->currentStatus().tracks.front().samples, static_cast<std::size_t>(1));
    QCOMPARE(cursor_table->item(0, 2)->text(), QStringLiteral("0"));
    QCOMPARE(cursor_table->item(0, 3)->text(), QStringLiteral("0"));
    auto* waveform = window->findChild<WaveformWidget*>("waveform");
    QVERIFY(waveform != nullptr);
    QVERIFY(!waveform->checkpoint().has_value());
    QVERIFY(!waveform->branchZone());

    window->findChild<QPushButton*>("checkButton")->click();
    QVERIFY(wait_idle(window));
    auto* check_table = window->findChild<QTableWidget*>("checkTable");
    QVERIFY(check_table != nullptr && check_table->rowCount() >= 4);

    // 草稿变化后目录标注为陈旧（WB-08 边界）。
    auto* left_tabs = window->findChild<QTabWidget*>("leftPane");
    QVERIFY(left_tabs != nullptr);
    QCOMPARE(left_tabs->tabText(0), QStringLiteral("模块树"));
    auto* editor = window->findChild<QWidget*>("configEditor_plant");
    QVERIFY(editor != nullptr);
    auto* line = editor->findChild<QLineEdit*>();
    QVERIFY(line != nullptr);
    line->setFocus();
    line->selectAll();
    QTest::keyClicks(line, QStringLiteral("1"));
    QTest::keyClick(line, Qt::Key_Return);
    QVERIFY(wait_idle(window));
    QCOMPARE(left_tabs->tabText(0), QStringLiteral("模块树（目录陈旧）"));
}

void TestWorkbench::stepAndVariableSwitch() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    auto* cursor_table = window->findChild<QTableWidget*>("cursorTable");
    QVERIFY(cursor_table != nullptr);
    QCOMPARE(cursor_table->item(0, 2)->text(), QStringLiteral("0"));
    QCOMPARE(cursor_table->item(0, 3)->text(), QStringLiteral("0"));

    // 外部输入可选：未设置时按世界自身配置值（示例 a = 1）推进。
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.front().frame, 1);
    QCOMPARE(window->currentStatus().tracks.front().samples, static_cast<std::size_t>(2));
    QCOMPARE(cursor_table->item(0, 3)->text(), QStringLiteral("1"));
    QCOMPARE(cursor_table->item(1, 3)->text(), QStringLiteral("0"));

    // 切换显示信号不改变演化结果。
    auto* signal_tree = window->findChild<QTreeWidget*>("signalTree");
    QVERIFY(signal_tree != nullptr && signal_tree->topLevelItemCount() >= 1);
    auto* signal_group = signal_tree->topLevelItem(0);
    QVERIFY(signal_group->childCount() >= 2);
    signal_group->child(1)->setCheckState(0, Qt::Unchecked);
    signal_group->child(1)->setCheckState(0, Qt::Checked);
    QTest::qWait(30);
    QCOMPARE(window->currentStatus().tracks.front().frame, 1);
    QCOMPARE(window->currentStatus().tracks.front().samples, static_cast<std::size_t>(2));
    QCOMPARE(cursor_table->item(0, 3)->text(), QStringLiteral("1"));
}

void TestWorkbench::stopDuringRun() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    window->findChild<QSpinBox*>("stepsSpin")->setValue(10000);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(window->busy());
    // 运行中切换标签页只请求只读快照，不应禁用停止按钮。
    window->findChild<QTabWidget*>("resultsTabs")->setCurrentWidget(window->findChild<QWidget*>("differencesPage"));
    QVERIFY(window->findChild<QPushButton*>("stopButton")->isEnabled());
    // 请求在完整的逻辑帧边界生效，实际完成数小于请求步数。
    QElapsedTimer stop_timer;
    stop_timer.start();
    window->findChild<QPushButton*>("stopButton")->click();
    QVERIFY(wait_idle(window, 20000));
    qInfo() << "stop response elapsed" << stop_timer.elapsed() << "ms";
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::stopped);
    const auto frame = window->currentStatus().tracks.front().frame;
    QVERIFY(frame >= 0 && frame < 10000);
    QVERIFY(!window->findChild<QPushButton*>("stopButton")->isEnabled());
    QVERIFY(window->findChild<QPushButton*>("stepButton")->isEnabled());

    // 停止后可以继续运行。
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.front().frame, frame + 1);
}

void TestWorkbench::runNThroughput() {
    // 界面侧上限路径：一次运行到 10000 逻辑帧并记录耗时与常驻内存（测量条件见测试文档）。
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));
    window->findChild<QSpinBox*>("stepsSpin")->setValue(10000);
    QElapsedTimer timer;
    timer.start();
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window, 120000));
    const auto elapsed = timer.elapsed();
    QCOMPARE(window->currentStatus().tracks.front().frame, 10000);
    QCOMPARE(window->currentStatus().tracks.front().samples, static_cast<std::size_t>(10001));
    qInfo() << "ui runN 10000 elapsed" << elapsed << "ms";
    // 时间轴全量视图绘制耗时（可见区间渲染 + 按像素列降采样）。
    if (auto* tabs = window->findChild<QTabWidget*>("resultsTabs")) {
        tabs->setCurrentWidget(window->findChild<QWidget*>("timelinePage"));
    }
    QTest::qWait(30);
    if (auto* waveform = window->findChild<QWidget*>("waveform")) {
        QElapsedTimer paint_timer;
        paint_timer.start();
        const QPixmap frame = waveform->grab();
        qInfo() << "waveform full-view paint" << paint_timer.elapsed() << "ms" << frame.size();
    }
#ifdef Q_OS_LINUX
    qint64 rss_kb = -1;
    QFile status(QStringLiteral("/proc/self/status"));
    if (status.open(QIODevice::ReadOnly)) {
        // /proc 报告的大小为 0，必须整体读取而不能依赖 atEnd 判断。
        const QByteArray content = status.readAll();
        for (const QByteArray& line : content.split('\n')) {
            if (!line.startsWith("VmRSS:")) continue;
            const QList<QByteArray> parts = line.simplified().split(' ');
            if (parts.size() >= 2) rss_kb = parts.at(1).toLongLong();
        }
    }
    qInfo() << "ui runN 10000 VmRSS" << rss_kb << "kB";
#endif
}

void TestWorkbench::branchComparisonAndReplay() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    window->findChild<QSpinBox*>("stepsSpin")->setValue(2);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.front().frame, 2);

    window->findChild<QPushButton*>("checkpointButton")->click();
    QVERIFY(wait_idle(window));
    QVERIFY(window->currentStatus().has_checkpoint);
    QCOMPARE(window->currentStatus().checkpoint_frame, 2);

    bool dialog_seen = false;
    QTimer dialog_timer;
    connect(&dialog_timer, &QTimer::timeout, [&dialog_seen, &dialog_timer] {
        auto* dialog = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (dialog == nullptr) return;
        dialog_seen = true;
        if (auto* table = dialog->findChild<QTableWidget*>("branchFieldTable")) {
            for (int row = 0; row < table->rowCount(); ++row) {
                if (table->item(row, 0)->text() == QStringLiteral("plant/state") &&
                    table->item(row, 1)->text() == QStringLiteral("x")) {
                    table->setCurrentCell(row, 0);
                    table->selectRow(row);
                }
            }
        }
        if (auto* edit = dialog->findChild<QLineEdit*>("branchValueEdit")) {
            edit->setText(QStringLiteral("10"));
        }
        dialog_timer.stop();
        dialog->accept();
    });
    dialog_timer.start(10);
    window->findChild<QPushButton*>("branchButton")->click();
    QVERIFY(wait_idle(window));
    QVERIFY(dialog_seen);
    QCOMPARE(window->currentStatus().branches, ascend::session::max_branches);
    auto* series_combo = window->findChild<QComboBox*>("sampleSeriesCombo");
    QVERIFY(series_combo != nullptr);
    QCOMPARE(series_combo->count(), 3);
    QCOMPARE(series_combo->itemText(0), QStringLiteral("运行"));
    QCOMPARE(series_combo->itemText(1), QStringLiteral("对照"));
    QCOMPARE(series_combo->itemText(2), QStringLiteral("干预"));
    const auto checks = window->findChildren<QCheckBox*>();
    int series_checks = 0;
    for (auto* check : checks) {
        if (check->objectName().startsWith(QStringLiteral("seriesCheck_"))) ++series_checks;
    }
    QCOMPARE(series_checks, 3);
    auto* checkpoint_label = window->findChild<QLabel*>("checkpointLabel");
    QVERIFY(checkpoint_label != nullptr);
    QVERIFY(checkpoint_label->text().contains(QStringLiteral("由 2 改为 10")));

    window->findChild<QSpinBox*>("stepsSpin")->setValue(3);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window));
    const auto status = window->currentStatus();
    QCOMPARE(status.tracks.size(), static_cast<std::size_t>(2));
    QCOMPARE(status.tracks[0].frame, 5);
    QCOMPARE(status.tracks[1].frame, 5);

    // 差值信号组：两分支存在时出现，三个观测各一条。
    auto* signal_tree = window->findChild<QTreeWidget*>("signalTree");
    QVERIFY(signal_tree != nullptr && signal_tree->topLevelItemCount() == 2);
    QCOMPARE(signal_tree->topLevelItem(1)->text(0), QStringLiteral("差值（干预 − 对照）"));
    QCOMPARE(signal_tree->topLevelItem(1)->childCount(), 3);

    // 时间轴游标读数：运行／对照／干预三条序列，对照 (5,4,6)、干预 (13,12,22)。
    auto* cursor_table = window->findChild<QTableWidget*>("cursorTable");
    QVERIFY(cursor_table != nullptr && cursor_table->rowCount() == 12);
    QCOMPARE(cursor_table->item(0, 0)->text(), QStringLiteral("x"));
    QCOMPARE(cursor_table->item(0, 1)->text(), QStringLiteral("运行"));
    QCOMPARE(cursor_table->item(0, 3)->text(), QStringLiteral("—"));
    QCOMPARE(cursor_table->item(1, 1)->text(), QStringLiteral("对照"));
    QCOMPARE(cursor_table->item(1, 3)->text(), QStringLiteral("5"));
    QCOMPARE(cursor_table->item(2, 3)->text(), QStringLiteral("13"));
    QCOMPARE(cursor_table->item(4, 3)->text(), QStringLiteral("4"));
    QCOMPARE(cursor_table->item(5, 3)->text(), QStringLiteral("12"));
    QCOMPARE(cursor_table->item(7, 3)->text(), QStringLiteral("6"));
    QCOMPARE(cursor_table->item(8, 3)->text(), QStringLiteral("22"));
    // 差值曲线读数：游标 A 停在加载时的逻辑帧 0（非共同帧，读数为空），
    // 游标 B 跟随数据末端逻辑帧 5，给出精确差值。
    QCOMPARE(cursor_table->item(9, 0)->text(), QStringLiteral("x（差值）"));
    QCOMPARE(cursor_table->item(9, 1)->text(), QStringLiteral("干预 − 对照"));
    QCOMPARE(cursor_table->item(9, 2)->text(), QStringLiteral("—"));
    QCOMPARE(cursor_table->item(9, 3)->text(), QStringLiteral("8"));
    QCOMPARE(cursor_table->item(9, 4)->text(), QStringLiteral("—"));
    QCOMPARE(cursor_table->item(10, 3)->text(), QStringLiteral("8"));
    QCOMPARE(cursor_table->item(11, 3)->text(), QStringLiteral("16"));

    // 检查点可视化：参考线与分支区间状态，点击参考线把游标 A 定位到检查点。
    auto* waveform = window->findChild<WaveformWidget*>("waveform");
    QVERIFY(waveform != nullptr);
    QVERIFY(waveform->checkpoint().has_value() && *waveform->checkpoint() == 2);
    QVERIFY(waveform->branchZone());
    QTest::mouseClick(waveform, Qt::LeftButton, Qt::NoModifier, waveform->framePosition(2));
    QCOMPARE(waveform->cursorA(), static_cast<std::int64_t>(2));
    QCOMPARE(cursor_table->item(1, 2)->text(), QStringLiteral("2"));
    QCOMPARE(cursor_table->item(9, 2)->text(), QStringLiteral("8"));
    QCOMPARE(cursor_table->item(9, 4)->text(), QStringLiteral("0"));

    // 共同逻辑帧差值：8、8、16。
    window->findChild<QTabWidget*>("resultsTabs")->setCurrentWidget(window->findChild<QWidget*>("differencesPage"));
    QVERIFY(wait_until([window] { return !window->busy(); }));
    QTest::qWait(30);
    auto* diff = window->findChild<QTableWidget*>("diffTable");
    QCOMPARE(diff->rowCount(), 4);
    QCOMPARE(diff->item(3, 1)->text(), QStringLiteral("8"));
    QCOMPARE(diff->item(3, 2)->text(), QStringLiteral("8"));
    QCOMPARE(diff->item(3, 3)->text(), QStringLiteral("16"));

    // 重放：场景 A 成功。
    QString replay_text;
    QTimer replay_timer;
    connect(&replay_timer, &QTimer::timeout, [&replay_text, &replay_timer] {
        auto* box = qobject_cast<QMessageBox*>(QApplication::activeModalWidget());
        if (box == nullptr) return;
        replay_text = box->text();
        replay_timer.stop();
        box->accept();
    });
    replay_timer.start(10);
    window->findChild<QPushButton*>("replayButton")->click();
    QVERIFY(wait_until([&replay_text] { return !replay_text.isEmpty(); }, 8000));
    QVERIFY(replay_text.contains(QStringLiteral("重放成功")));
    QVERIFY(!replay_text.contains(QStringLiteral("部分范围未比较")));

    // 从检查点重建分支：轨迹回到共同起点，可继续运行。
    window->findChild<QPushButton*>("resetBranchesButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks[0].frame, 2);
    QCOMPARE(window->currentStatus().tracks[0].samples, static_cast<std::size_t>(1));
    QCOMPARE(window->currentStatus().tracks[1].samples, static_cast<std::size_t>(1));
    QVERIFY(window->findChild<QPushButton*>("stepButton")->isEnabled());
}

void TestWorkbench::failedStateControls() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    // 通过初始配置表单把 x 改为 64 位上限并应用。
    auto* x_edit = window->findChild<QLineEdit*>("configLeaf_x");
    QVERIFY(x_edit != nullptr);
    x_edit->setFocus();
    x_edit->selectAll();
    QTest::keyClicks(x_edit, QString::number(std::numeric_limits<qint64>::max()));
    QTest::keyClick(x_edit, Qt::Key_Return);
    QVERIFY(wait_idle(window));
    QVERIFY(window->currentStatus().dirty);
    window->findChild<QPushButton*>("applyButton")->click();
    QVERIFY(wait_idle(window));
    QVERIFY(!window->currentStatus().dirty);

    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::failed);
    QVERIFY(window->currentStatus().tracks.front().failed);
    QVERIFY(!window->findChild<QPushButton*>("stepButton")->isEnabled());
    QVERIFY(!window->findChild<QPushButton*>("checkpointButton")->isEnabled());
    QVERIFY(!window->findChild<QPushButton*>("branchButton")->isEnabled());
    QVERIFY(window->findChild<QPushButton*>("applyButton")->isEnabled());
    QVERIFY(window->findChild<QPushButton*>("replayButton")->isEnabled());
    auto* diagnostics = window->findChild<QTreeWidget*>("diagnosticsTree");
    QVERIFY(diagnostics != nullptr && diagnostics->topLevelItemCount() >= 1);

    // 修正配置并重建后恢复可运行。
    x_edit = window->findChild<QLineEdit*>("configLeaf_x");
    QVERIFY(x_edit != nullptr);
    x_edit->setFocus();
    x_edit->selectAll();
    QTest::keyClicks(x_edit, QStringLiteral("0"));
    QTest::keyClick(x_edit, Qt::Key_Return);
    QVERIFY(wait_idle(window));
    window->findChild<QPushButton*>("applyButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::runnable);
    QVERIFY(window->findChild<QPushButton*>("stepButton")->isEnabled());
}

void TestWorkbench::closeWhileRunning() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));
    window->findChild<QSpinBox*>("stepsSpin")->setValue(10000);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(window->busy());
    QTest::qWait(10);
    window->close();
    QVERIFY(wait_until([window] { return !window->busy(); }, 20000));
    QVERIFY(!window->isVisible());
}

void TestWorkbench::abandonedWorkbench() {
    // 构造但未启动的工作台析构时必须回收控制层（sanitizer 下验证无泄漏）。
    auto workbench = std::make_unique<Workbench>(example_template(default_resource_paths()));
    QVERIFY(workbench->window() != nullptr);
    workbench.reset();
}

void TestWorkbench::uiTextFallback() {
    // 界面文案按 ascend.workbench 文本域解析；缺失资源或翻译时回退默认模板。
    UiTexts empty;
    empty.locale = "en";
    QCOMPARE(ui_text(empty, "workbench.action.check", "Check"), QStringLiteral("Check"));

    UiTexts zh = load_ui_texts(ASCEND_WORKBENCH_UI_I18N, "zh-CN");
    QVERIFY(zh.load_errors.empty());
    QCOMPARE(ui_text(zh, "workbench.action.check", "Check"), QStringLiteral("检查"));
    QCOMPARE(ui_text(zh, "workbench.missing.key", "Fallback text"), QStringLiteral("Fallback text"));

    UiTexts en = load_ui_texts(ASCEND_WORKBENCH_UI_I18N, "en");
    QCOMPARE(ui_text(en, "workbench.action.check", "Check"), QStringLiteral("Check"));

    UiTexts broken = load_ui_texts("/nonexistent/workbench-zh-CN.json", "zh-CN");
    QVERIFY(!broken.load_errors.empty());
    QCOMPARE(ui_text(broken, "workbench.action.check", "Check"), QStringLiteral("Check"));
}

void TestWorkbench::resourceResolution() {
    // 构建时把语言资源复制到可执行文件同目录的 resources/，运行时优先使用
    // 运行目录副本（复制运行目录即可在另一台机器保持文案）。
    const std::string app_dir = QCoreApplication::applicationDirPath().toStdString();
    const ResourcePaths runtime = resource_paths_from(app_dir);
    const auto under_app_dir = [&](const std::string& path) {
        return path.rfind(app_dir, 0) == 0;
    };
    QVERIFY(under_app_dir(runtime.engine_i18n));
    QVERIFY(under_app_dir(runtime.session_i18n));
    QVERIFY(under_app_dir(runtime.ui_i18n));
    QVERIFY(under_app_dir(runtime.example_root));
    QVERIFY(QFileInfo::exists(QString::fromStdString(runtime.engine_i18n)));
    QVERIFY(QFileInfo::exists(QString::fromStdString(runtime.session_i18n)));
    QVERIFY(QFileInfo::exists(QString::fromStdString(runtime.ui_i18n)));
    QVERIFY(QFileInfo(QString::fromStdString(runtime.example_root)).isDir());

    // 运行目录缺失时回退编译期源码路径，开发构建仍可直接运行。
    const ResourcePaths fallback = resource_paths_from("/nonexistent/ascend-workbench");
    QVERIFY(!under_app_dir(fallback.engine_i18n));
    QVERIFY(QFileInfo::exists(QString::fromStdString(fallback.engine_i18n)));
    QVERIFY(QFileInfo::exists(QString::fromStdString(fallback.session_i18n)));
    QVERIFY(QFileInfo::exists(QString::fromStdString(fallback.ui_i18n)));
    QVERIFY(QFileInfo(QString::fromStdString(fallback.example_root)).isDir());

    // 运行目录副本能被会话与界面文案加载（与工作台启动路径一致）。
    const UiTexts texts = load_ui_texts(runtime.ui_i18n, "zh-CN");
    QVERIFY(texts.load_errors.empty());
    QCOMPARE(ui_text(texts, "workbench.action.check", "Check"), QStringLiteral("检查"));
}

void TestWorkbench::menuBarStructure() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    auto* menu_bar = window->menuBar();
    QVERIFY(menu_bar != nullptr);
    QStringList titles;
    for (QAction* action : menu_bar->actions()) titles << action->text();
    QCOMPARE(titles, (QStringList{QStringLiteral("文件"), QStringLiteral("编辑"),
                                  QStringLiteral("查看"), QStringLiteral("运行")}));

    const auto find_action = [](QMenu* menu, const QString& text) {
        for (QAction* action : menu->actions()) {
            if (action->text() == text) return action;
        }
        return static_cast<QAction*>(nullptr);
    };

    // 运行菜单与按钮同源：触发“单步”等同点击按钮。
    QMenu* run_menu = menu_bar->actions().at(3)->menu();
    QAction* step = find_action(run_menu, QStringLiteral("单步"));
    QVERIFY(step != nullptr);
    QVERIFY(step->isEnabled());
    const auto samples = window->currentStatus().tracks.front().samples;
    step->trigger();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.front().samples, samples + 1);

    // 文件菜单：新建研究与研究文件入口已实装。
    QMenu* file_menu = menu_bar->actions().at(0)->menu();
    QAction* new_research = find_action(file_menu, QStringLiteral("新建研究…"));
    QVERIFY(new_research != nullptr);
    QVERIFY(new_research->isEnabled());
    QAction* open_experiment = find_action(file_menu, QStringLiteral("打开研究…"));
    QVERIFY(open_experiment != nullptr);
    QVERIFY(open_experiment->isEnabled());
    QAction* save = find_action(file_menu, QStringLiteral("保存"));
    QVERIFY(save != nullptr);
    QVERIFY(save->isEnabled());
    QAction* save_as = find_action(file_menu, QStringLiteral("另存为…"));
    QVERIFY(save_as != nullptr);
    QVERIFY(save_as->isEnabled());

    // 未实装项占位且禁用，并带后续阶段提示。
    QMenu* edit_menu = menu_bar->actions().at(1)->menu();
    QAction* placeholder_action = find_action(edit_menu, QStringLiteral("撤销"));
    QVERIFY(placeholder_action != nullptr);
    QVERIFY(!placeholder_action->isEnabled());
    QVERIFY(!placeholder_action->toolTip().isEmpty());

    // 查看菜单：停靠面板开关为勾选项，选择后菜单保持展开。
    QMenu* view_menu = menu_bar->actions().at(2)->menu();
    auto* state_dock = window->findChild<QDockWidget*>("stateDock");
    QVERIFY(state_dock != nullptr);
    QVERIFY(state_dock->features().testFlag(QDockWidget::DockWidgetFloatable));
    QVERIFY(state_dock->features().testFlag(QDockWidget::DockWidgetMovable));
    QAction* state_toggle = find_action(view_menu, QStringLiteral("状态"));
    QVERIFY(state_toggle != nullptr);
    QVERIFY(state_toggle->isCheckable());
    QVERIFY(state_toggle->isChecked());
    view_menu->popup(QPoint(40, 40));
    QVERIFY(wait_until([view_menu] { return view_menu->isVisible(); }));
    const QPoint center = view_menu->actionGeometry(state_toggle).center();
    QTest::mouseClick(view_menu, Qt::LeftButton, Qt::NoModifier, center);
    QVERIFY(!state_toggle->isChecked());
    QVERIFY(state_dock->isHidden());
    QVERIFY(view_menu->isVisible());
    QTest::mouseClick(view_menu, Qt::LeftButton, Qt::NoModifier, center);
    QVERIFY(state_toggle->isChecked());
    QVERIFY(!state_dock->isHidden());
    QVERIFY(view_menu->isVisible());
    view_menu->close();

    // 停靠面板可拆分浮动并恢复停靠；中央工作区为浏览器式页签（可关闭、从查看菜单重开）。
    state_dock->setFloating(true);
    QVERIFY(state_dock->isFloating());
    state_dock->setFloating(false);
    QVERIFY(!state_dock->isFloating());
    QAction* config_toggle = find_action(view_menu, QStringLiteral("配置"));
    QVERIFY(config_toggle != nullptr);
    auto* config_dock = window->findChild<QDockWidget*>("configDock");
    QVERIFY(config_dock != nullptr);
    config_toggle->trigger();
    QVERIFY(config_dock->isHidden());
    config_toggle->trigger();
    QVERIFY(!config_dock->isHidden());

    // 时间轴/共同逻辑帧差值页签：默认打开，可从查看菜单关闭与重开。
    QAction* timeline_toggle = find_action(view_menu, QStringLiteral("时间轴"));
    QAction* diffs_toggle = find_action(view_menu, QStringLiteral("共同逻辑帧差值"));
    QVERIFY(timeline_toggle != nullptr && diffs_toggle != nullptr);
    QVERIFY(timeline_toggle->isChecked());
    QVERIFY(diffs_toggle->isChecked());
    auto* results_tabs = window->findChild<QTabWidget*>("resultsTabs");
    auto* timeline_page = window->findChild<QWidget*>("timelinePage");
    auto* differences_page = window->findChild<QWidget*>("differencesPage");
    QVERIFY(results_tabs != nullptr && timeline_page != nullptr && differences_page != nullptr);
    // 开始页默认打开：先关闭，聚焦其余页签语义检查。
    auto* start_toggle = window->findChild<QAction*>("startViewAction");
    auto* start_page = window->findChild<QWidget*>("startPage");
    QVERIFY(start_toggle != nullptr && start_page != nullptr);
    QVERIFY(start_toggle->isChecked());
    QVERIFY(results_tabs->indexOf(start_page) >= 0);
    start_toggle->trigger();
    QCOMPARE(results_tabs->indexOf(start_page), -1);
    QVERIFY(!start_toggle->isChecked());
    QVERIFY(results_tabs->indexOf(timeline_page) >= 0);
    QVERIFY(results_tabs->indexOf(differences_page) >= 0);
    diffs_toggle->trigger();
    QCOMPARE(results_tabs->indexOf(differences_page), -1);
    QVERIFY(!diffs_toggle->isChecked());
    diffs_toggle->trigger();
    QVERIFY(results_tabs->indexOf(differences_page) >= 0);
    QVERIFY(diffs_toggle->isChecked());

    // 浏览器式关闭按钮：最后一个页签的关闭请求被忽略（至少保留一个视图）。
    timeline_toggle->trigger();
    QCOMPARE(results_tabs->indexOf(timeline_page), -1);
    QVERIFY(!timeline_toggle->isChecked());
    QCOMPARE(results_tabs->count(), 1);
    const int last_index = results_tabs->indexOf(differences_page);
    QWidget* close_button = results_tabs->tabBar()->tabButton(last_index, QTabBar::RightSide);
    QVERIFY(close_button != nullptr);
    QTest::mouseClick(close_button, Qt::LeftButton);
    QCOMPARE(results_tabs->count(), 1);
    QVERIFY(diffs_toggle->isChecked());
    timeline_toggle->trigger();
    QVERIFY(results_tabs->indexOf(timeline_page) >= 0);
    QVERIFY(timeline_toggle->isChecked());

    // 底部面板同样可在查看菜单中开关。
    auto* dock = window->findChild<QDockWidget*>("diagnosticsDock");
    QVERIFY(dock != nullptr);
    const bool hidden = dock->isHidden();
    QAction* toggle = find_action(view_menu, QStringLiteral("诊断"));
    QVERIFY(toggle != nullptr);
    toggle->trigger();
    QVERIFY(dock->isHidden() != hidden);
    toggle->trigger();
    QCOMPARE(dock->isHidden(), hidden);
}


void TestWorkbench::fileSaveOpen() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    // 场景 A：推进 2 步、检查点、干预分支、再推进 3 步。
    window->findChild<QSpinBox*>("stepsSpin")->setValue(2);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window));
    window->findChild<QPushButton*>("checkpointButton")->click();
    QVERIFY(wait_idle(window));
    QTimer dialog_timer;
    connect(&dialog_timer, &QTimer::timeout, [&dialog_timer] {
        auto* dialog = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (dialog == nullptr) return;
        if (auto* table = dialog->findChild<QTableWidget*>("branchFieldTable")) {
            for (int row = 0; row < table->rowCount(); ++row) {
                if (table->item(row, 0)->text() == QStringLiteral("plant/state") &&
                    table->item(row, 1)->text() == QStringLiteral("x")) {
                    table->setCurrentCell(row, 0);
                    table->selectRow(row);
                }
            }
        }
        if (auto* edit = dialog->findChild<QLineEdit*>("branchValueEdit")) {
            edit->setText(QStringLiteral("10"));
        }
        dialog_timer.stop();
        dialog->accept();
    });
    dialog_timer.start(10);
    window->findChild<QPushButton*>("branchButton")->click();
    QVERIFY(wait_idle(window));
    window->findChild<QSpinBox*>("stepsSpin")->setValue(3);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks[0].frame, 5);

    // 另存为实验文件。
    const QString path = QDir::tempPath() + QStringLiteral("/ascend-workbench-ui.aexp");
    QFile::remove(path);
    QTimer save_timer;
    connect(&save_timer, &QTimer::timeout, [&] { drive_file_dialog(path); });
    save_timer.start(10);
    window->findChild<QAction*>("saveAsAction")->trigger();
    QVERIFY(wait_until([&] { return QFile::exists(path) && window->currentFile() == path; }, 8000));
    save_timer.stop();
    QVERIFY(wait_idle(window));

    // 继续推进制造差异，然后打开文件：接管回保存时的状态并可继续推进。
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks[0].frame, 6);
    QTimer open_timer;
    connect(&open_timer, &QTimer::timeout, [&] { drive_file_dialog(path); });
    open_timer.start(10);
    window->findChild<QAction*>("openExperimentAction")->trigger();
    QVERIFY(wait_until([&] {
        return window->currentStatus().tracks.size() == 2 && window->currentStatus().tracks[0].frame == 5;
    }, 8000));
    open_timer.stop();
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::runnable);
    QCOMPARE(window->currentFile(), path);
    QVERIFY(window->findChild<QPushButton*>("stepButton")->isEnabled());
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks[0].frame, 6);
    QFile::remove(path);
}

void TestWorkbench::fileOpenFailure() {
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    // 打开结构损坏的文件：弹出针对本次操作的失败原因，会话不变，诊断入面板。
    const QString path = QDir::tempPath() + QStringLiteral("/ascend-workbench-ui-bad.aexp");
    {
        QFile file(path);
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write("not an ascend experiment file");
    }
    QString box_text;
    QTimer timer;
    connect(&timer, &QTimer::timeout, [&] {
        QWidget* modal = QApplication::activeModalWidget();
        if (auto* box = qobject_cast<QMessageBox*>(modal)) {
            box_text = box->text();
            box->accept();
            return;
        }
        auto* dialog = qobject_cast<QFileDialog*>(modal);
        if (dialog == nullptr) return;
        dialog->setDirectory(QFileInfo(path).absolutePath());
        dialog->setFocus();
        dialog->selectFile(path);
        if (dialog->selectedFiles().value(0) != path) return;
        QMetaObject::invokeMethod(dialog, "accept", Qt::DirectConnection);
    });
    timer.start(10);
    window->findChild<QAction*>("openExperimentAction")->trigger();
    QVERIFY(wait_until([&] { return !box_text.isEmpty(); }, 8000));
    timer.stop();
    // 失败原因随本次操作给出（路径 + 具体原因），而不是旧诊断或空。
    QVERIFY(box_text.contains(QStringLiteral("ascend-workbench-ui-bad.aexp")));
    QVERIFY(box_text.contains(QStringLiteral("\n")));
    QVERIFY(window->currentFile().isEmpty());
    auto* tree = window->findChild<QTreeWidget*>("diagnosticsTree");
    QVERIFY(tree != nullptr);
    QVERIFY(wait_until([&] { return tree->topLevelItemCount() >= 1; }, 4000));
    QFile::remove(path);
}

void TestWorkbench::modulePackageLoadUnload() {
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    // 生成示例模块包文件（无状态值模块）。
    const QString path = write_example_package(
        QDir::tempPath() + QStringLiteral("/ascend-workbench-module.amod"));

    auto* table = window->findChild<QTableWidget*>("modulePackageTable");
    QVERIFY(table != nullptr);
    QCOMPARE(table->rowCount(), 0);
    // 模块库是中央工作区页签：默认不打开；载入成功时自动打开并切换过去。
    auto* results = window->findChild<QTabWidget*>("resultsTabs");
    QVERIFY(results != nullptr);
    auto* page = window->findChild<QWidget*>("packagesPage");
    QVERIFY(page != nullptr);
    QCOMPARE(results->indexOf(page), -1);
    QVERIFY(!page->isVisible());
    auto* library_action = window->findChild<QAction*>("packageLibraryAction");
    QVERIFY(library_action != nullptr);
    QVERIFY(library_action->isCheckable());
    QVERIFY(!library_action->isChecked());

    // 文件 → 载入模块包…：驱动文件对话框；成功后模块库页签打开并出现概要。
    QTimer load_timer;
    connect(&load_timer, &QTimer::timeout, [&] { drive_choose_paths_dialog({path}); });
    load_timer.start(10);
    window->findChild<QAction*>("loadModulePackageAction")->trigger();
    QVERIFY(wait_until([&] { return table->rowCount() == 1; }, 8000));
    load_timer.stop();
    QVERIFY(wait_until([&] { return results->indexOf(page) >= 0; }, 4000));
    QCOMPARE(results->currentWidget(), page);
    QVERIFY(library_action->isChecked());
    QCOMPARE(table->item(0, 0)->text(), QStringLiteral("library.source"));
    QCOMPARE(table->item(0, 1)->text(), QStringLiteral("1.0"));
    QCOMPARE(table->item(0, 3)->text(), QStringLiteral("无状态"));
    QCOMPARE(table->item(0, 4)->text(), QStringLiteral("1"));

    // 文件 → 卸载模块包…：选择对话框确认后表格清空（页签保留）。
    QTimer unload_timer;
    connect(&unload_timer, &QTimer::timeout, [&] {
        auto* dialog = qobject_cast<QInputDialog*>(QApplication::activeModalWidget());
        if (dialog == nullptr) return;
        unload_timer.stop();
        dialog->accept();
    });
    unload_timer.start(10);
    window->findChild<QAction*>("unloadModulePackageAction")->trigger();
    QVERIFY(wait_until([&] { return table->rowCount() == 0; }, 8000));
    unload_timer.stop();
    QVERIFY(results->indexOf(page) >= 0);

    // 浏览器式页签：关闭按钮移除页签并从查看菜单重开。
    const int tab_index = results->indexOf(page);
    QWidget* close_button = results->tabBar()->tabButton(tab_index, QTabBar::RightSide);
    QVERIFY(close_button != nullptr);
    QTest::mouseClick(close_button, Qt::LeftButton);
    QCOMPARE(results->indexOf(page), -1);
    QVERIFY(!library_action->isChecked());
    library_action->trigger();
    QVERIFY(results->indexOf(page) >= 0);
    QCOMPARE(results->currentWidget(), page);

    // 损坏文件：提示本次操作的原因，已关闭的模块库页签不打开且列表不变。
    library_action->trigger();  // 关闭页签
    QCOMPARE(results->indexOf(page), -1);
    const QString broken = QDir::tempPath() + QStringLiteral("/ascend-workbench-module-bad.amod");
    {
        QFile file(broken);
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write("not a module package");
    }
    QString box_text;
    QTimer broken_timer;
    connect(&broken_timer, &QTimer::timeout, [&] {
        QWidget* modal = QApplication::activeModalWidget();
        if (auto* box = qobject_cast<QMessageBox*>(modal)) {
            box_text = box->text();
            box->accept();
            return;
        }
        drive_choose_paths_dialog({broken});
    });
    broken_timer.start(10);
    window->findChild<QAction*>("loadModulePackageAction")->trigger();
    QVERIFY(wait_until([&] { return !box_text.isEmpty(); }, 8000));
    broken_timer.stop();
    QVERIFY(box_text.contains(QStringLiteral("ascend-workbench-module-bad.amod")));
    QVERIFY(box_text.contains(QStringLiteral("\n")));
    QCOMPARE(table->rowCount(), 0);
    QCOMPARE(results->indexOf(page), -1);

    QFile::remove(path);
    QFile::remove(broken);
}

void TestWorkbench::modulePackageMultiLoad() {
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    const QString first = write_example_package(
        QDir::tempPath() + QStringLiteral("/ascend-workbench-multi-source.amod"));
    const QString second = write_example_package(
        QDir::tempPath() + QStringLiteral("/ascend-workbench-multi-accumulator.amod"),
        ascend::example::library_accumulator_definition);
    auto* table = window->findChild<QTableWidget*>("modulePackageTable");
    QVERIFY(table != nullptr);
    QCOMPARE(table->rowCount(), 0);

    // 一次多选两个包：两行都出现，模块库页签自动打开并切换过去。
    QTimer load_timer;
    connect(&load_timer, &QTimer::timeout, [&] { drive_choose_paths_dialog({first, second}); });
    load_timer.start(10);
    window->findChild<QAction*>("loadModulePackageAction")->trigger();
    QVERIFY(wait_until([&] { return table->rowCount() == 2; }, 8000));
    load_timer.stop();
    auto* results = window->findChild<QTabWidget*>("resultsTabs");
    auto* page = window->findChild<QWidget*>("packagesPage");
    QVERIFY(results != nullptr && page != nullptr);
    QVERIFY(wait_until([&] { return results->indexOf(page) >= 0; }, 4000));
    QCOMPARE(results->currentWidget(), page);
    QStringList definitions;
    for (int row = 0; row < table->rowCount(); ++row) definitions << table->item(row, 0)->text();
    QVERIFY(definitions.contains(QStringLiteral("library.source")));
    QVERIFY(definitions.contains(QStringLiteral("library.accumulator")));

    // 同批中失败项（重复载入与损坏文件）汇总一次列出：列表不变。
    const QString broken = QDir::tempPath() + QStringLiteral("/ascend-workbench-multi-bad.amod");
    {
        QFile file(broken);
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write("not a module package");
    }
    QString box_text;
    QTimer broken_timer;
    connect(&broken_timer, &QTimer::timeout, [&] {
        QWidget* modal = QApplication::activeModalWidget();
        if (auto* box = qobject_cast<QMessageBox*>(modal)) {
            box_text = box->text();
            box->accept();
            return;
        }
        drive_choose_paths_dialog({first, broken});
    });
    broken_timer.start(10);
    window->findChild<QAction*>("loadModulePackageAction")->trigger();
    QVERIFY(wait_until([&] { return !box_text.isEmpty(); }, 8000));
    broken_timer.stop();
    QVERIFY(box_text.contains(QStringLiteral("ascend-workbench-multi-source.amod")));
    QVERIFY(box_text.contains(QStringLiteral("ascend-workbench-multi-bad.amod")));
    QVERIFY(box_text.contains(QStringLiteral("\n")));
    QCOMPARE(table->rowCount(), 2);

    QFile::remove(first);
    QFile::remove(second);
    QFile::remove(broken);
}

void TestWorkbench::modulePackageFolderLoad() {
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    const QString root = QDir::tempPath() + QStringLiteral("/ascend-workbench-folder");
    QDir().mkpath(root + QStringLiteral("/empty"));
    const QString first = write_example_package(root + QStringLiteral("/library.source.amod"));
    const QString second = write_example_package(root + QStringLiteral("/library.accumulator.amod"),
                                                 ascend::example::library_accumulator_definition);
    const QString broken = root + QStringLiteral("/library.bad.amod");
    {
        QFile file(broken);
        QVERIFY(file.open(QIODevice::WriteOnly));
        file.write("not a module package");
    }
    {
        QFile note(root + QStringLiteral("/notes.txt"));
        QVERIFY(note.open(QIODevice::WriteOnly));
        note.write("not a module package");
    }

    // 选择文件夹：载入其中全部模块包（忽略无关文件），失败项一次列出。
    QStringList seen;
    QString target = root;
    QTimer timer;
    connect(&timer, &QTimer::timeout, [&] {
        QWidget* modal = QApplication::activeModalWidget();
        if (auto* box = qobject_cast<QMessageBox*>(modal)) {
            seen << box->text();
            box->accept();
            return;
        }
        drive_choose_paths_dialog({target});
    });
    timer.start(10);
    auto* action = window->findChild<QAction*>("loadModulePackageFolderAction");
    QVERIFY(action != nullptr);
    action->trigger();
    QVERIFY(wait_until([&] { return seen.size() == 1; }, 8000));
    QVERIFY(seen.first().contains(QStringLiteral("ascend-workbench-folder/library.bad.amod")));
    QVERIFY(!seen.first().contains(QStringLiteral("notes.txt")));
    auto* table = window->findChild<QTableWidget*>("modulePackageTable");
    QVERIFY(table != nullptr);
    QVERIFY(wait_until([&] { return table->rowCount() == 2; }, 4000));

    // 空文件夹：提示没有模块包，列表不变。
    QVERIFY(wait_until([&] { return action->isEnabled(); }, 4000));
    target = root + QStringLiteral("/empty");
    action->trigger();
    QVERIFY(wait_until([&] { return seen.size() == 2; }, 8000));
    QVERIFY(seen.last().contains(QStringLiteral("没有模块包")));
    QCOMPARE(table->rowCount(), 2);
    timer.stop();

    QFile::remove(first);
    QFile::remove(second);
    QFile::remove(broken);
    QFile::remove(root + QStringLiteral("/notes.txt"));
    QDir(root).removeRecursively();
}

void TestWorkbench::systemEditorAssembly() {
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    // 编辑 → 系统编辑器…：打开中央页签。
    window->findChild<QAction*>("systemEditorAction")->trigger();
    auto* results = window->findChild<QTabWidget*>("resultsTabs");
    auto* editor = window->findChild<QWidget*>("systemEditorPage");
    QVERIFY(results != nullptr && editor != nullptr);
    QVERIFY(wait_until([&] { return results->indexOf(editor) >= 0; }, 4000));
    QCOMPARE(results->currentWidget(), editor);

    // 可用模块：示例内置与模块库内置注册都可选。
    auto* definition_combo = window->findChild<QComboBox*>("systemDefinitionCombo");
    QVERIFY(definition_combo != nullptr);
    QVERIFY(wait_until([&] { return definition_combo->findText(QStringLiteral("example.plant")) >= 0; }, 4000));
    QVERIFY(definition_combo->findText(QStringLiteral("library.source")) >= 0);

    // 新建系统：命名对话框。
    QTimer name_timer;
    connect(&name_timer, &QTimer::timeout, [&] {
        auto* dialog = qobject_cast<QInputDialog*>(QApplication::activeModalWidget());
        if (dialog == nullptr) return;
        name_timer.stop();
        dialog->setTextValue(QStringLiteral("编辑器系统"));
        dialog->accept();
    });
    name_timer.start(10);
    window->findChild<QPushButton*>("systemNewButton")->click();
    QVERIFY(wait_idle(window));
    QVERIFY(wait_until([&] { return window->currentStatus().model_name == "编辑器系统"; }, 4000));
    QVERIFY(window->windowTitle().contains(QStringLiteral("编辑器系统")));

    // 添加模块：实例名按定义自动建议。
    auto* instance_edit = window->findChild<QLineEdit*>("systemInstanceEdit");
    auto* add_button = window->findChild<QPushButton*>("systemAddButton");
    QVERIFY(instance_edit != nullptr && add_button != nullptr);
    definition_combo->setCurrentText(QStringLiteral("example.plant"));
    QCOMPARE(instance_edit->text(), QStringLiteral("plant"));
    add_button->click();
    QVERIFY(wait_idle(window));
    definition_combo->setCurrentText(QStringLiteral("example.stimulus"));
    QCOMPARE(instance_edit->text(), QStringLiteral("stimulus"));  // 默认取定义末段，可改
    instance_edit->setText(QStringLiteral("input"));
    add_button->click();
    QVERIFY(wait_idle(window));

    auto* tree = window->findChild<QTreeWidget*>("systemTree");
    QVERIFY(tree != nullptr);
    QVERIFY(wait_until([&] { return tree->topLevelItemCount() == 2; }, 4000));
    const auto find_requirement = [&](const QString& instance, const QString& symbol) -> QTreeWidgetItem* {
        for (int i = 0; i < tree->topLevelItemCount(); ++i) {
            QTreeWidgetItem* top = tree->topLevelItem(i);
            if (top->text(0) != instance) continue;
            for (int j = 0; j < top->childCount(); ++j) {
                if (top->child(j)->text(0) == symbol) return top->child(j);
            }
        }
        return nullptr;
    };
    QTreeWidgetItem* requirement = find_requirement(QStringLiteral("plant"), QStringLiteral("input"));
    QVERIFY(requirement != nullptr);
    QVERIFY(requirement->text(2) != QStringLiteral("input/value"));  // 未连接

    // 连接需求到 input/value。
    tree->setCurrentItem(requirement);
    auto* provider_combo = window->findChild<QComboBox*>("systemProviderCombo");
    QVERIFY(provider_combo != nullptr);
    QVERIFY(wait_until([&] { return provider_combo->findText(QStringLiteral("input/value")) >= 0; }, 4000));
    provider_combo->setCurrentText(QStringLiteral("input/value"));
    window->findChild<QPushButton*>("systemConnectButton")->click();
    QVERIFY(wait_idle(window));
    requirement = find_requirement(QStringLiteral("plant"), QStringLiteral("input"));
    QVERIFY(requirement != nullptr);
    QCOMPARE(requirement->text(2), QStringLiteral("input/value"));

    // 规格：推进入口与观测显式选择；输入自动推导。
    auto* advance_combo = window->findChild<QComboBox*>("systemAdvanceCombo");
    QVERIFY(advance_combo != nullptr);
    QVERIFY(advance_combo->findText(QStringLiteral("plant/advance")) >= 0);
    advance_combo->setCurrentText(QStringLiteral("plant/advance"));
    QVERIFY(wait_idle(window));
    auto* observation_list = window->findChild<QListWidget*>("systemObservationList");
    QVERIFY(observation_list != nullptr);
    QVERIFY(observation_list->count() >= 3);
    for (int i = 0; i < observation_list->count(); ++i) {
        auto* item = observation_list->item(i);
        if (item->text() == QStringLiteral("plant/x") || item->text() == QStringLiteral("plant/y") ||
            item->text() == QStringLiteral("plant/z")) {
            item->setCheckState(Qt::Checked);
        }
    }
    QVERIFY(wait_idle(window));
    QVERIFY(wait_until([&] {
        int checked = 0;
        for (int i = 0; i < observation_list->count(); ++i) {
            if (observation_list->item(i)->checkState() == Qt::Checked) ++checked;
        }
        return checked == 3;
    }, 4000));
    auto* input_label = window->findChild<QLabel*>("systemInputLabel");
    QVERIFY(input_label != nullptr);
    QVERIFY(input_label->text().contains(QStringLiteral("input.drive")));

    // 应用并推进：新系统成为可运行模型。
    window->findChild<QPushButton*>("systemApplyButton")->click();
    QVERIFY(wait_until([&] { return window->currentStatus().phase == ascend::session::Phase::runnable; }, 8000));
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.size(), std::size_t{1});
    QCOMPARE(window->currentStatus().tracks.front().frame, std::int64_t{1});

    // 移除实例（确认对话框）：plant 移除后只剩 input。
    tree->setCurrentItem(find_requirement(QStringLiteral("plant"), QStringLiteral("input")));
    QTimer remove_timer;
    connect(&remove_timer, &QTimer::timeout, [&] {
        auto* box = qobject_cast<QMessageBox*>(QApplication::activeModalWidget());
        if (box == nullptr) return;
        remove_timer.stop();
        box->button(QMessageBox::Yes)->click();
    });
    remove_timer.start(10);
    window->findChild<QPushButton*>("systemRemoveButton")->click();
    QVERIFY(wait_idle(window));
    QVERIFY(wait_until([&] { return tree->topLevelItemCount() == 1; }, 4000));
    QCOMPARE(tree->topLevelItem(0)->text(0), QStringLiteral("input"));
}

void TestWorkbench::startPageResearch() {
    auto workbench = start_workbench(false);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    // 默认打开开始页；未打开系统时标题不含模型名。
    auto* results = window->findChild<QTabWidget*>("resultsTabs");
    auto* start = window->findChild<QWidget*>("startPage");
    QVERIFY(results != nullptr && start != nullptr);
    QCOMPARE(results->currentWidget(), start);
    QCOMPARE(window->windowTitle(), QStringLiteral("Ascend 因果建模工作台"));

    // 开始页 → 打开内置示例。
    window->findChild<QPushButton*>("startOpenExampleButton")->click();
    QVERIFY(wait_until([&] { return window->currentStatus().phase == ascend::session::Phase::runnable; }, 8000));
    QCOMPARE(results->indexOf(start), -1);
    QVERIFY(window->windowTitle().contains(QStringLiteral("确定性三变量")));

    // 查看菜单重新打开开始页；新建研究（创建新的因果系统）。
    window->findChild<QAction*>("startViewAction")->trigger();
    QVERIFY(wait_until([&] { return results->indexOf(start) >= 0; }, 4000));
    QTimer research_timer;
    connect(&research_timer, &QTimer::timeout, [&] {
        auto* dialog = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (dialog == nullptr || dialog->objectName() != QStringLiteral("newResearchDialog")) return;
        research_timer.stop();
        dialog->findChild<QLineEdit*>("researchNameEdit")->setText(QStringLiteral("起始研究"));
        dialog->accept();
    });
    research_timer.start(10);
    window->findChild<QPushButton*>("startNewResearchButton")->click();
    QVERIFY(wait_idle(window));
    auto* editor = window->findChild<QWidget*>("systemEditorPage");
    QVERIFY(editor != nullptr);
    QVERIFY(wait_until([&] { return results->indexOf(editor) >= 0 && results->currentWidget() == editor; }, 4000));
    QVERIFY(wait_until([&] { return window->currentStatus().model_name == "起始研究"; }, 4000));
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::editing);
    QCOMPARE(results->indexOf(start), -1);
    QVERIFY(window->windowTitle().contains(QStringLiteral("起始研究")));

    // 添加模块并保存因果系统文件（.aasm）。
    auto* definition_combo = window->findChild<QComboBox*>("systemDefinitionCombo");
    auto* add_button = window->findChild<QPushButton*>("systemAddButton");
    QVERIFY(definition_combo != nullptr && add_button != nullptr);
    definition_combo->setCurrentText(QStringLiteral("example.plant"));
    add_button->click();
    QVERIFY(wait_idle(window));
    auto* tree = window->findChild<QTreeWidget*>("systemTree");
    QVERIFY(tree != nullptr);
    QVERIFY(wait_until([&] { return tree->topLevelItemCount() == 1; }, 4000));

    const QString path = QDir::tempPath() + QStringLiteral("/ascend-workbench-system.aasm");
    QFile::remove(path);
    QTimer save_timer;
    connect(&save_timer, &QTimer::timeout, [&] { drive_file_dialog(path); });
    save_timer.start(10);
    window->findChild<QPushButton*>("systemSaveButton")->click();
    QVERIFY(wait_until([&] { return QFile::exists(path); }, 8000));
    save_timer.stop();
    QVERIFY(wait_until([&] {
        auto* label = window->findChild<QLabel*>("systemNameLabel");
        return label != nullptr && label->text().contains(QStringLiteral("ascend-workbench-system.aasm"));
    }, 4000));

    // 再次从开始页新建研究：选择打开已有因果系统文件。
    window->findChild<QAction*>("startViewAction")->trigger();
    QVERIFY(wait_until([&] { return results->indexOf(start) >= 0; }, 4000));
    bool research_done = false;
    QTimer open_timer;
    connect(&open_timer, &QTimer::timeout, [&] {
        auto* dialog = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (dialog != nullptr && dialog->objectName() == QStringLiteral("newResearchDialog")) {
            if (research_done) return;
            research_done = true;
            dialog->findChild<QLineEdit*>("researchNameEdit")->setText(QStringLiteral("打开系统研究"));
            dialog->findChild<QRadioButton*>("researchOpenSystemRadio")->setChecked(true);
            dialog->accept();
            return;
        }
        drive_file_dialog(path);
    });
    open_timer.start(10);
    window->findChild<QPushButton*>("startNewResearchButton")->click();
    QVERIFY(wait_idle(window));
    open_timer.stop();
    QVERIFY(wait_until([&] { return tree->topLevelItemCount() == 1; }, 4000));
    QVERIFY(wait_until([&] { return window->currentStatus().model_name == "打开系统研究"; }, 4000));
    QCOMPARE(results->currentWidget(), editor);
    QVERIFY(results->indexOf(start) == -1);
    QVERIFY(window->windowTitle().contains(QStringLiteral("打开系统研究")));

    QFile::remove(path);
}

void TestWorkbench::dockTitleDoubleClick() {
    // 双击各停靠面板标题栏切换浮动/停靠（中央工作区页签不属于停靠面板）。
    auto workbench = start_workbench(true);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    const QStringList names = {QStringLiteral("modulesDock"), QStringLiteral("configDock"),
                               QStringLiteral("stateDock"), QStringLiteral("diagnosticsDock")};
    for (const auto& name : names) {
        auto* dock = window->findChild<QDockWidget*>(name);
        QVERIFY2(dock != nullptr, qPrintable(name));
        if (!dock->isVisible()) dock->show();
        QTest::qWait(30);
        const int y = dock->style()->pixelMetric(QStyle::PM_TitleBarHeight) / 2;
        QTest::mouseDClick(dock, Qt::LeftButton, Qt::NoModifier, QPoint(dock->width() / 2, y));
        QTest::qWait(50);
        if (dock->isFloating()) {
            dock->setFloating(false);
            QTest::qWait(50);
        }
    }
}

void TestWorkbench::dockFloatDockStress() {
    // 停靠面板反复浮动/回停与显隐：覆盖停靠状态反复切换的稳定性。
    auto workbench = start_workbench(true);
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));
    const QStringList names = {QStringLiteral("modulesDock"), QStringLiteral("configDock"),
                               QStringLiteral("stateDock"), QStringLiteral("diagnosticsDock")};
    for (const auto& name : names) {
        auto* dock = window->findChild<QDockWidget*>(name);
        QVERIFY2(dock != nullptr, qPrintable(name));
        for (int round = 0; round < 2; ++round) {
            dock->setFloating(true);
            QTest::qWait(30);
            dock->setFloating(false);
            QTest::qWait(30);
            dock->hide();
            QTest::qWait(20);
            dock->show();
            QTest::qWait(30);
        }
    }
    const int y = window->findChild<QDockWidget*>("configDock")->style()->pixelMetric(QStyle::PM_TitleBarHeight) / 2;
    auto* config = window->findChild<QDockWidget*>("configDock");
    QTest::mouseDClick(config, Qt::LeftButton, Qt::NoModifier, QPoint(config->width() / 2, y));
    QTest::qWait(60);
    if (config->isFloating()) {
        config->setFloating(false);
        QTest::qWait(60);
    }
}

void TestWorkbench::captureScreenshot() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));
    window->resize(1400, 900);
    window->findChild<QSpinBox*>("stepsSpin")->setValue(2);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window));
    window->findChild<QPushButton*>("checkpointButton")->click();
    QVERIFY(wait_idle(window));
    QTimer dialog_timer;
    connect(&dialog_timer, &QTimer::timeout, [&dialog_timer] {
        auto* dialog = qobject_cast<QDialog*>(QApplication::activeModalWidget());
        if (dialog == nullptr) return;
        if (auto* table = dialog->findChild<QTableWidget*>("branchFieldTable")) {
            for (int row = 0; row < table->rowCount(); ++row) {
                if (table->item(row, 0)->text() == QStringLiteral("plant/state") &&
                    table->item(row, 1)->text() == QStringLiteral("x")) {
                    table->setCurrentCell(row, 0);
                    table->selectRow(row);
                }
            }
        }
        if (auto* edit = dialog->findChild<QLineEdit*>("branchValueEdit")) {
            edit->setText(QStringLiteral("10"));
        }
        dialog_timer.stop();
        dialog->accept();
    });
    dialog_timer.start(10);
    window->findChild<QPushButton*>("branchButton")->click();
    QVERIFY(wait_idle(window));
    window->findChild<QSpinBox*>("stepsSpin")->setValue(3);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window));
    // 配置区滚动到顶部，使截图包含初始配置与共同输入。
    if (auto* scroll = window->findChild<QScrollArea*>()) {
        if (scroll->verticalScrollBar() != nullptr) scroll->verticalScrollBar()->setValue(0);
    }
    auto* tabs = window->findChild<QTabWidget*>("resultsTabs");
    tabs->setCurrentWidget(window->findChild<QWidget*>("timelinePage"));
    QTest::qWait(80);
    const QString path =
        qEnvironmentVariable("ASCEND_WORKBENCH_SCREENSHOT", QStringLiteral("workbench-ui.png"));
    QVERIFY2(window->grab().save(path), qPrintable(QStringLiteral("cannot save %1").arg(path)));
    tabs->setCurrentWidget(window->findChild<QWidget*>("differencesPage"));
    QTest::qWait(50);
    const QString diff_path = path.chopped(4) + QStringLiteral("-diff.png");
    QVERIFY2(window->grab().save(diff_path),
             qPrintable(QStringLiteral("cannot save %1").arg(diff_path)));
    qInfo() << "screenshots saved to" << path << "and" << diff_path;
}

QTEST_MAIN(TestWorkbench)
#include "test_workbench_ui.moc"
