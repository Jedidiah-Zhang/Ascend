#include "table_models.hpp"

#include "ui_format.hpp"

#include <cstddef>

namespace ascend::workbench {

CheckTableModel::CheckTableModel(const UiTexts& texts, QObject* parent)
    : QAbstractTableModel(parent), texts_(texts) {}

void CheckTableModel::set_report(const session::CheckReport& report, bool has_check) {
    beginResetModel();
    report_ = report;
    has_check_ = has_check;
    endResetModel();
}

int CheckTableModel::rowCount(const QModelIndex& parent) const {
    if (parent.isValid()) return 0;
    return has_check_ ? static_cast<int>(report_.items.size()) : 0;
}

int CheckTableModel::columnCount(const QModelIndex& parent) const {
    return parent.isValid() ? 0 : 4;
}

QVariant CheckTableModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid() || role != Qt::DisplayRole) return {};
    if (index.row() < 0 || index.row() >= rowCount()) return {};
    const auto& item = report_.items[static_cast<std::size_t>(index.row())];
    switch (index.column()) {
        case 0: return from_utf8(item.area);
        case 1: return from_utf8(item.subject);
        case 2:
            return item.passed ? ui_text(texts_, "workbench.check.passed", "Passed")
                               : ui_text(texts_, "workbench.check.failed", "Failed");
        case 3: return from_utf8(item.note);
        default: return {};
    }
}

QVariant CheckTableModel::headerData(int section, Qt::Orientation orientation, int role) const {
    if (role != Qt::DisplayRole || orientation != Qt::Horizontal) return {};
    switch (section) {
        case 0: return ui_text(texts_, "workbench.table.area", "Area");
        case 1: return ui_text(texts_, "workbench.table.object", "Object");
        case 2: return ui_text(texts_, "workbench.table.result", "Result");
        case 3: return ui_text(texts_, "workbench.table.description", "Description");
        default: return {};
    }
}

RecordTableModel::RecordTableModel(const UiTexts& texts, QObject* parent)
    : QAbstractTableModel(parent), texts_(texts) {}

void RecordTableModel::set_record(const session::RecordView& record) {
    beginResetModel();
    record_ = record;
    endResetModel();
}

int RecordTableModel::rowCount(const QModelIndex& parent) const {
    if (parent.isValid()) return 0;
    return static_cast<int>(record_.input_list.size());
}

int RecordTableModel::columnCount(const QModelIndex& parent) const {
    return parent.isValid() ? 0 : 3;
}

QVariant RecordTableModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid() || role != Qt::DisplayRole) return {};
    if (index.row() < 0 || index.row() >= rowCount()) return {};
    const auto& input = record_.input_list[static_cast<std::size_t>(index.row())];
    switch (index.column()) {
        case 0: return QString::number(input.frame);
        case 1: return from_utf8(input.name);
        case 2: return from_utf8(input.value);
        default: return {};
    }
}

QVariant RecordTableModel::headerData(int section, Qt::Orientation orientation, int role) const {
    if (role != Qt::DisplayRole || orientation != Qt::Horizontal) return {};
    switch (section) {
        case 0: return ui_text(texts_, "workbench.table.driven_frame", "Frame before drive");
        case 1: return ui_text(texts_, "workbench.table.input", "Input");
        case 2: return ui_text(texts_, "workbench.table.value", "Value");
        default: return {};
    }
}

DiagnosticTableModel::DiagnosticTableModel(const UiTexts& texts, QObject* parent)
    : QAbstractTableModel(parent), texts_(texts) {}

void DiagnosticTableModel::set_entries(const std::vector<session::DiagnosticView>& diagnostics,
                                       const std::vector<QString>& resource_errors) {
    beginResetModel();
    diagnostics_ = diagnostics;
    resource_errors_ = resource_errors;
    endResetModel();
}

int DiagnosticTableModel::entry_index(const QModelIndex& index) const {
    if (!index.isValid()) return -1;
    const int row = index.row();
    if (row < 0 || row >= rowCount()) return -1;
    if (static_cast<std::size_t>(row) < resource_errors_.size()) return -1;
    return row - static_cast<int>(resource_errors_.size());
}

int DiagnosticTableModel::rowCount(const QModelIndex& parent) const {
    if (parent.isValid()) return 0;
    return static_cast<int>(resource_errors_.size() + diagnostics_.size());
}

int DiagnosticTableModel::columnCount(const QModelIndex& parent) const {
    return parent.isValid() ? 0 : 4;
}

QVariant DiagnosticTableModel::data(const QModelIndex& index, int role) const {
    if (!index.isValid()) return {};
    if (role == entry_role) return entry_index(index);
    if (role != Qt::DisplayRole) return {};
    const int row = index.row();
    if (row < 0 || row >= rowCount()) return {};
    if (static_cast<std::size_t>(row) < resource_errors_.size()) {
        switch (index.column()) {
            case 0: return ui_text(texts_, "workbench.error.invalid_i18n", "Text resources");
            case 3: return resource_errors_[static_cast<std::size_t>(row)];
            default: return {};
        }
    }
    const auto& diagnostic =
        diagnostics_[static_cast<std::size_t>(row) - resource_errors_.size()];
    switch (index.column()) {
        case 0: return code_text(texts_, diagnostic.code);
        case 1: return reference_text(diagnostic.target);
        case 2: return from_utf8(diagnostic.source);
        case 3: return from_utf8(diagnostic.message);
        default: return {};
    }
}

QVariant DiagnosticTableModel::headerData(int section, Qt::Orientation orientation, int role) const {
    if (role != Qt::DisplayRole || orientation != Qt::Horizontal) return {};
    switch (section) {
        case 0: return ui_text(texts_, "workbench.table.category", "Category");
        case 1: return ui_text(texts_, "workbench.table.target", "Target");
        case 2: return ui_text(texts_, "workbench.table.source", "Source");
        case 3: return ui_text(texts_, "workbench.table.message", "Message");
        default: return {};
    }
}

}  // namespace ascend::workbench
