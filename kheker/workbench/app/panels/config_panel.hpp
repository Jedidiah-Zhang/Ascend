#pragma once

// 配置（右侧停靠页）：滚动区内的实例配置表单。
// 只读工作区状态渲染；编辑值经 PanelContext 的 set_instance_config 交回外壳
// （忙碌簿记与队列提交由外壳保持）。设计规则见设计文档 WB-19。

#include "panel_context.hpp"

#include <QWidget>

#include <map>
#include <string>
#include <vector>

class QScrollArea;
class QVBoxLayout;

namespace ascend::workbench {

class ConfigEditor;

class ConfigPanel : public QWidget {
    Q_OBJECT

public:
    explicit ConfigPanel(const PanelContext& context, QWidget* parent = nullptr);

    // 渲染入口：由外壳在目录、装配或规格变更时调用；同值回显保留编辑器与输入。
    void render_instances(const std::vector<session::InstanceView>& instances);
    // 启用条件：由外壳在控件条件变更时调用。
    void set_editable(bool editable);

private:
    const PanelContext& context_;
    QScrollArea* config_area_ = nullptr;
    QWidget* config_container_ = nullptr;
    QVBoxLayout* config_layout_ = nullptr;
    std::map<std::string, ConfigEditor*> editors_;
};

}  // namespace ascend::workbench
