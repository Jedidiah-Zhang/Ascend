#include "diff_panel.hpp"

#include "ui_format.hpp"

#include <QColor>
#include <QLabel>
#include <QTableWidget>
#include <QVBoxLayout>

namespace ascend::workbench {

DiffPanel::DiffPanel(const PanelContext& context, QWidget* parent) : QWidget(parent), context_(context) {
    setObjectName("differencesPage");
    auto* layout = new QVBoxLayout(this);
    diff_note_ = new QLabel(this);
    diff_note_->setWordWrap(true);
    layout->addWidget(diff_note_);
    diff_table_ = new QTableWidget(this);
    diff_table_->setObjectName("diffTable");
    diff_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    layout->addWidget(diff_table_, 1);
    // 逻辑帧差异默认不打开：出现运行/记录时由状态层的面板规则加入页签。
    hide();
}

void DiffPanel::render_comparison() {
    const auto& comparison = context_.workspace->state().comparison;
    const UiTexts& texts = *context_.texts;
    diff_table_->clear();
    diff_table_->setRowCount(0);
    diff_table_->setColumnCount(static_cast<int>(comparison.variables.size()) + 1);
    QStringList headers{ui_text(texts, "workbench.table.frame", "Frame")};
    for (const auto& name : comparison.variables) headers << from_utf8(name);
    diff_table_->setHorizontalHeaderLabels(headers);
    for (const auto& row : comparison.rows) {
        const int position = diff_table_->rowCount();
        diff_table_->insertRow(position);
        diff_table_->setItem(position, 0, new QTableWidgetItem(QString::number(row.frame)));
        for (std::size_t index = 0; index < row.cells.size(); ++index) {
            const auto& cell = row.cells[index];
            auto* item = new QTableWidgetItem(from_utf8(cell.difference));
            item->setToolTip(ui_text(texts, "workbench.diff.tooltip", "treated %1 − control %2").arg(from_utf8(cell.treated),
                                                                      from_utf8(cell.control)));
            if (!cell.comparable) item->setForeground(QColor(0xb4, 0x53, 0x09));
            diff_table_->setItem(position, static_cast<int>(index) + 1, item);
        }
    }
    QString note = ui_text(texts, "workbench.diff.note", "Differences are treated − control using exact integer arithmetic; only shared frames with samples on both branches are compared.");
    if (!comparison.unpaired.empty()) {
        QStringList frames;
        for (const auto frame : comparison.unpaired) frames << QString::number(frame);
        note += ui_text(texts, "workbench.diff.unpaired", " Boundaries with samples on one side only: %1 (unpaired; not used for differences).").arg(frames.join(", "));
    }
    diff_note_->setText(note);
}

void DiffPanel::clear() {
    diff_table_->clearContents();
    diff_table_->setRowCount(0);
    diff_note_->clear();
}

}  // namespace ascend::workbench
