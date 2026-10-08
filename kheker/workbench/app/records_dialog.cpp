#include "records_dialog.hpp"

#include "ui_text.hpp"

#include <QAbstractItemView>
#include <QDialogButtonBox>
#include <QHeaderView>
#include <QTableWidget>
#include <QTableWidgetItem>
#include <QVBoxLayout>

#include <utility>

namespace ascend::workbench {

RecordsDialog::RecordsDialog(const UiTexts& texts, std::vector<ascend::session::RecordEntryView> records,
                             QWidget* parent)
    : QDialog(parent), records_(std::move(records)) {
    setObjectName("recordsDialog");
    setWindowTitle(ui_text(texts, "workbench.records.title", "Run records"));
    auto* layout = new QVBoxLayout(this);
    table_ = new QTableWidget(static_cast<int>(records_.size()), 3, this);
    table_->setObjectName("recordsTable");
    table_->setHorizontalHeaderLabels({ui_text(texts, "workbench.records.name", "Name"),
                                       ui_text(texts, "workbench.records.model", "Model"),
                                       ui_text(texts, "workbench.records.series", "Series")});
    table_->verticalHeader()->setVisible(false);
    table_->setSelectionBehavior(QAbstractItemView::SelectRows);
    table_->setSelectionMode(QAbstractItemView::SingleSelection);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->horizontalHeader()->setStretchLastSection(true);
    for (int row = 0; row < static_cast<int>(records_.size()); ++row) {
        const auto& entry = records_[static_cast<std::size_t>(row)];
        auto* name = new QTableWidgetItem(QString::fromStdString(entry.name));
        auto* model = new QTableWidgetItem(QString::fromStdString(entry.model));
        auto* series = new QTableWidgetItem(QString::number(static_cast<int>(entry.series)));
        if (!entry.readable) {
            const QString problem = entry.problem.has_value()
                                        ? QString::fromStdString(entry.problem->message)
                                        : ui_text(texts, "workbench.records.unreadable", "Unreadable record");
            name->setToolTip(problem);
            model->setToolTip(problem);
            name->setForeground(Qt::gray);
            model->setForeground(Qt::gray);
            series->setForeground(Qt::gray);
        }
        table_->setItem(row, 0, name);
        table_->setItem(row, 1, model);
        table_->setItem(row, 2, series);
    }
    if (!records_.empty()) table_->selectRow(0);
    layout->addWidget(table_);
    auto* buttons = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    buttons->setObjectName("recordsDialogButtons");
    connect(buttons, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(buttons, &QDialogButtonBox::rejected, this, &QDialog::reject);
    connect(table_, &QTableWidget::doubleClicked, this, &QDialog::accept);
    layout->addWidget(buttons);
    resize(560, 320);
}

QString RecordsDialog::selected_path() const {
    const int row = table_->currentRow();
    if (row < 0 || row >= static_cast<int>(records_.size())) return {};
    const auto& entry = records_[static_cast<std::size_t>(row)];
    if (!entry.readable) return {};
    return QString::fromStdString(entry.path);
}

}  // namespace ascend::workbench
