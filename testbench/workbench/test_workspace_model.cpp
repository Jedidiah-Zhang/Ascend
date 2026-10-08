// 工作区状态层无控件单测：身份通知、运行可用性与控件条件由状态派生。
// 用例列表与设计目的见 docs/研究平台/因果建模工作台/测试.md。

#include "workspace/workspace_model.hpp"

#include <QTest>

#include <cstddef>

namespace session = ascend::session;
using ascend::workbench::ControlsView;
using ascend::workbench::IdentityIntent;
using ascend::workbench::WorkspaceChange;
using ascend::workbench::WorkspaceModel;

class TestWorkspaceModel : public QObject {
    Q_OBJECT

private Q_SLOTS:
    void identity_notice();
    void busy_controls();
    void results_availability();
    void results_panels();
    void samples_append();
    void observations_changed();
    void comparison_stored();
    void check_store();
    void diagnostics_log();
    void record_store();
    void controls_matrix();
    void packages_controls();
};

void TestWorkspaceModel::identity_notice() {
    WorkspaceModel model;
    int count = 0;
    WorkspaceChange last = WorkspaceChange::none;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange changed) {
        ++count;
        last = changed;
    });
    model.beginIdentity(IdentityIntent::new_research);
    QCOMPARE(count, 1);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::identity));

    // 身份文件与未保存标记语义：打开系统设置装配文件并清空研究与记录文件；
    // 打开项目设置研究文件与装配文件；打开记录设置当前记录；打开示例清空全部。
    model.set_system_dirty(true);
    model.set_research_dirty(true);
    model.beginIdentity(IdentityIntent::open_system, "system.aasm");
    QCOMPARE(count, 2);
    QCOMPARE(QString::fromStdString(model.state().system_file), QStringLiteral("system.aasm"));
    QVERIFY(!model.state().system_dirty);
    QVERIFY(model.state().research_file.empty());
    QVERIFY(model.state().record_file.empty());
    QVERIFY(model.state().research_dirty);  // 研究标记保持既有语义

    model.beginIdentity(IdentityIntent::open_project, "research.aexp", "assembly.aasm");
    QCOMPARE(QString::fromStdString(model.state().research_file), QStringLiteral("research.aexp"));
    QCOMPARE(QString::fromStdString(model.state().system_file), QStringLiteral("assembly.aasm"));
    QVERIFY(!model.state().research_dirty);
    QVERIFY(!model.state().system_dirty);
    QVERIFY(model.state().record_file.empty());

    model.beginIdentity(IdentityIntent::open_record, "run.arec");
    QCOMPARE(QString::fromStdString(model.state().record_file), QStringLiteral("run.arec"));
    QCOMPARE(QString::fromStdString(model.state().research_file), QStringLiteral("research.aexp"));

    model.beginIdentity(IdentityIntent::open_example);
    QVERIFY(model.state().system_file.empty());
    QVERIFY(model.state().research_file.empty());
    QVERIFY(model.state().record_file.empty());

    const int before_notes = count;
    model.note_system_saved("saved.aasm");
    model.note_research_saved("saved.aexp");
    model.note_record_saved("saved.arec");
    model.set_system_dirty(true);
    model.set_research_dirty(true);
    QCOMPARE(count, before_notes);  // 静默簿记不产生通知
    QCOMPARE(QString::fromStdString(model.state().system_file), QStringLiteral("saved.aasm"));
    QCOMPARE(QString::fromStdString(model.state().research_file), QStringLiteral("saved.aexp"));
    QCOMPARE(QString::fromStdString(model.state().record_file), QStringLiteral("saved.arec"));
    QVERIFY(model.state().system_dirty);
    QVERIFY(model.state().research_dirty);
}

void TestWorkspaceModel::busy_controls() {
    WorkspaceModel model;
    session::Status status;
    status.phase = session::Phase::runnable;
    model.applyStatus(status);
    QVERIFY(model.controls().runnable);
    model.applyBusy(true);
    QVERIFY(!model.controls().runnable);
    QVERIFY(!model.controls().editable);
    QVERIFY(!model.controls().can_open_file);
    model.applyBusy(false);
    QVERIFY(model.controls().runnable);
}

