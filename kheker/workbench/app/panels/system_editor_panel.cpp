#include "system_editor_panel.hpp"

#include "ui_format.hpp"

#include <QComboBox>
#include <QFileInfo>
#include <QGroupBox>
#include <QHBoxLayout>
#include <QHeaderView>
#include <QLabel>
#include <QLineEdit>
#include <QListWidget>
#include <QPushButton>
#include <QTreeWidget>
#include <QVBoxLayout>

#include <map>
#include <string>
#include <vector>

namespace ascend::workbench {
namespace {

// 由定义标识建议实例名：取最后一段，与现有实例重名时追加序号。
QString suggest_instance_name(const QString& definition, const std::vector<session::InstanceView>& instances) {
    QString base = definition.section(QLatin1Char('.'), -1);
    if (base.isEmpty()) base = definition;
    const auto taken = [&](const QString& name) {
        for (const auto& instance : instances) {
            if (from_utf8(instance.name) == name) return true;
        }
        return false;
    };
    QString candidate = base;
    int suffix = 2;
    while (taken(candidate)) candidate = QStringLiteral("%1_%2").arg(base).arg(suffix++);
    return candidate;
}

}  // namespace

SystemEditorPanel::SystemEditorPanel(const PanelContext& context, QWidget* parent)
    : QWidget(parent), context_(context) {
    const UiTexts& texts = *context_.texts;
    setObjectName("systemEditorPage");
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(6, 6, 6, 6);
    layout->setSpacing(6);

    // 顶部：新建、系统名、检查与应用
    auto* top = new QHBoxLayout();
    new_system_button_ = new QPushButton(ui_text(texts, "workbench.editor.new_system", "New system…"), this);
    new_system_button_->setObjectName("systemNewButton");
    connect(new_system_button_, &QPushButton::clicked, this, [this] { context_.new_system(); });
    top->addWidget(new_system_button_);
    open_system_button_ = new QPushButton(ui_text(texts, "workbench.editor.open_system", "Open system…"), this);
    open_system_button_->setObjectName("systemOpenButton");
    connect(open_system_button_, &QPushButton::clicked, this, [this] { context_.open_system(); });
    top->addWidget(open_system_button_);
    save_system_button_ = new QPushButton(ui_text(texts, "workbench.editor.save_system", "Save system…"), this);
    save_system_button_->setObjectName("systemSaveButton");
    connect(save_system_button_, &QPushButton::clicked, this, [this] { context_.save_system(); });
    top->addWidget(save_system_button_);
    system_name_label_ = new QLabel(this);
    system_name_label_->setObjectName("systemNameLabel");
    top->addWidget(system_name_label_);
    top->addStretch(1);
    editor_check_button_ = new QPushButton(ui_text(texts, "workbench.action.check", "Check"), this);
    editor_check_button_->setObjectName("systemCheckButton");
    connect(editor_check_button_, &QPushButton::clicked, this, [this] { context_.dispatch("check"); });
    top->addWidget(editor_check_button_);
    editor_apply_button_ = new QPushButton(ui_text(texts, "workbench.editor.apply", "Apply (rebuild run)"), this);
    editor_apply_button_->setObjectName("systemApplyButton");
    connect(editor_apply_button_, &QPushButton::clicked, this, [this] { context_.dispatch("apply"); });
    top->addWidget(editor_apply_button_);
    layout->addLayout(top);

    // 可用模块与实例名
    auto* add_row = new QHBoxLayout();
    add_row->addWidget(new QLabel(ui_text(texts, "workbench.editor.available", "Available modules"), this));
    definition_combo_ = new QComboBox(this);
    definition_combo_->setObjectName("systemDefinitionCombo");
    definition_combo_->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    connect(definition_combo_, &QComboBox::currentIndexChanged, this, [this] { on_system_definition_changed(); });
    add_row->addWidget(definition_combo_, 1);
    instance_edit_ = new QLineEdit(this);
    instance_edit_->setObjectName("systemInstanceEdit");
    instance_edit_->setPlaceholderText(ui_text(texts, "workbench.editor.instance", "Instance name"));
    add_row->addWidget(instance_edit_);
    add_module_button_ = new QPushButton(ui_text(texts, "workbench.editor.add", "Add module"), this);
    add_module_button_->setObjectName("systemAddButton");
    connect(add_module_button_, &QPushButton::clicked, this, &SystemEditorPanel::add_module);
    add_row->addWidget(add_module_button_);
    layout->addLayout(add_row);

    // 系统结构：实例（定义）与需求（提供方）
    system_tree_ = new QTreeWidget(this);
    system_tree_->setObjectName("systemTree");
    system_tree_->setColumnCount(3);
    system_tree_->setHeaderLabels({ui_text(texts, "workbench.editor.column_object", "Object"),
                                   ui_text(texts, "workbench.editor.column_kind", "Kind / type"),
                                   ui_text(texts, "workbench.editor.column_provider", "Provider")});
    system_tree_->header()->setStretchLastSection(true);
    system_tree_->setSelectionMode(QAbstractItemView::SingleSelection);
    connect(system_tree_, &QTreeWidget::itemSelectionChanged, this, &SystemEditorPanel::on_system_selection_changed);
    layout->addWidget(system_tree_, 1);

    // 连接与移除
    auto* wire_row = new QHBoxLayout();
    wire_row->addWidget(new QLabel(ui_text(texts, "workbench.editor.connect", "Connect to"), this));
    provider_combo_ = new QComboBox(this);
    provider_combo_->setObjectName("systemProviderCombo");
    provider_combo_->setSizeAdjustPolicy(QComboBox::AdjustToContents);
    connect(provider_combo_, &QComboBox::currentIndexChanged, this, [this] { refresh_controls(); });
    wire_row->addWidget(provider_combo_, 1);
    connect_button_ = new QPushButton(ui_text(texts, "workbench.editor.connect_action", "Connect"), this);
    connect_button_->setObjectName("systemConnectButton");
    connect(connect_button_, &QPushButton::clicked, this, &SystemEditorPanel::connect_requirement);
    wire_row->addWidget(connect_button_);
    disconnect_button_ = new QPushButton(ui_text(texts, "workbench.editor.disconnect", "Disconnect"), this);
    disconnect_button_->setObjectName("systemDisconnectButton");
    connect(disconnect_button_, &QPushButton::clicked, this, &SystemEditorPanel::disconnect_requirement);
    wire_row->addWidget(disconnect_button_);
    remove_module_button_ = new QPushButton(ui_text(texts, "workbench.editor.remove", "Remove instance"), this);
    remove_module_button_->setObjectName("systemRemoveButton");
    connect(remove_module_button_, &QPushButton::clicked, this, &SystemEditorPanel::remove_module);
    wire_row->addWidget(remove_module_button_);
    layout->addLayout(wire_row);

    // 规格：推进入口、观测与自动推导的输入
    auto* spec_box = new QGroupBox(ui_text(texts, "workbench.editor.spec", "Experiment specification"), this);
    auto* spec_layout = new QVBoxLayout(spec_box);
    auto* advance_row = new QHBoxLayout();
    advance_row->addWidget(new QLabel(ui_text(texts, "workbench.editor.advance", "Advance entry"), spec_box));
    advance_combo_ = new QComboBox(spec_box);
    advance_combo_->setObjectName("systemAdvanceCombo");
    connect(advance_combo_, &QComboBox::currentIndexChanged, this, [this] { on_spec_advance_changed(); });
    advance_row->addWidget(advance_combo_, 1);
    spec_layout->addLayout(advance_row);
    spec_layout->addWidget(new QLabel(ui_text(texts, "workbench.editor.observations", "Observations"), spec_box));
    observation_list_ = new QListWidget(spec_box);
    observation_list_->setObjectName("systemObservationList");
    observation_list_->setSelectionMode(QAbstractItemView::NoSelection);
    connect(observation_list_, &QListWidget::itemChanged, this, [this] { on_spec_observations_changed(); });
    spec_layout->addWidget(observation_list_, 1);
    input_label_ = new QLabel(spec_box);
    input_label_->setObjectName("systemInputLabel");
    input_label_->setWordWrap(true);
    spec_layout->addWidget(input_label_);
    layout->addWidget(spec_box);

    hide();  // 与其他中央页签一致：默认不打开
}

void SystemEditorPanel::render() {
    const WorkspaceState& state = context_.workspace->state();
    const UiTexts& texts = *context_.texts;
    editor_updating_ = true;
    const QString system_name = from_utf8(state.status.model_name);
    const QString system_file = from_utf8(state.system_file);
    const QString unsaved = state.system_dirty ? QStringLiteral(" *") : QString();
    system_name_label_->setText(
        system_file.isEmpty()
            ? ui_text(texts, "workbench.editor.system_name", "System: %1").arg(system_name) + unsaved
            : ui_text(texts, "workbench.editor.system_name_file", "System: %1 · File: %2")
                      .arg(system_name, QFileInfo(system_file).fileName()) +
                  unsaved);

    // 可用模块：保留当前选择，空编辑框自动填建议实例名。
    const QString current_definition = definition_combo_->currentText();
    definition_combo_->clear();
    for (const auto& definition : state.model.available_modules) definition_combo_->addItem(from_utf8(definition));
    if (!current_definition.isEmpty()) {
        const int index = definition_combo_->findText(current_definition);
        if (index >= 0) definition_combo_->setCurrentIndex(index);
    }
    if (instance_edit_->text().isEmpty() && definition_combo_->count() > 0) {
        instance_edit_->setText(suggest_instance_name(definition_combo_->currentText(), state.model.instances));
    }

    // 结构树：根作用域目录含各实例的需求与连接。
    const session::ModuleNodeView* root = nullptr;
    for (const auto& node : state.model.catalog.modules) {
        if (node.path.empty()) {
            root = &node;
            break;
        }
    }
    std::map<std::string, std::vector<const session::RequirementView*>> requirements;
    std::map<ascend::Reference, ascend::Reference> providers;
    if (root != nullptr) {
        for (const auto& requirement : root->requirements) requirements[requirement.reference.module].push_back(&requirement);
        for (const auto& connection : root->connections) providers[connection.requirement] = connection.provider;
    }
    const auto kind_text = [&texts](ascend::SymbolKind kind) {
        return kind == ascend::SymbolKind::method ? ui_text(texts, "workbench.kind.method", "Method")
                                                  : ui_text(texts, "workbench.kind.value", "Value");
    };
    system_tree_->clear();
    for (const auto& instance : state.model.instances) {
        if (!instance.scope.empty()) continue;  // 切片 A：根作用域
        auto* item = new QTreeWidgetItem(system_tree_);
        item->setText(0, from_utf8(instance.name));
        item->setText(1, from_utf8(instance.definition));
        item->setData(0, Qt::UserRole, QStringLiteral("instance"));
        item->setData(0, Qt::UserRole + 1, from_utf8(instance.name));
        item->setExpanded(true);
        const auto found = requirements.find(instance.name);
        if (found == requirements.end()) continue;
        for (const auto* requirement : found->second) {
            auto* child = new QTreeWidgetItem(item);
            child->setText(0, from_utf8(requirement->reference.symbol));
            child->setText(1, QStringLiteral("%1 · %2").arg(kind_text(requirement->kind),
                                                            from_utf8(requirement->result_type)));
            const auto provider = providers.find(requirement->reference);
            const bool connected = provider != providers.end();
            child->setText(2, connected ? reference_text(provider->second)
                                        : ui_text(texts, "workbench.editor.unconnected", "not connected"));
            child->setData(0, Qt::UserRole, QStringLiteral("requirement"));
            child->setData(0, Qt::UserRole + 1, from_utf8(requirement->reference.module));
            child->setData(0, Qt::UserRole + 2, from_utf8(requirement->reference.symbol));
            child->setData(0, Qt::UserRole + 3, connected ? 1 : 0);
        }
    }

    // 规格：推进入口（公开无参方法）与观测（公开量）。
    const auto reference_equal = [](const ascend::Reference& left, const ascend::Reference& right) {
        return left.module == right.module && left.symbol == right.symbol;
    };
    advance_combo_->clear();
    advance_combo_->addItem(ui_text(texts, "workbench.editor.no_advance", "(none)"), QString());
    int advance_index = 0;
    observation_list_->clear();
    if (root != nullptr) {
        for (const auto& declaration : root->declarations) {
            if (declaration.kind == ascend::SymbolKind::method && declaration.parameters.empty()) {
                const QString text = reference_text(declaration.reference);
                advance_combo_->addItem(text, text);
                if (reference_equal(declaration.reference, state.model.spec.advance)) {
                    advance_index = advance_combo_->count() - 1;
                }
            } else if (declaration.kind == ascend::SymbolKind::value) {
                auto* item = new QListWidgetItem(reference_text(declaration.reference), observation_list_);
                item->setFlags(item->flags() | Qt::ItemIsUserCheckable);
                bool checked = false;
                for (const auto& observation : state.model.spec.observations) {
                    if (reference_equal(observation.second, declaration.reference)) {
                        checked = true;
                        break;
                    }
                }
                item->setCheckState(checked ? Qt::Checked : Qt::Unchecked);
            }
        }
    }
    advance_combo_->setCurrentIndex(advance_index);
    QStringList inputs;
    for (const auto& input : state.model.spec.inputs) {
        inputs << QStringLiteral("%1 → %2").arg(from_utf8(input.first), reference_text(input.second));
    }
    input_label_->setText(ui_text(texts, "workbench.editor.inputs", "Inputs (derived): %1")
                              .arg(inputs.join(QStringLiteral("，"))));
    editor_updating_ = false;
    on_system_selection_changed();
}

void SystemEditorPanel::on_system_selection_changed() {
    if (editor_updating_ || provider_combo_ == nullptr) return;
    const WorkspaceState& state = context_.workspace->state();
    editor_updating_ = true;
    provider_combo_->clear();
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item != nullptr && item->data(0, Qt::UserRole).toString() == QStringLiteral("requirement")) {
        const std::string module = item->data(0, Qt::UserRole + 1).toString().toStdString();
        const std::string symbol = item->data(0, Qt::UserRole + 2).toString().toStdString();
        const session::ModuleNodeView* root = nullptr;
        for (const auto& node : state.model.catalog.modules) {
            if (node.path.empty()) {
                root = &node;
                break;
            }
        }
        const session::RequirementView* requirement = nullptr;
        if (root != nullptr) {
            for (const auto& candidate : root->requirements) {
                if (candidate.reference.module == module && candidate.reference.symbol == symbol) {
                    requirement = &candidate;
                    break;
                }
            }
        }
        if (requirement != nullptr && root != nullptr) {
            // 候选提供方：公开声明中种类、结果类型与参数签名一致的其他实例符号。
            for (const auto& declaration : root->declarations) {
                if (declaration.reference.module == module) continue;  // 不连接自身
                if (declaration.kind != requirement->kind) continue;
                if (declaration.result_type != requirement->result_type) continue;
                if (declaration.parameters.size() != requirement->parameters.size()) continue;
                bool same_signature = true;
                for (std::size_t index = 0; index < declaration.parameters.size(); ++index) {
                    if (declaration.parameters[index].type != requirement->parameters[index].type) {
                        same_signature = false;
                        break;
                    }
                }
                if (!same_signature) continue;
                const QString text = reference_text(declaration.reference);
                provider_combo_->addItem(text, text);
            }
        }
    }
    editor_updating_ = false;
    refresh_controls();
}

