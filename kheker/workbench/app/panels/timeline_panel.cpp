#include "timeline_panel.hpp"

#include "ui_format.hpp"
#include "waveform_widget.hpp"

#include <QCheckBox>
#include <QColor>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QSignalBlocker>
#include <QSplitter>
#include <QTableWidget>
#include <QTreeWidget>
#include <QVBoxLayout>
#include <QVector>

#include <limits>
#include <optional>

namespace ascend::workbench {
namespace {

QColor series_color(int index) {
    static const QColor palette[] = {QColor(0x1f, 0x77, 0xb4), QColor(0xd6, 0x27, 0x28),
                                     QColor(0x2c, 0xa0, 0x2c)};
    return palette[index % 3];
}

}  // namespace

TimelinePanel::TimelinePanel(const PanelContext& context, QWidget* parent) : QWidget(parent), context_(context) {
    const UiTexts& texts = *context_.texts;
    setObjectName("timelinePage");
    auto* layout = new QVBoxLayout(this);
    auto* bar = new QHBoxLayout;
    series_checks_host_ = new QWidget(this);
    auto* checks_layout = new QHBoxLayout(series_checks_host_);
    checks_layout->setContentsMargins(0, 0, 0, 0);
    series_checks_host_->setLayout(checks_layout);
    bar->addWidget(series_checks_host_);
    bar->addStretch(1);
    auto* hint = new QLabel(ui_text(texts, "workbench.waveform.hint",
                                    "Wheel to zoom · drag to pan · click to place cursor A · Shift+click for "
                                    "cursor B · double-click to fit"),
                            this);
    hint->setStyleSheet(QStringLiteral("color: #666;"));
    bar->addWidget(hint);
    layout->addLayout(bar);

    auto* splitter = new QSplitter(Qt::Horizontal, this);
    signal_tree_ = new QTreeWidget(splitter);
    signal_tree_->setObjectName("signalTree");
    signal_tree_->setHeaderHidden(true);
    signal_tree_->setMinimumWidth(150);
    signal_tree_->setMaximumWidth(240);
    splitter->addWidget(signal_tree_);
    waveform_ = new WaveformWidget(&texts, splitter);
    waveform_->setObjectName("waveform");
    splitter->addWidget(waveform_);
    splitter->setStretchFactor(1, 1);
    layout->addWidget(splitter, 1);

    cursor_table_ = new QTableWidget(this);
    cursor_table_->setObjectName("cursorTable");
    cursor_table_->setColumnCount(5);
    cursor_table_->setHorizontalHeaderLabels({ui_text(texts, "workbench.waveform.signal", "Signal"),
                                              ui_text(texts, "workbench.waveform.series", "Series"),
                                              ui_text(texts, "workbench.waveform.cursor_a", "A"),
                                              ui_text(texts, "workbench.waveform.cursor_b", "B"),
                                              ui_text(texts, "workbench.waveform.delta", "Δ")});
    cursor_table_->horizontalHeader()->setStretchLastSection(true);
    cursor_table_->verticalHeader()->setVisible(false);
    cursor_table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    cursor_table_->setMaximumHeight(150);
    layout->addWidget(cursor_table_);
    // 时间轴默认不打开：出现运行/记录时由状态层的面板规则加入页签。
    hide();

    connect(signal_tree_, &QTreeWidget::itemChanged, this, [this] { render_waveform(); });
    connect(waveform_, &WaveformWidget::cursorsChanged, this, [this] { update_cursor_table(); });
    connect(waveform_, &WaveformWidget::frameSelected, this,
            [this](std::int64_t frame) { context_.request_sample_detail(0, frame); });
}

void TimelinePanel::render_waveform() {
    if (waveform_rebuilding_) return;
    waveform_rebuilding_ = true;

    const WorkspaceState& state = context_.workspace->state();
    const UiTexts& texts = *context_.texts;

    bool rebuild_checks = series_checks_.size() != state.series.size() ||
                          series_checks_host_->findChildren<QCheckBox*>().size() != static_cast<int>(state.series.size());
    if (!rebuild_checks) {
        for (std::size_t index = 0; index < state.series.size(); ++index) {
            if (series_checks_[index]->text() != from_utf8(state.series[index].label)) {
                rebuild_checks = true;
                break;
            }
        }
    }
    if (rebuild_checks) {
        auto* layout = series_checks_host_->layout();
        while (auto* item = layout->takeAt(0)) {
            delete item->widget();
            delete item;
        }
        series_checks_.clear();
        for (std::size_t index = 0; index < state.series.size(); ++index) {
            auto* check = new QCheckBox(from_utf8(state.series[index].label), series_checks_host_);
            check->setObjectName("seriesCheck_" + QString::number(index));
            check->setChecked(true);
            connect(check, &QCheckBox::toggled, this, [this] { render_waveform(); });
            layout->addWidget(check);
            series_checks_.push_back(check);
        }
    }

    // 信号树：观测组与差值组按当前规格观测重建（名称或数量变化时），无观测时不保留。
    const auto items_match = [&state](const QTreeWidgetItem* group) {
        if (group == nullptr) return false;
        if (static_cast<std::size_t>(group->childCount()) != state.model.observations.size()) return false;
        for (int index = 0; index < group->childCount(); ++index) {
            if (group->child(index)->text(0) != from_utf8(state.model.observations[static_cast<std::size_t>(index)])) {
                return false;
            }
        }
        return true;
    };
    const auto fill_group = [&state](QTreeWidgetItem* group, int difference) {
        for (std::size_t index = 0; index < state.model.observations.size(); ++index) {
            auto* item = new QTreeWidgetItem(group);
            item->setText(0, from_utf8(state.model.observations[index]));
            item->setData(0, Qt::UserRole, static_cast<int>(index));
            item->setData(0, Qt::UserRole + 1, difference);
            item->setFlags(Qt::ItemIsUserCheckable | Qt::ItemIsEnabled | Qt::ItemIsSelectable);
            item->setCheckState(0, Qt::Checked);
        }
    };
    if (observation_group_ != nullptr && !items_match(observation_group_)) {
        const QSignalBlocker blocker(signal_tree_);
        delete observation_group_;
        observation_group_ = nullptr;
    }
    if (observation_group_ == nullptr && !state.model.observations.empty()) {
        const QSignalBlocker blocker(signal_tree_);
        observation_group_ = new QTreeWidgetItem(signal_tree_);
        observation_group_->setText(0, ui_text(texts, "workbench.waveform.observations", "Observations"));
        observation_group_->setFlags(Qt::ItemIsEnabled);
        observation_group_->setExpanded(true);
        fill_group(observation_group_, 0);
        signal_tree_->expandAll();
    }
    const bool want_diff = state.series.size() > 2 && !state.model.observations.empty();
    if (diff_group_ != nullptr && (!want_diff || !items_match(diff_group_))) {
        const QSignalBlocker blocker(signal_tree_);
        delete diff_group_;
        diff_group_ = nullptr;
    }
    if (want_diff && diff_group_ == nullptr) {
        const QSignalBlocker blocker(signal_tree_);
        diff_group_ = new QTreeWidgetItem(signal_tree_);
        diff_group_->setText(0, ui_text(texts, "workbench.waveform.diff_group", "Difference (treated − control)"));
        diff_group_->setFlags(Qt::ItemIsEnabled);
        diff_group_->setExpanded(true);
        fill_group(diff_group_, 1);
        signal_tree_->expandAll();
    }

    QVector<WaveformWidget::Signal> rows;
    for (const auto& selection : checked_signals()) {
        WaveformWidget::Signal signal;
        if (selection.difference) {
            signal.name = ui_text(texts, "workbench.waveform.diff_name", "%1 (difference)")
                              .arg(selection.name);
            WaveformWidget::Series line;
            line.label = ui_text(texts, "workbench.waveform.diff_series", "Treated − control");
            line.color = QColor(0x8e, 0x44, 0xad);
            for (const auto& row : state.comparison.rows) {
                if (selection.index < 0 ||
                    static_cast<std::size_t>(selection.index) >= row.cells.size()) {
                    continue;
                }
                const auto& cell = row.cells[static_cast<std::size_t>(selection.index)];
                if (!cell.integer.has_value()) continue;
                line.points.append(WaveformWidget::Point{
                    row.frame, true, static_cast<double>(*cell.integer), true});
            }
            signal.series.push_back(line);
            rows.push_back(signal);
            continue;
        }
        signal.name = selection.name;
        const auto signal_index = static_cast<std::size_t>(selection.index);
        for (std::size_t index = 0; index < state.series.size(); ++index) {
            if (index >= series_checks_.size() || !series_checks_[index]->isChecked()) continue;
            WaveformWidget::Series line;
            line.label = from_utf8(state.series[index].label);
            line.color = series_color(static_cast<int>(index));
            for (const auto& sample : state.series[index].samples) {
                if (signal_index >= sample.observations.size()) continue;
                const auto& cell = sample.observations[signal_index];
                line.points.append(
                    WaveformWidget::Point{sample.frame, cell.numeric, cell.value, cell.exact});
            }
            signal.series.push_back(line);
        }
        rows.push_back(signal);
    }
    waveform_->setSignals(rows);

    // 事件轨：分支起点、干预、检查点、失败与停止。
    QVector<WaveformWidget::Event> events;
    for (std::size_t index = 0; index < state.series.size(); ++index) {
        const auto& series_data = state.series[index];
        if (index > 0) {
            events.append(WaveformWidget::Event{
                series_data.origin,
                ui_text(texts, "workbench.waveform.event.branch", "Branch start") + " " + from_utf8(series_data.label),
                QColor(0x8e, 0x44, 0xad), WaveformWidget::EventKind::branch});
        }
        for (const auto& intervention : series_data.interventions) {
            QString text = ui_text(texts, "workbench.waveform.event.intervention", "Intervention");
            text += QStringLiteral(" ") + from_utf8(intervention.module);
            if (!intervention.field.empty()) text += "/" + from_utf8(intervention.field);
            text += QStringLiteral(": %1 → %2")
                        .arg(from_utf8(intervention.previous), from_utf8(intervention.replacement));
            events.append(WaveformWidget::Event{series_data.origin, text, QColor(0xe6, 0x7e, 0x22),
                                                WaveformWidget::EventKind::intervention});
        }
        for (const auto& step_event : series_data.events) {
            QString text;
            QColor color(0xc0, 0x39, 0x2b);
            auto kind = WaveformWidget::EventKind::failure;
            switch (step_event.kind) {
                case session::StepEvent::Kind::stopped:
                    text = ui_text(texts, "workbench.waveform.event.stopped", "Stopped");
                    color = QColor(0x77, 0x77, 0x77);
                    kind = WaveformWidget::EventKind::stop;
                    break;
                case session::StepEvent::Kind::input_failed:
                    text = ui_text(texts, "workbench.waveform.event.input_failed", "Input failed");
                    break;
                case session::StepEvent::Kind::advance_failed:
                    text = ui_text(texts, "workbench.waveform.event.advance_failed", "Advance failed");
                    break;
                case session::StepEvent::Kind::sample_failed:
                    text = ui_text(texts, "workbench.waveform.event.sample_failed", "Sample failed");
                    break;
                case session::StepEvent::Kind::completed:
                    continue;
            }
            events.append(WaveformWidget::Event{step_event.frame, text, color, kind});
        }
    }
    if (state.status.has_checkpoint) {
        events.append(WaveformWidget::Event{
            state.status.checkpoint_frame,
            ui_text(texts, "workbench.waveform.event.checkpoint", "Checkpoint"),
            QColor(0x17, 0xa2, 0xb8), WaveformWidget::EventKind::checkpoint});
    }
    waveform_->setEvents(events);
    waveform_->setCheckpoint(
        state.status.has_checkpoint ? std::optional<std::int64_t>(state.status.checkpoint_frame) : std::nullopt,
        state.series.size() > 2);

    waveform_rebuilding_ = false;
    update_cursor_table();
}

std::vector<TimelinePanel::SignalSelection> TimelinePanel::checked_signals() const {
    std::vector<SignalSelection> result;
    for (int group_index = 0; group_index < signal_tree_->topLevelItemCount(); ++group_index) {
        auto* group = signal_tree_->topLevelItem(group_index);
        for (int child = 0; child < group->childCount(); ++child) {
            auto* item = group->child(child);
            if (item->checkState(0) != Qt::Checked) continue;
            result.push_back(SignalSelection{item->data(0, Qt::UserRole).toInt(),
                                            item->data(0, Qt::UserRole + 1).toInt() == 1,
                                            item->text(0)});
        }
    }
    return result;
}

void TimelinePanel::update_cursor_table() {
    const WorkspaceState& state = context_.workspace->state();
    const UiTexts& texts = *context_.texts;
    cursor_table_->setRowCount(0);
    const std::int64_t a = waveform_->cursorA();
    const std::int64_t b = waveform_->cursorB();
    const auto cell_at = [](const session::TrackTraceView& series_data, std::int64_t frame, std::size_t signal_index)
        -> const session::CellView* {
        for (const auto& sample : series_data.samples) {
            if (sample.frame != frame) continue;
            if (signal_index >= sample.observations.size()) return nullptr;
            return &sample.observations[signal_index];
        }
        return nullptr;
    };
    const auto subtract_checked = [](std::int64_t value_a, std::int64_t value_b,
                                     std::int64_t& difference) {
        if (value_a > 0 && value_b < std::numeric_limits<std::int64_t>::min() + value_a) return false;
        if (value_a < 0 && value_b > std::numeric_limits<std::int64_t>::max() + value_a) return false;
        difference = value_b - value_a;
        return true;
    };
    const auto diff_cell_at = [&state](std::int64_t frame, int signal_index) -> const session::DiffCellView* {
        for (const auto& row : state.comparison.rows) {
            if (row.frame != frame) continue;
            if (signal_index < 0 || static_cast<std::size_t>(signal_index) >= row.cells.size()) return nullptr;
            return &row.cells[static_cast<std::size_t>(signal_index)];
        }
        return nullptr;
    };
    const auto delta_text = [&texts, &subtract_checked](std::optional<std::int64_t> value_a, std::optional<std::int64_t> value_b) {
        if (!value_a.has_value() || !value_b.has_value()) return QStringLiteral("—");
        std::int64_t difference = 0;
        return subtract_checked(*value_a, *value_b, difference)
                   ? QString::number(difference)
                   : ui_text(texts, "workbench.waveform.overflow", "overflow");
    };
    for (const auto& selection : checked_signals()) {
        if (selection.difference) {
            const int row = cursor_table_->rowCount();
            cursor_table_->insertRow(row);
            cursor_table_->setItem(row, 0, new QTableWidgetItem(
                ui_text(texts, "workbench.waveform.diff_name", "%1 (difference)").arg(selection.name)));
            cursor_table_->setItem(row, 1, new QTableWidgetItem(
                ui_text(texts, "workbench.waveform.diff_series", "Treated − control")));
            const auto* cell_a = diff_cell_at(a, selection.index);
            const auto* cell_b = diff_cell_at(b, selection.index);
            cursor_table_->setItem(row, 2,
                                   new QTableWidgetItem(cell_a != nullptr ? from_utf8(cell_a->difference)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 3,
                                   new QTableWidgetItem(cell_b != nullptr ? from_utf8(cell_b->difference)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 4, new QTableWidgetItem(delta_text(
                cell_a != nullptr ? cell_a->integer : std::nullopt,
                cell_b != nullptr ? cell_b->integer : std::nullopt)));
            continue;
        }
        for (std::size_t index = 0; index < state.series.size(); ++index) {
            if (index >= series_checks_.size() || !series_checks_[index]->isChecked()) continue;
            const int row = cursor_table_->rowCount();
            cursor_table_->insertRow(row);
            cursor_table_->setItem(row, 0, new QTableWidgetItem(selection.name));
            cursor_table_->setItem(row, 1, new QTableWidgetItem(from_utf8(state.series[index].label)));
            const auto signal = static_cast<std::size_t>(selection.index);
            const session::CellView* cell_a = cell_at(state.series[index], a, signal);
            const session::CellView* cell_b = cell_at(state.series[index], b, signal);
            cursor_table_->setItem(row, 2,
                                   new QTableWidgetItem(cell_a != nullptr ? from_utf8(cell_a->display)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 3,
                                   new QTableWidgetItem(cell_b != nullptr ? from_utf8(cell_b->display)
                                                                          : QStringLiteral("—")));
            cursor_table_->setItem(row, 4, new QTableWidgetItem(delta_text(
                cell_a != nullptr ? cell_a->integer : std::nullopt,
                cell_b != nullptr ? cell_b->integer : std::nullopt)));
        }
    }
}

void TimelinePanel::clear() { waveform_->clear(); }

}  // namespace ascend::workbench
