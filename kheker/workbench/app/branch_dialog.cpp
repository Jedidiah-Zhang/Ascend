#include "branch_dialog.hpp"

#include <QDialogButtonBox>
#include <QHeaderView>
#include <QItemSelectionModel>
#include <QLabel>
#include <QLineEdit>
#include <QPushButton>
#include <QTableWidget>
#include <QVBoxLayout>

namespace ascend::workbench {

BranchDialog::BranchDialog(std::int64_t frame, const std::vector<session::StateFieldView>& fields,
                           const session::ValueAdapter& integer_adapter, const UiTexts& texts, QWidget* parent)
    : QDialog(parent), fields_(fields), adapter_(integer_adapter), texts_(texts) {
    setWindowTitle(ui_text(texts_, "workbench.branch.dialog_title", "Build control and treated branches"));
    auto* layout = new QVBoxLayout(this);
    auto* title = new QLabel(
        ui_text(texts_, "workbench.branch.intro", "Shared checkpoint: frame %1. The control branch keeps the state; the treated branch applies a one-time change to the selected integer field.")
            .arg(frame),
        this);
    title->setWordWrap(true);
    layout->addWidget(title);

    table_ = new QTableWidget(this);
    table_->setObjectName("branchFieldTable");
    table_->setColumnCount(3);
    table_->setHorizontalHeaderLabels({ui_text(texts_, "workbench.table.module", "Module"), ui_text(texts_, "workbench.table.field", "Field"),
                                       ui_text(texts_, "workbench.table.current_value", "Current value")});
    table_->horizontalHeader()->setStretchLastSection(true);
    table_->verticalHeader()->setVisible(false);
    table_->setSelectionBehavior(QAbstractItemView::SelectRows);
    table_->setSelectionMode(QAbstractItemView::SingleSelection);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    int row = 0;
    int first_leaf = -1;
    int first_editable = -1;
    for (std::size_t index = 0; index < fields_.size(); ++index) {
        const auto& field = fields_[index];
        table_->insertRow(row);
        table_->setItem(row, 0, new QTableWidgetItem(QString::fromStdString(field.module)));
        table_->setItem(row, 1,
                        new QTableWidgetItem(field.field.empty() ? ui_text(texts_, "workbench.value.whole_state", "(whole state object)")
                                                                 : QString::fromStdString(field.field)));
        table_->setItem(row, 2, new QTableWidgetItem(QString::fromStdString(field.display)));
        if (!field.integer) {
            for (int column = 0; column < 3; ++column) {
                table_->item(row, column)->setFlags(Qt::NoItemFlags);
            }
        } else {
            if (first_editable < 0) first_editable = row;
            if (first_leaf < 0 && !field.field.empty()) first_leaf = row;
        }
        ++row;
    }
    const int preferred = first_leaf >= 0 ? first_leaf : first_editable;
    if (preferred >= 0) {
        table_->setCurrentCell(preferred, 0);
        table_->selectRow(preferred);
    }
    layout->addWidget(table_);

    auto* value_row = new QHBoxLayout;
    value_row->addWidget(new QLabel(ui_text(texts_, "workbench.branch.new_value", "New value:"), this));
    value_ = new QLineEdit(this);
    value_->setObjectName("branchValueEdit");
    value_row->addWidget(value_);
    layout->addLayout(value_row);

    hint_ = new QLabel(this);
    hint_->setWordWrap(true);
    layout->addWidget(hint_);

    buttons_ = new QDialogButtonBox(QDialogButtonBox::Ok | QDialogButtonBox::Cancel, this);
    layout->addWidget(buttons_);
    connect(buttons_, &QDialogButtonBox::rejected, this, &QDialog::reject);
    connect(buttons_, &QDialogButtonBox::accepted, this, &QDialog::accept);
    connect(table_, &QTableWidget::itemSelectionChanged, this, [this] {
        const int row = selected_row();
        if (row >= 0 && fields_[static_cast<std::size_t>(row)].integer) {
            value_->setText(QString::number(fields_[static_cast<std::size_t>(row)].value));
        }
        validate();
    });
    connect(value_, &QLineEdit::textChanged, this, &BranchDialog::validate);

    if (preferred >= 0) {
        value_->setText(QString::number(fields_[static_cast<std::size_t>(preferred)].value));
    } else {
        hint_->setText(ui_text(texts_, "workbench.branch.no_editable_field", "No editable integer field at the checkpoint; the treated branch cannot be built."));
    }
    validate();
}

std::vector<ascend::Intervention> BranchDialog::interventions() const {
    std::vector<ascend::Intervention> result;
    const int row = selected_row();
    if (row < 0 || !fields_[static_cast<std::size_t>(row)].integer) return result;
    TextRef error;
    const auto parsed = adapter_.parse(value_->text().toStdString(), error);
    if (!parsed.has_value()) return result;
    const auto& field = fields_[static_cast<std::size_t>(row)];
    result.push_back(ascend::Intervention{field.module, field.field,
                                          ascend::Config::integer(std::any_cast<std::int64_t>(*parsed))});
    return result;
}

int BranchDialog::selected_row() const {
    if (table_->selectionModel() != nullptr) {
        const auto rows = table_->selectionModel()->selectedRows();
        if (!rows.isEmpty()) return rows.front().row();
    }
    return table_->currentRow();
}

void BranchDialog::validate() {
    const int row = selected_row();
    if (row < 0 || !fields_[static_cast<std::size_t>(row)].integer) {
        hint_->setText(ui_text(texts_, "workbench.branch.select_field", "Select an editable integer field."));
        buttons_->button(QDialogButtonBox::Ok)->setEnabled(false);
        return;
    }
    const auto& field = fields_[static_cast<std::size_t>(row)];
    TextRef error;
    const auto parsed = adapter_.parse(value_->text().toStdString(), error);
    if (!parsed.has_value()) {
        hint_->setText(ui_text(texts_, "workbench.branch.invalid_value", "Invalid new value: %1")
                           .arg(QString::fromStdString(render_text(error, &texts_.catalog, texts_.locale))));
        buttons_->button(QDialogButtonBox::Ok)->setEnabled(false);
        return;
    }
    buttons_->button(QDialogButtonBox::Ok)->setEnabled(true);
    hint_->setText(ui_text(texts_, "workbench.branch.preview", "Will apply intervention: %1#%2 = %3 (previous %4)")
                       .arg(QString::fromStdString(field.module))
                       .arg(QString::fromStdString(field.field))
                       .arg(std::any_cast<std::int64_t>(*parsed))
                       .arg(QString::fromStdString(field.display)));
}

}  // namespace ascend::workbench
