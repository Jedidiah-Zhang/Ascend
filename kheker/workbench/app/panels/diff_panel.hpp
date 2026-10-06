#pragma once

// 逻辑帧差异页：分支对照差异表与说明（中央工作区页签，有运行/记录时打开）。
// 只读工作区状态的 comparison 渲染；设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "panel_context.hpp"

#include <QWidget>

class QLabel;
class QTableWidget;

namespace ascend::workbench {

class DiffPanel : public QWidget {
    Q_OBJECT

public:
    explicit DiffPanel(const PanelContext& context, QWidget* parent = nullptr);

    // 渲染入口：由外壳在状态层 comparison 变更时调用。
    void render_comparison();
    // 结果重置与身份切换：清空差异表与说明。
    void clear();

private:
    const PanelContext& context_;
    QTableWidget* diff_table_ = nullptr;
    QLabel* diff_note_ = nullptr;
};

}  // namespace ascend::workbench
