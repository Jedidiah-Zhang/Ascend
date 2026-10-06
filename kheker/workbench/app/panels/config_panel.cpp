#include "config_panel.hpp"

#include "config_editor.hpp"
#include "ui_format.hpp"

#include <QGroupBox>
#include <QScrollArea>
#include <QVBoxLayout>

#include <utility>

namespace ascend::workbench {

ConfigPanel::ConfigPanel(const PanelContext& context, QWidget* parent)
    : QWidget(parent), context_(context) {
    setObjectName("configPanel");
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);

    config_area_ = new QScrollArea(this);
    config_area_->setObjectName("configArea");
    config_area_->setWidgetResizable(true);
    config_area_->setMinimumHeight(150);
    config_container_ = new QWidget(config_area_);
    config_layout_ = new QVBoxLayout(config_container_);
    config_layout_->setContentsMargins(4, 4, 4, 4);
    config_area_->setWidget(config_container_);

    layout->addWidget(config_area_);
}

void ConfigPanel::render_instances(const std::vector<session::InstanceView>& instances) {
    const UiTexts& texts = *context_.texts;
    // 复用已有编辑器，避免模型回显时打断输入。
    std::map<std::string, ConfigEditor*> editors;
    for (const auto& instance : instances) {
        const std::string key = instance.scope + "/" + instance.name;
        auto found = editors_.find(key);
        if (found != editors_.end()) {
            editors.emplace(key, found->second);
            found->second->setConfig(instance.config);
            continue;
        }
        auto* box = new QGroupBox(
            ui_text(texts, "workbench.config.instance_box", "%1 (definition %2)")
                .arg(from_utf8(instance.name), from_utf8(instance.definition)),
            config_container_);
        auto* layout = new QVBoxLayout(box);
        auto* editor = new ConfigEditor(*context_.int_adapter, texts, box);
        editor->setObjectName("configEditor_" + from_utf8(instance.name));
        editor->setConfig(instance.config);
        const std::string scope = instance.scope;
        const std::string name = instance.name;
        connect(editor, &ConfigEditor::configEdited, this, [this, scope, name](const ascend::Config& config) {
            context_.set_instance_config(from_utf8(scope), from_utf8(name), config);
        });
        layout->addWidget(editor);
        box->setObjectName("instanceBox_" + from_utf8(instance.name));
        config_layout_->addWidget(box);
        editors.emplace(key, editor);
    }
    // 删除已经不在草稿中的实例编辑器。
    for (auto& item : editors_) {
        if (editors.count(item.first) != 0) continue;
        auto* editor = item.second;
        auto* box = editor->parentWidget();
        box->deleteLater();
    }
    editors_ = std::move(editors);
}

void ConfigPanel::set_editable(bool editable) {
    for (auto& item : editors_) item.second->setEnabled(editable);
}

}  // namespace ascend::workbench
