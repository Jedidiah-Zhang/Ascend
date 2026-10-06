#pragma once

// 时间轴页：信号树、波形与游标读数表（中央工作区页签，有运行/记录时打开）。
// 只读工作区状态渲染；选中帧的采样详情经 PanelContext 的 request_sample_detail 交给外壳。
// 设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "panel_context.hpp"

#include <QWidget>

#include <vector>

class QCheckBox;
class QTableWidget;
class QTreeWidget;
class QTreeWidgetItem;

namespace ascend::workbench {

class WaveformWidget;

class TimelinePanel : public QWidget {
    Q_OBJECT

public:
    explicit TimelinePanel(const PanelContext& context, QWidget* parent = nullptr);

    // 渲染入口：由外壳在状态层相应变更时调用。
    void render_waveform();
    // 身份切换：清掉与上一身份绑定的波形与游标状态（信号树随模型变更重建）。
    void clear();

private:
    struct SignalSelection {
        int index = 0;
        bool difference = false;
        QString name;
    };

    std::vector<SignalSelection> checked_signals() const;
    void update_cursor_table();

    const PanelContext& context_;
    QTreeWidget* signal_tree_ = nullptr;
    WaveformWidget* waveform_ = nullptr;
    QTableWidget* cursor_table_ = nullptr;
    QWidget* series_checks_host_ = nullptr;
    std::vector<QCheckBox*> series_checks_;
    QTreeWidgetItem* observation_group_ = nullptr;
    QTreeWidgetItem* diff_group_ = nullptr;
    bool waveform_rebuilding_ = false;
};

}  // namespace ascend::workbench
