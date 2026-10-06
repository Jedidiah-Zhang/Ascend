#pragma once

// 模块库：已载入模块包概要表与载入／载入文件夹／卸载入口（中央工作区页签，默认不打开）。
// 只读状态渲染（render），命令与文件对话框经 PanelContext 交回外壳；设计规则见 WB-19。

#include "panel_context.hpp"

#include <ascend/session/session.hpp>

#include <QWidget>

#include <vector>

class QPushButton;
class QTableWidget;

namespace ascend::workbench {

class PackageLibraryPanel : public QWidget {
    Q_OBJECT

public:
    explicit PackageLibraryPanel(const PanelContext& context, QWidget* parent = nullptr);

    // 状态层 packages 变更时由外壳调用：重建概要表行（列顺序：定义／版本／实现／状态／声明／需求／资源）。
    void render(const std::vector<session::ModulePackageView>& packages);
    // 「载入…」与「载入文件夹…」的可用性（忙碌时禁用）。
    void set_load_enabled(bool enabled);
    // 「卸载」的可用性由外壳按 ControlsView 派生（额外要求已载入非空）。
    void set_unload_enabled(bool enabled);

private:
    // 当前选中的模块包定义标识；无选中时为空。
    QString selected_definition() const;

    const PanelContext& context_;
    QTableWidget* table_ = nullptr;
    QPushButton* load_button_ = nullptr;
    QPushButton* load_folder_button_ = nullptr;
    QPushButton* unload_button_ = nullptr;
};

}  // namespace ascend::workbench
