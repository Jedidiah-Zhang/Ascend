#include "state_panel.hpp"

#include "ui_format.hpp"

#include <QComboBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QTabWidget>
#include <QTableWidget>
#include <QTextBrowser>
#include <QTreeWidget>
#include <QVBoxLayout>

namespace ascend::workbench {

StatePanel::StatePanel(const PanelContext& context, QWidget* parent)
    : QWidget(parent), context_(context) {
    const UiTexts& texts = *context_.texts;
    setObjectName("statePanel");
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);

    tabs_ = new QTabWidget(this);
    tabs_->setObjectName("rightPane");
    tabs_->setMinimumWidth(300);

    details_view_ = new QTextBrowser(tabs_);
    details_view_->setObjectName("detailsView");
    details_view_->setHtml(ui_text(texts, "workbench.detail.placeholder", "<p style='color:#666'>Select a module, public item or connection on the left to see details.</p>"));
    tabs_->addTab(details_view_, ui_text(texts, "workbench.pane.details", "Item details"));

    auto* state_page = new QWidget(tabs_);
    auto* state_layout = new QVBoxLayout(state_page);
    checkpoint_label_ = new QLabel(ui_text(texts, "workbench.state.no_checkpoint", "No checkpoint yet"), state_page);
    checkpoint_label_->setObjectName("checkpointLabel");
    checkpoint_label_->setWordWrap(true);
    state_layout->addWidget(checkpoint_label_);
    state_table_ = new QTableWidget(state_page);
    state_table_->setObjectName("stateTable");
    state_table_->setColumnCount(4);
    state_table_->setHorizontalHeaderLabels({ui_text(texts, "workbench.table.module", "Module"), ui_text(texts, "workbench.table.field", "Field"),
                                             ui_text(texts, "workbench.table.current_value", "Current value"), ui_text(texts, "workbench.table.editable", "Editable")});
    state_table_->horizontalHeader()->setStretchLastSection(true);
    state_table_->verticalHeader()->setVisible(false);
    state_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    state_layout->addWidget(state_table_, 1);
    tabs_->addTab(state_page, ui_text(texts, "workbench.pane.state", "State and intervention"));

    auto* sample_page = new QWidget(tabs_);
    auto* sample_layout = new QVBoxLayout(sample_page);
    auto* sample_bar = new QHBoxLayout;
    sample_bar->addWidget(new QLabel(ui_text(texts, "workbench.label.series", "Series:"), sample_page));
    sample_series_combo_ = new QComboBox(sample_page);
    sample_series_combo_->setObjectName("sampleSeriesCombo");
    sample_bar->addWidget(sample_series_combo_);
    sample_layout->addLayout(sample_bar);
    sample_label_ = new QLabel(ui_text(texts, "workbench.sample.select_hint", "Select a frame in the result table."), sample_page);
    sample_layout->addWidget(sample_label_);
    sample_tree_ = new QTreeWidget(sample_page);
    sample_tree_->setObjectName("sampleTree");
    sample_tree_->setColumnCount(3);
    sample_tree_->setHeaderLabels({ui_text(texts, "workbench.table.object", "Object"), ui_text(texts, "workbench.table.field", "Field"), ui_text(texts, "workbench.table.value", "Value")});
    sample_tree_->header()->setStretchLastSection(true);
    sample_layout->addWidget(sample_tree_, 1);
    tabs_->addTab(sample_page, ui_text(texts, "workbench.pane.truth", "Truth and observations"));

    // 切换序列只发信号，是否请求该序列详情／最新采样由外壳按选中帧决定。
    connect(sample_series_combo_, &QComboBox::currentIndexChanged, this, [this] { Q_EMIT sample_series_changed(); });

    layout->addWidget(tabs_);
}