void SystemEditorPanel::update_controls(bool closing) {
    shell_closing_ = closing;
    const ControlsView controls = context_.workspace->controls();
    // 空会话尚未确立系统身份：只可新建或打开系统，装配编辑与检查/应用禁用。
    const bool available = controls.can_open_file && !closing;
    const bool editable = controls.editable && !closing;
    new_system_button_->setEnabled(available);
    open_system_button_->setEnabled(available);
    save_system_button_->setEnabled(editable);
    editor_check_button_->setEnabled(editable);
    editor_apply_button_->setEnabled(editable);
    definition_combo_->setEnabled(editable);
    instance_edit_->setEnabled(editable);
    add_module_button_->setEnabled(editable && definition_combo_->count() > 0);
    advance_combo_->setEnabled(editable);
    observation_list_->setEnabled(editable);
    QTreeWidgetItem* item = system_tree_->currentItem();
    const QString kind = item != nullptr ? item->data(0, Qt::UserRole).toString() : QString();
    const bool requirement = kind == QStringLiteral("requirement");
    const bool instance = kind == QStringLiteral("instance");
    remove_module_button_->setEnabled(editable && (requirement || instance));
    provider_combo_->setEnabled(editable && requirement);
    connect_button_->setEnabled(editable && requirement && provider_combo_->count() > 0);
    disconnect_button_->setEnabled(editable && requirement && item->data(0, Qt::UserRole + 3).toInt() == 1);
}

