#pragma once

// 开始页：新建研究、打开项目与打开内置示例入口（默认打开的中央工作区页签）。
// 面板只呈现文案，命令经 PanelContext 交回外壳；设计规则见设计文档 WB-19。

#include "panel_context.hpp"

#include <QWidget>

class QPushButton;

namespace ascend::workbench {

class StartPagePanel : public QWidget {
    Q_OBJECT

public:
    explicit StartPagePanel(const PanelContext& context, QWidget* parent = nullptr);

    // 三个入口的可用性由外壳按 ControlsView 套用（忙碌时禁用）。
    void set_open_enabled(bool enabled);

private:
    const PanelContext& context_;
    QPushButton* new_button_ = nullptr;
    QPushButton* open_button_ = nullptr;
    QPushButton* example_button_ = nullptr;
};

}  // namespace ascend::workbench
