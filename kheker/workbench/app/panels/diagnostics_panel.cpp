#include "diagnostics_panel.hpp"

#include "ui_format.hpp"

#include <QAbstractItemView>
#include <QFileInfo>
#include <QHeaderView>
#include <QItemSelectionModel>
#include <QLabel>
#include <QTabWidget>
#include <QTableView>
#include <QTextBrowser>
#include <QVBoxLayout>

namespace ascend::workbench {

namespace {

// 只读表视图的共同外观：隐藏垂直表头、最后一列拉伸、不可编辑、整行单选。
void configure_table(QTableView* view) {
    view->setEditTriggers(QAbstractItemView::NoEditTriggers);
    view->setSelectionBehavior(QAbstractItemView::SelectRows);
    view->setSelectionMode(QAbstractItemView::SingleSelection);
    view->verticalHeader()->setVisible(false);
    view->horizontalHeader()->setStretchLastSection(true);
}

}  // namespace

DiagnosticsPanel::DiagnosticsPanel(const UiTexts& texts, QWidget* parent)
    : QWidget(parent), texts_(texts) {
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);
    tabs_ = new QTabWidget(this);

    auto* diagnostics_page = new QWidget(tabs_);
    auto* diagnostics_layout = new QVBoxLayout(diagnostics_page);
    diagnostics_layout->setContentsMargins(0, 0, 0, 0);
    diagnostic_model_ = new DiagnosticTableModel(texts_, this);
    diagnostics_view_ = new QTableView(diagnostics_page);
    diagnostics_view_->setObjectName("diagnosticsTree");
    diagnostics_view_->setModel(diagnostic_model_);
    configure_table(diagnostics_view_);
    diagnostics_layout->addWidget(diagnostics_view_, 1);
    causes_view_ = new QTextBrowser(diagnostics_page);
    causes_view_->setObjectName("causesView");
    causes_view_->setMaximumHeight(140);
    diagnostics_layout->addWidget(causes_view_);
    tabs_->addTab(diagnostics_page, ui_text(texts_, "workbench.error.generic", "Diagnostic"));

    auto* check_page = new QWidget(tabs_);
    auto* check_layout = new QVBoxLayout(check_page);
    check_layout->setContentsMargins(0, 0, 0, 0);
    check_note_ = new QLabel(ui_text(texts_, "workbench.check.not_yet", "Not checked yet."), check_page);
    check_note_->setWordWrap(true);
    check_layout->addWidget(check_note_);
    check_model_ = new CheckTableModel(texts_, this);
    check_view_ = new QTableView(check_page);
    check_view_->setObjectName("checkTable");
    check_view_->setModel(check_model_);
    configure_table(check_view_);
    check_layout->addWidget(check_view_, 1);
    tabs_->addTab(check_page, ui_text(texts_, "workbench.action.check", "Check"));

    auto* record_page = new QWidget(tabs_);
    auto* record_layout = new QVBoxLayout(record_page);
    record_layout->setContentsMargins(0, 0, 0, 0);
    record_label_ = new QLabel(ui_text(texts_, "workbench.record.empty", "The record is empty."), record_page);
    record_layout->addWidget(record_label_);
    record_model_ = new RecordTableModel(texts_, this);
    record_view_ = new QTableView(record_page);
    record_view_->setObjectName("recordTable");
    record_view_->setModel(record_model_);
    configure_table(record_view_);
    record_layout->addWidget(record_view_, 1);
    tabs_->addTab(record_page, ui_text(texts_, "workbench.pane.record", "Recorded inputs"));

    layout->addWidget(tabs_);

    connect(diagnostics_view_->selectionModel(), &QItemSelectionModel::selectionChanged, this, [this] {
        const QModelIndexList selected = diagnostics_view_->selectionModel()->selectedRows();
        if (selected.isEmpty()) {
            causes_view_->clear();
            return;
        }
        update_causes(selected.front());
    });
    connect(tabs_, &QTabWidget::currentChanged, this, [this](int index) {
        if (index == 2) Q_EMIT record_requested();
    });
}

void DiagnosticsPanel::render_diagnostics(
    const std::vector<session::DiagnosticView>& diagnostics) {
    diagnostics_ = &diagnostics;
    refresh_diagnostics_model();
    if (!diagnostics.empty()) {
        // 新诊断到达：选中最近一条并切到诊断页。
        diagnostics_view_->selectRow(diagnostic_model_->rowCount() - 1);
        tabs_->setCurrentIndex(0);
    }
}

void DiagnosticsPanel::refresh_diagnostics_model() {
    static const std::vector<session::DiagnosticView> kEmpty;
    diagnostic_model_->set_entries(diagnostics_ != nullptr ? *diagnostics_ : kEmpty, resource_errors_);
}

void DiagnosticsPanel::update_causes(const QModelIndex& index) {
    const int entry = diagnostic_model_->entry_index(index);
    if (diagnostics_ == nullptr || entry < 0 || static_cast<std::size_t>(entry) >= diagnostics_->size()) {
        causes_view_->clear();
        return;
    }
    causes_view_->setHtml(causes_html(texts_, (*diagnostics_)[static_cast<std::size_t>(entry)], 0));
}

void DiagnosticsPanel::render_check(const session::CheckReport& report, bool has_check) {
    check_model_->set_report(report, has_check);
    if (!has_check) {
        check_note_->setText(ui_text(texts_, "workbench.check.not_yet", "Not checked yet."));
        return;
    }
    check_note_->setText(
        report.passed
            ? ui_text(texts_, "workbench.check.passed_note",
                      "Check passed: covers factory and assembly, experiment specification, type adapters and "
                      "implementation identifiers; passing is not a proof of implementation correctness or research "
                      "qualification.")
            : ui_text(texts_, "workbench.check.failed_note",
                      "Check failed: see the diagnostics and check items below."));
}

void DiagnosticsPanel::render_record(const session::RecordView& record, const QString& file_path) {
    const QString file = file_path.isEmpty() ? ui_text(texts_, "workbench.record.in_memory", "in-process only")
                                             : QFileInfo(file_path).fileName();
    record_label_->setText(ui_text(texts_, "workbench.record.summary",
                                   "Branches: %1 · actual driven inputs: %2 · advance failures: %3 · file: %4")
                               .arg(record.branches)
                               .arg(record.inputs)
                               .arg(record.failures)
                               .arg(file));
    record_model_->set_record(record);
}

void DiagnosticsPanel::append_resource_error(const QString& message) {
    resource_errors_.push_back(message);
    refresh_diagnostics_model();
}

void DiagnosticsPanel::show_check_tab() { tabs_->setCurrentIndex(1); }

}  // namespace ascend::workbench
