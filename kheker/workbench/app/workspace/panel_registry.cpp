#include "panel_registry.hpp"

#include <QAction>
#include <QDockWidget>
#include <QWidget>

namespace ascend::workbench {

PanelDescriptor& PanelRegistry::addCentral(const QString& id, QWidget* page, const QString& title,
                                           const QString& object_name, bool checked, QWidget* action_parent) {
    PanelDescriptor descriptor;
    descriptor.id = id;
    descriptor.central = true;
    descriptor.page = page;
    descriptor.action = new QAction(title, action_parent);
    descriptor.action->setObjectName(object_name);
    descriptor.action->setCheckable(true);
    descriptor.action->setChecked(checked);
    panels_.push_back(descriptor);
    return panels_.back();
}

PanelDescriptor& PanelRegistry::addDock(const QString& id, QDockWidget* dock, const QString& title) {
    PanelDescriptor descriptor;
    descriptor.id = id;
    descriptor.central = false;
    descriptor.dock = dock;
    descriptor.action = dock->toggleViewAction();
    descriptor.action->setText(title);
    panels_.push_back(descriptor);
    return panels_.back();
}

const PanelDescriptor* PanelRegistry::find(const QString& id) const {
    for (const auto& panel : panels_) {
        if (panel.id == id) return &panel;
    }
    return nullptr;
}

const PanelDescriptor* PanelRegistry::findByPage(const QWidget* page) const {
    for (const auto& panel : panels_) {
        if (panel.central && panel.page == page) return &panel;
    }
    return nullptr;
}

}  // namespace ascend::workbench