void TestWorkspaceModel::results_availability() {
    WorkspaceModel model;
    QVERIFY(!model.state().results_available);
    QVERIFY(!model.controls().can_replay);
    session::TrackTraceView trace;
    trace.label = "source";
    model.applyTracesReset({trace});
    QVERIFY(model.state().results_available);
    QCOMPARE(model.state().series.size(), std::size_t{1});
    QCOMPARE(model.state().series[0].label, std::string("source"));
    QVERIFY(model.controls().can_replay);
    model.applyTracesReset({});
    QVERIFY(!model.state().results_available);
    QVERIFY(model.state().series.empty());
    QVERIFY(!model.controls().can_replay);
}

void TestWorkspaceModel::samples_append() {
    WorkspaceModel model;
    session::TrackTraceView trace;
    trace.label = "source";
    model.applyTracesReset({trace});
    WorkspaceChange last = WorkspaceChange::none;
    int count = 0;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange changed) {
        last = changed;
        ++count;
    });
    session::SampleView sample;
    sample.frame = 3;
    session::StepEvent event;
    event.kind = session::StepEvent::Kind::completed;
    event.frame = 3;
    model.applySamplesAppended(0, {sample}, {event});
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::samples));
    QCOMPARE(model.state().series[0].samples.size(), std::size_t{1});
    QCOMPARE(model.state().series[0].events.size(), std::size_t{1});
    // 越界序列不触发更新。
    const int before = count;
    model.applySamplesAppended(5, {sample}, {});
    QCOMPARE(count, before);
}

void TestWorkspaceModel::observations_changed() {
    WorkspaceModel model;
    WorkspaceChange last = WorkspaceChange::none;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange changed) { last = changed; });
    ascend::workbench::ModelSnapshot snapshot;
    snapshot.observations = {"plant/x"};
    model.applyModel(snapshot);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::observations));
    model.applyModel(snapshot);  // 观测不变：不发观测标志
    QVERIFY(!ascend::workbench::has(last, WorkspaceChange::observations));
    snapshot.observations.push_back("plant/y");
    model.applyModel(snapshot);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::observations));
    QCOMPARE(model.state().model.observations.size(), std::size_t{2});
}

void TestWorkspaceModel::comparison_stored() {
    WorkspaceModel model;
    WorkspaceChange last = WorkspaceChange::none;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange changed) { last = changed; });
    session::ComparisonView comparison;
    comparison.variables = {"x"};
    session::ComparisonView::Row row;
    row.frame = 2;
    session::DiffCellView cell;
    cell.variable = "x";
    cell.difference = "8";
    cell.comparable = true;
    cell.integer = 8;
    row.cells.push_back(cell);
    comparison.rows.push_back(row);
    model.applyComparison(comparison);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::comparison));
    QCOMPARE(model.state().comparison.rows.size(), std::size_t{1});
    QCOMPARE(model.state().comparison.rows[0].cells[0].difference, std::string("8"));
}

void TestWorkspaceModel::results_panels() {
    WorkspaceModel model;
    session::Status status;
    status.phase = session::Phase::editing;
    model.applyStatus(status);
    session::TrackTraceView trace;
    model.applyTracesReset({trace});
    // 出现运行/记录：打开时间轴与逻辑帧差异并首次置前。
    QVERIFY(ascend::workbench::panel_open(model.state().panels, ascend::workbench::panel_id::timeline));
    QVERIFY(ascend::workbench::panel_open(model.state().panels, ascend::workbench::panel_id::differences));
    QCOMPARE(QString::fromStdString(model.state().panels.central_focus), QStringLiteral("timeline"));
    // 再次更新：页签已在，不打断当前页。
    model.clearCentralFocus();
    model.applyTracesReset({trace, trace});
    QVERIFY(model.state().panels.central_focus.empty());
    // 手动关闭后再次出现运行/记录：重新打开并置前（出现即打开）。
    model.noteCentralClosed(ascend::workbench::panel_id::timeline);
    QVERIFY(!ascend::workbench::panel_open(model.state().panels, ascend::workbench::panel_id::timeline));
    model.clearCentralFocus();
    model.applyTracesReset({trace});
    QVERIFY(ascend::workbench::panel_open(model.state().panels, ascend::workbench::panel_id::timeline));
    QCOMPARE(QString::fromStdString(model.state().panels.central_focus), QStringLiteral("timeline"));
    // 编辑态空轨迹：关闭结果页签。
    model.applyTracesReset({});
    QVERIFY(!ascend::workbench::panel_open(model.state().panels, ascend::workbench::panel_id::timeline));
    QVERIFY(!ascend::workbench::panel_open(model.state().panels, ascend::workbench::panel_id::differences));
}

