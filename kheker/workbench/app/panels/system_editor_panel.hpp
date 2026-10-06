#pragma once

// 系统编辑器：系统文件与装配编辑、规格推进入口与观测（中央工作区页签，默认不打开）。
// 只读工作区状态渲染（render）；编辑命令的参数从控件读出后经 PanelContext 交回外壳，
// 系统文件字段与未保存标记由状态层持有。设计规则见设计文档 WB-19。

#include "panel_context.hpp"

#include <QWidget>

class QComboBox;
class QLabel;
class QLineEdit;
class QListWidget;
class QPushButton;
class QTreeWidget;

namespace ascend::workbench {

class SystemEditorPanel : public QWidget {
    Q_OBJECT

public:
    explicit SystemEditorPanel(const PanelContext& context, QWidget* parent = nullptr);

    // 渲染入口：由外壳在模型变更或系统文件字段更新时调用；状态自读。
    void render();
    // 启用条件：由外壳传入窗口关闭标记，读状态层控件条件与本地选择状态套用。
    void update_controls(bool closing);

private:
    void on_system_selection_changed();
    void on_system_definition_changed();
    void on_spec_advance_changed();
    void on_spec_observations_changed();
    void add_module();
    void remove_module();
    void connect_requirement();
    void disconnect_requirement();
    // 内部刷新：沿用外壳最近一次传入的关闭标记。
    void refresh_controls();

    const PanelContext& context_;
    QLabel* system_name_label_ = nullptr;
    QPushButton* new_system_button_ = nullptr;
    QPushButton* open_system_button_ = nullptr;
    QPushButton* save_system_button_ = nullptr;
    QComboBox* definition_combo_ = nullptr;
    QLineEdit* instance_edit_ = nullptr;
    QPushButton* add_module_button_ = nullptr;
    QTreeWidget* system_tree_ = nullptr;
    QComboBox* provider_combo_ = nullptr;
    QPushButton* connect_button_ = nullptr;
    QPushButton* disconnect_button_ = nullptr;
    QPushButton* remove_module_button_ = nullptr;
    QPushButton* editor_check_button_ = nullptr;
    QPushButton* editor_apply_button_ = nullptr;
    QComboBox* advance_combo_ = nullptr;
    QListWidget* observation_list_ = nullptr;
    QLabel* input_label_ = nullptr;
    bool editor_updating_ = false;
    bool shell_closing_ = false;
};

}  // namespace ascend::workbench