void StatePanel::render_state_fields() {
    const UiTexts& texts = *context_.texts;
    const WorkspaceState& state = context_.workspace->state();
    state_table_->setRowCount(0);
    if (state.status.has_checkpoint) {
        QString text = ui_text(texts, "workbench.state.checkpoint_hint", "Checkpoint frame %1; interventions target stateful modules at the checkpoint (the first version supports integer fields).")
                           .arg(state.status.checkpoint_frame);
        for (const auto& track : state.status.tracks) {
            for (const auto& intervention : track.interventions) {
                text += ui_text(texts, "workbench.intervention.line", "\nBranch \"%1\" intervention: %2#%3 changed from %4 to %5")
                            .arg(from_utf8(track.label), from_utf8(intervention.module),
                                 from_utf8(intervention.field), from_utf8(intervention.previous),
                                 from_utf8(intervention.replacement));
            }
        }
        checkpoint_label_->setText(text);
    } else {
        checkpoint_label_->setText(ui_text(texts, "workbench.state.no_checkpoint_period", "No checkpoint yet."));
    }
    for (const auto& field : state.model.state_fields) {
        const int row = state_table_->rowCount();
        state_table_->insertRow(row);
        state_table_->setItem(row, 0, new QTableWidgetItem(from_utf8(field.module)));
        state_table_->setItem(row, 1, new QTableWidgetItem(field.field.empty()
                                                               ? ui_text(texts, "workbench.value.whole_state", "(whole state object)")
                                                               : from_utf8(field.field)));
        state_table_->setItem(row, 2, new QTableWidgetItem(from_utf8(field.display)));
        state_table_->setItem(row, 3, new QTableWidgetItem(field.integer ? ui_text(texts, "workbench.value.yes", "Yes")
                                                                         : ui_text(texts, "workbench.value.no", "No")));
    }
}

void StatePanel::render_sample_selectors(const std::vector<std::string>& labels) {
    const int previous = sample_series_combo_->currentIndex();
    sample_series_combo_->blockSignals(true);
    sample_series_combo_->clear();
    for (const auto& label : labels) sample_series_combo_->addItem(from_utf8(label));
    if (previous >= 0 && previous < sample_series_combo_->count()) sample_series_combo_->setCurrentIndex(previous);
    sample_series_combo_->blockSignals(false);
    sample_tree_->clear();
}

void StatePanel::render_sample_detail(const session::SampleDetailView& detail, std::int64_t) {
    const UiTexts& texts = *context_.texts;
    sample_tree_->clear();
    if (!detail.found) {
        sample_label_->setText(ui_text(texts, "workbench.sample.none", "No sample to display at this frame."));
        return;
    }
    sample_label_->setText(ui_text(texts, "workbench.sample.caption", "Research truth and declared observations at frame %1 (shown separately)")
                               .arg(detail.frame));
    auto* truth = new QTreeWidgetItem(sample_tree_);
    truth->setText(0, ui_text(texts, "workbench.sample.truth", "Research truth (complete state)"));
    for (const auto& module : detail.truth) {
        auto* node = new QTreeWidgetItem(truth);
        node->setText(0, from_utf8(module.path));
        node->setText(1, module.stateless ? ui_text(texts, "workbench.sample.stateless", "stateless")
                                          : ui_text(texts, "workbench.sample.contract", "contract %1").arg(from_utf8(module.contract)));
        for (const auto& field : module.fields) {
            auto* leaf = new QTreeWidgetItem(node);
            leaf->setText(1, from_utf8(field.first));
            leaf->setText(2, from_utf8(field.second));
        }
        if (module.stateless) {
            auto* leaf = new QTreeWidgetItem(node);
            leaf->setText(1, ui_text(texts, "workbench.sample.no_own_state", "(no own state)"));
        }
    }
    auto* observations = new QTreeWidgetItem(sample_tree_);
    observations->setText(0, ui_text(texts, "workbench.sample.observations", "Declared observations (visible to the subject)"));
    for (const auto& entry : detail.observations) {
        auto* leaf = new QTreeWidgetItem(observations);
        leaf->setText(1, from_utf8(entry.first));
        leaf->setText(2, from_utf8(entry.second.display));
    }
    sample_tree_->expandAll();
}

void StatePanel::show_details_html(const QString& html) { details_view_->setHtml(html); }

void StatePanel::clear_transient() {
    sample_tree_->clear();
    sample_label_->setText(ui_text(*context_.texts, "workbench.sample.select_hint", "Select a logical frame in the timeline."));
    details_view_->setHtml(ui_text(*context_.texts, "workbench.detail.placeholder",
                                   "<p style='color:#666'>Select a module, public item or connection on the left to see details.</p>"));
}

int StatePanel::selected_series() const { return sample_series_combo_->currentIndex(); }

}  // namespace ascend::workbench