void TestWorkspaceModel::check_store() {
    WorkspaceModel model;
    WorkspaceChange last = WorkspaceChange::none;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange changed) { last = changed; });
    session::CheckReport report;
    report.passed = true;
    session::CheckItemView item;
    item.area = "factory";
    item.subject = "plant";
    item.passed = true;
    report.items.push_back(item);
    model.applyCheck(report);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::check));
    QVERIFY(model.state().has_check);
    QCOMPARE(model.state().check.items.size(), std::size_t{1});
    model.beginIdentity(IdentityIntent::open_example);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::identity));
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::check));
    QVERIFY(!model.state().has_check);
    QVERIFY(model.state().check.items.empty());
}

void TestWorkspaceModel::diagnostics_log() {
    WorkspaceModel model;
    int count = 0;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange) { ++count; });
    model.appendDiagnostics({});
    QCOMPARE(count, 0);  // 空批次不通知
    std::vector<session::DiagnosticView> batch;
    for (int index = 0; index < 205; ++index) {
        session::DiagnosticView diagnostic;
        diagnostic.message = "diag-" + std::to_string(index);
        batch.push_back(diagnostic);
    }
    model.appendDiagnostics(batch);
    QCOMPARE(count, 1);
    QCOMPARE(model.state().diagnostics.size(), std::size_t{200});
    QCOMPARE(model.state().diagnostics.front().message, std::string("diag-5"));
    QCOMPARE(model.state().diagnostics.back().message, std::string("diag-204"));
}

void TestWorkspaceModel::record_store() {
    WorkspaceModel model;
    WorkspaceChange last = WorkspaceChange::none;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange changed) { last = changed; });
    session::RecordView record;
    record.inputs = 2;
    session::InputRecordView input;
    input.frame = 1;
    input.name = "a";
    input.value = "2";
    record.input_list.push_back(input);
    model.applyRecord(record);
    QVERIFY(ascend::workbench::has(last, WorkspaceChange::record));
    QCOMPARE(model.state().record.inputs, std::size_t{2});
    QCOMPARE(model.state().record.input_list.size(), std::size_t{1});
}

void TestWorkspaceModel::controls_matrix() {
    WorkspaceModel model;
    // 空会话：只有打开文件类入口可用。
    QVERIFY(!model.controls().editable);
    QVERIFY(model.controls().can_open_file);

    session::Status status;
    status.phase = session::Phase::editing;
    model.applyStatus(status);
    QVERIFY(model.controls().editable);
    QVERIFY(!model.controls().runnable);
    QVERIFY(!model.controls().can_save_project);  // 未打开项目

    status.phase = session::Phase::runnable;
    status.project_directory = "/tmp/project";
    model.applyStatus(status);
    QVERIFY(model.controls().can_save_project);
    session::TrackTraceView trace;
    model.applyTracesReset({trace});  // 单条序列：可创建检查点
    QVERIFY(model.controls().can_checkpoint);
    QVERIFY(!model.controls().can_branch);  // 尚无检查点
    status.has_checkpoint = true;
    model.applyStatus(status);
    QVERIFY(model.controls().can_branch);

    model.applyTracesReset({trace, trace, trace});  // 分支后三条序列
    status.branches = session::max_branches;
    model.applyStatus(status);
    QVERIFY(!model.controls().can_checkpoint);
    QVERIFY(!model.controls().can_branch);
    QVERIFY(model.controls().can_reset_branches);
}

void TestWorkspaceModel::packages_controls() {
    WorkspaceModel model;
    QVERIFY(!model.controls().can_unload_package);
    session::ModulePackageView package;
    package.definition = "library.source";
    package.version = "1.0";
    package.stateless = true;
    package.declarations = 1;
    int notices = 0;
    QObject::connect(&model, &WorkspaceModel::changed, [&](WorkspaceChange) { ++notices; });
    model.applyPackages({package});
    QCOMPARE(notices, 1);
    QCOMPARE(model.state().packages.size(), std::size_t{1});
    QCOMPARE(model.state().packages[0].definition, std::string("library.source"));
    QVERIFY(model.controls().can_unload_package);
    model.applyPackages({package});  // 内容不变：不发通知
    QCOMPARE(notices, 1);
    model.applyBusy(true);
    QVERIFY(!model.controls().can_unload_package);
}

QTEST_GUILESS_MAIN(TestWorkspaceModel)
#include "test_workspace_model.moc"
