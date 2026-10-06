#pragma once

// 模块管理器（左侧停靠页）：模块树、规格、需求与连接四个页签。
// 只读工作区状态渲染；选中项详情生成 HTML 后经 PanelContext 的 show_details 交外壳。
// 设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "panel_context.hpp"

#include <QWidget>

#include <map>
#include <string>

class QTabWidget;
class QTreeWidget;
class QTreeWidgetItem;

namespace ascend::workbench {

class ModulesPanel : public QWidget {
    Q_OBJECT

public:
    explicit ModulesPanel(const PanelContext& context, QWidget* parent = nullptr);

    // 渲染入口：由外壳在目录、装配或规格变更时调用（目录陈旧标注读状态层）。
    void render();

private:
    void rebuild_module_tree();
    void rebuild_spec();
    void rebuild_requirements();
    void rebuild_connections();
    void show_declaration_at(int index);
    void show_requirement_at(int index);
    QTreeWidgetItem* module_node_for(QTreeWidget* tree, std::map<std::string, QTreeWidgetItem*>& nodes,
                                     const std::string& path);

    const PanelContext& context_;
    QTabWidget* tabs_ = nullptr;
    QTreeWidget* module_tree_ = nullptr;
    QTreeWidget* spec_tree_ = nullptr;
    QTreeWidget* requirements_tree_ = nullptr;
    QTreeWidget* connections_tree_ = nullptr;
};

}  // namespace ascend::workbench
