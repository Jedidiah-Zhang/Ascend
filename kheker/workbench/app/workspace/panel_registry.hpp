#pragma once

// 面板注册表：面板元数据只声明一次，查看菜单、页签标识查找与勾选同步由它生成（WB-19）。
// 仅界面侧使用：注册表持有 QWidget／QAction，不进入 QtCore 状态层。

#include <QString>

#include <vector>

class QAction;
class QDockWidget;
class QWidget;

namespace ascend::workbench {

struct PanelDescriptor {
    QString id;
    bool central = true;
    QWidget* page = nullptr;      // 中央页；停靠面板为空
    QDockWidget* dock = nullptr;  // 停靠面板；中央页为空
    QAction* action = nullptr;    // 查看菜单动作（中央页可勾选；停靠为 toggleViewAction）
};

class PanelRegistry {
public:
    // 注册中央页：创建可勾选菜单动作（object_name 供测试定位）。
    PanelDescriptor& addCentral(const QString& id, QWidget* page, const QString& title, const QString& object_name,
                                bool checked, QWidget* action_parent);
    // 注册停靠面板：动作复用 QDockWidget::toggleViewAction 并替换标题。
    PanelDescriptor& addDock(const QString& id, QDockWidget* dock, const QString& title);

    const std::vector<PanelDescriptor>& panels() const noexcept { return panels_; }
    const PanelDescriptor* find(const QString& id) const;
    const PanelDescriptor* findByPage(const QWidget* page) const;

private:
    std::vector<PanelDescriptor> panels_;
};

}  // namespace ascend::workbench
