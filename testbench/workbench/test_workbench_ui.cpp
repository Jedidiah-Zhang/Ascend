#include "config_editor.hpp"
#include "main_window.hpp"
#include "workbench.hpp"

#include <QAction>
#include <QApplication>
#include <QCheckBox>
#include <QComboBox>
#include <QCoreApplication>
#include <QDialog>
#include <QDockWidget>
#include <QElapsedTimer>
#include <QFile>
#include <QFileInfo>
#include <QGroupBox>
#include <QScrollArea>
#include <QScrollBar>
#include <QLabel>
#include <QLineEdit>
#include <QMenu>
#include <QMenuBar>
#include <QMessageBox>
#include <QPushButton>
#include <QSpinBox>
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

std::unique_ptr<Workbench> start_workbench() {
    auto workbench =
        std::make_unique<Workbench>(example_template(default_resource_paths()));
    workbench->start();
    return workbench;
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
};

void TestWorkbench::windowLoadsExample() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
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
    QCOMPARE(window->resultRowCount(), 1);

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

    auto* table = window->findChild<QTableWidget*>("resultTable");
    QCOMPARE(table->item(0, 1)->text(), QStringLiteral("0"));

    // 外部输入可选：未设置时按世界自身配置值（示例 a = 1）推进。
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.front().boundary, 1);
    QCOMPARE(window->resultRowCount(), 2);
    QCOMPARE(table->item(1, 1)->text(), QStringLiteral("1"));
    QCOMPARE(table->item(1, 2)->text(), QStringLiteral("0"));

    // 切换显示信号不改变演化结果。
    auto* signal_tree = window->findChild<QTreeWidget*>("signalTree");
    QVERIFY(signal_tree != nullptr && signal_tree->topLevelItemCount() >= 1);
    auto* signal_group = signal_tree->topLevelItem(0);
    QVERIFY(signal_group->childCount() >= 2);
    signal_group->child(1)->setCheckState(0, Qt::Unchecked);
    signal_group->child(1)->setCheckState(0, Qt::Checked);
    QTest::qWait(30);
    QCOMPARE(window->currentStatus().tracks.front().boundary, 1);
    QCOMPARE(window->resultRowCount(), 2);
    QCOMPARE(table->item(1, 1)->text(), QStringLiteral("1"));
    QCOMPARE(table->item(0, 1)->text(), QStringLiteral("0"));
}

void TestWorkbench::stopDuringRun() {
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));

    window->findChild<QSpinBox*>("stepsSpin")->setValue(10000);
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(window->busy());
    // 运行中切换标签页只请求只读快照，不应禁用停止按钮。
    window->findChild<QTabWidget*>("resultsTabs")->setCurrentIndex(2);
    QVERIFY(window->findChild<QPushButton*>("stopButton")->isEnabled());
    // 请求在完整的步骤边界生效，实际完成数小于请求步数。
    QElapsedTimer stop_timer;
    stop_timer.start();
    window->findChild<QPushButton*>("stopButton")->click();
    QVERIFY(wait_idle(window, 20000));
    qInfo() << "stop response elapsed" << stop_timer.elapsed() << "ms";
    QCOMPARE(window->currentStatus().phase, ascend::session::Phase::stopped);
    const auto boundary = window->currentStatus().tracks.front().boundary;
    QVERIFY(boundary >= 0 && boundary < 10000);
    QVERIFY(!window->findChild<QPushButton*>("stopButton")->isEnabled());
    QVERIFY(window->findChild<QPushButton*>("stepButton")->isEnabled());

    // 停止后可以继续运行。
    window->findChild<QPushButton*>("stepButton")->click();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->currentStatus().tracks.front().boundary, boundary + 1);
}