void SystemEditorPanel::refresh_controls() { update_controls(shell_closing_); }

void SystemEditorPanel::on_system_definition_changed() {
    if (editor_updating_) return;
    if (definition_combo_->count() == 0) return;
    instance_edit_->setText(
        suggest_instance_name(definition_combo_->currentText(), context_.workspace->state().model.instances));
    refresh_controls();
}

void SystemEditorPanel::add_module() {
    if (definition_combo_->count() == 0) return;
    const QString definition = definition_combo_->currentText();
    QString instance = instance_edit_->text().trimmed();
    if (instance.isEmpty()) {
        instance = suggest_instance_name(definition, context_.workspace->state().model.instances);
    }
    context_.add_module(definition, instance);
}

void SystemEditorPanel::remove_module() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr) return;
    const QString instance = item->data(0, Qt::UserRole + 1).toString();
    if (instance.isEmpty()) return;
    context_.remove_module(instance);
}

void SystemEditorPanel::connect_requirement() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr || item->data(0, Qt::UserRole).toString() != QStringLiteral("requirement")) return;
    const QString provider = provider_combo_->currentData().toString();
    if (provider.isEmpty()) return;
    const int slash = provider.lastIndexOf(QLatin1Char('/'));
    if (slash <= 0) return;
    context_.connect_requirement(item->data(0, Qt::UserRole + 1).toString(),
                                 item->data(0, Qt::UserRole + 2).toString(), provider.left(slash),
                                 provider.mid(slash + 1));
}

void SystemEditorPanel::disconnect_requirement() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr || item->data(0, Qt::UserRole).toString() != QStringLiteral("requirement")) return;
    context_.disconnect_requirement(item->data(0, Qt::UserRole + 1).toString(),
                                    item->data(0, Qt::UserRole + 2).toString());
}

void SystemEditorPanel::on_spec_advance_changed() {
    if (editor_updating_) return;
    const QString value = advance_combo_->currentData().toString();
    QString module;
    QString symbol;
    if (!value.isEmpty()) {
        const int slash = value.lastIndexOf(QLatin1Char('/'));
        if (slash > 0) {
            module = value.left(slash);
            symbol = value.mid(slash + 1);
        }
    }
    context_.set_spec_advance(module, symbol);
}

void SystemEditorPanel::on_spec_observations_changed() {
    if (editor_updating_) return;
    QStringList checked;
    for (int row = 0; row < observation_list_->count(); ++row) {
        auto* item = observation_list_->item(row);
        if (item->checkState() == Qt::Checked) checked << item->text();
    }
    context_.set_spec_observations(checked);
}

}  // namespace ascend::workbench