void TestWorkbench::runNThroughput() {
    // 界面侧上限路径：一次运行到 10000 边界并记录耗时与常驻内存（测量条件见测试文档）。
    auto workbench = start_workbench();
    MainWindow* window = workbench->window();
    QVERIFY(wait_idle(window));
    window->findChild<QSpinBox*>("stepsSpin")->setValue(10000);
    QElapsedTimer timer;
    timer.start();
    window->findChild<QPushButton*>("runButton")->click();
    QVERIFY(wait_idle(window, 120000));
    const auto elapsed = timer.elapsed();
    QCOMPARE(window->currentStatus().tracks.front().boundary, 10000);
    QCOMPARE(window->resultRowCount(), 10001);
    qInfo() << "ui runN 10000 elapsed" << elapsed << "ms";
    // 时间轴全量视图绘制耗时（可见区间渲染 + 按像素列降采样）。
    if (auto* tabs = window->findChild<QTabWidget*>("resultsTabs")) tabs->setCurrentIndex(1);
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
    QCOMPARE(window->currentStatus().tracks.front().boundary, 2);

    window->findChild<QPushButton*>("checkpointButton")->click();
    QVERIFY(wait_idle(window));
    QVERIFY(window->currentStatus().has_checkpoint);
    QCOMPARE(window->currentStatus().checkpoint_boundary, 2);

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
    QCOMPARE(status.tracks[0].boundary, 5);
    QCOMPARE(status.tracks[1].boundary, 5);

    // 结果表格：对照 (5,4,6)、干预 (13,12,22)。
    auto* table = window->findChild<QTableWidget*>("resultTable");
    QCOMPARE(table->columnCount(), 10);
    int row = -1;
    for (int index = 0; index < table->rowCount(); ++index) {
        if (table->item(index, 0)->text() == QStringLiteral("5")) row = index;
    }
    QVERIFY(row >= 0);
    QCOMPARE(table->item(row, 4)->text(), QStringLiteral("5"));
    QCOMPARE(table->item(row, 5)->text(), QStringLiteral("4"));
    QCOMPARE(table->item(row, 6)->text(), QStringLiteral("6"));
    QCOMPARE(table->item(row, 7)->text(), QStringLiteral("13"));
    QCOMPARE(table->item(row, 8)->text(), QStringLiteral("12"));
    QCOMPARE(table->item(row, 9)->text(), QStringLiteral("22"));

    // 共同边界差值：8、8、16。
    window->findChild<QTabWidget*>("resultsTabs")->setCurrentIndex(2);
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
    QCOMPARE(window->currentStatus().tracks[0].boundary, 2);
    QCOMPARE(window->currentStatus().tracks[0].samples, static_cast<std::size_t>(1));
    QCOMPARE(window->resultRowCount(), 3);
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
    const int rows = window->resultRowCount();
    step->trigger();
    QVERIFY(wait_idle(window));
    QCOMPARE(window->resultRowCount(), rows + 1);

    // 未实装项占位且禁用，并带后续阶段提示。
    QMenu* file_menu = menu_bar->actions().at(0)->menu();
    QAction* save = find_action(file_menu, QStringLiteral("保存实验…"));
    QVERIFY(save != nullptr);
    QVERIFY(!save->isEnabled());
    QVERIFY(!save->toolTip().isEmpty());

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

    // 停靠面板可拆分浮动并恢复停靠；配置为停靠面板，结果为主工作区。
    state_dock->setFloating(true);
    QVERIFY(state_dock->isFloating());
    state_dock->setFloating(false);
    QVERIFY(!state_dock->isFloating());
    QAction* config_toggle = find_action(view_menu, QStringLiteral("配置"));
    QAction* results_toggle = find_action(view_menu, QStringLiteral("结果"));
    QVERIFY(config_toggle != nullptr && results_toggle != nullptr);
    auto* config_dock = window->findChild<QDockWidget*>("configDock");
    auto* results_tabs = window->findChild<QTabWidget*>("resultsTabs");
    QVERIFY(config_dock != nullptr && results_tabs != nullptr);
    config_toggle->trigger();
    QVERIFY(config_dock->isHidden());
    QVERIFY(!results_tabs->isHidden());
    config_toggle->trigger();
    QVERIFY(!config_dock->isHidden());
    results_toggle->trigger();
    QVERIFY(results_tabs->isHidden());
    QVERIFY(!config_dock->isHidden());
    results_toggle->trigger();
    QVERIFY(!results_tabs->isHidden());

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
    tabs->setCurrentIndex(1);
    QTest::qWait(80);
    const QString path =
        qEnvironmentVariable("ASCEND_WORKBENCH_SCREENSHOT", QStringLiteral("workbench-ui.png"));
    QVERIFY2(window->grab().save(path), qPrintable(QStringLiteral("cannot save %1").arg(path)));
    tabs->setCurrentIndex(0);
    QTest::qWait(50);
    const QString table_path = path.chopped(4) + QStringLiteral("-table.png");
    QVERIFY2(window->grab().save(table_path),
             qPrintable(QStringLiteral("cannot save %1").arg(table_path)));
    qInfo() << "screenshots saved to" << path << "and" << table_path;
}

QTEST_MAIN(TestWorkbench)
#include "test_workbench_ui.moc"
