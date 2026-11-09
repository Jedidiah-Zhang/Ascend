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
#include <QTreeWidgetItemIterator>
#include <QVBoxLayout>

#include <map>
#include <optional>
#include <string>
#include <vector>

namespace ascend::workbench {
namespace {

// 系统树节点数据：类别、所在作用域、局部模块名、符号、连接状态与接口来源。
constexpr int kKindRole = Qt::UserRole;
constexpr int kScopeRole = Qt::UserRole + 1;
constexpr int kModuleRole = Qt::UserRole + 2;
constexpr int kSymbolRole = Qt::UserRole + 3;
constexpr int kConnectedRole = Qt::UserRole + 4;
constexpr int kInterfaceRole = Qt::UserRole + 5;
constexpr int kOwnerScopeRole = Qt::UserRole + 6;
constexpr int kForwardedRole = Qt::UserRole + 7;
constexpr int kExportedRole = Qt::UserRole + 8;

QString parent_path(const QString& path) {
    const int slash = path.lastIndexOf(QLatin1Char('/'));
    return slash < 0 ? QString() : path.left(slash);
}

QString leaf_name(const QString& path) {
    return path.section(QLatin1Char('/'), -1);
}

QString join_scope(const QString& scope, const QString& name) {
    return scope.isEmpty() ? name : scope + QLatin1Char('/') + name;
}

// 树节点身份：类别、作用域、局部模块名与符号；用于重建后恢复选择。
QString item_key(QTreeWidgetItem* item) {
    if (item == nullptr) return {};
    const QString kind = item->data(0, kKindRole).toString();
    if (kind.isEmpty()) return {};
    const auto part = [](const QString& value) {
        return QString::number(value.size()) + QLatin1Char(':') + value;
    };
    return part(kind) + part(item->data(0, kScopeRole).toString()) +
           part(item->data(0, kModuleRole).toString()) + part(item->data(0, kSymbolRole).toString());
}

QTreeWidgetItem* find_item_by_key(QTreeWidget* tree, const QString& key) {
    if (key.isEmpty()) return nullptr;
    QTreeWidgetItemIterator iterator(tree);
    while (*iterator != nullptr) {
        if (item_key(*iterator) == key) return *iterator;
        ++iterator;
    }
    return nullptr;
}

const session::ModuleNodeView* find_node(
    const std::map<std::string, const session::ModuleNodeView*>& nodes, const QString& path) {
    const auto found = nodes.find(path.toStdString());
    return found == nodes.end() ? nullptr : found->second;
}

// 由定义标识建议实例名：取最后一段，与同一作用域内现有实例或子作用域重名时追加序号。
QString suggest_instance_name(const QString& definition, const WorkspaceState& state,
                              const QString& scope) {
    QString base = definition.section(QLatin1Char('.'), -1);
    if (base.isEmpty()) base = definition;
    const auto taken = [&](const QString& name) {
        for (const auto& instance : state.model.instances) {
            if (from_utf8(instance.scope) == scope && from_utf8(instance.name) == name) return true;
        }
        const QString path = join_scope(scope, name);
        for (const auto& node : state.model.catalog.modules) {
            if (node.scope && from_utf8(node.path) == path) return true;
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

    // 作用域与接口：父作用域取自当前选择。
    auto* scope_row = new QHBoxLayout();
    scope_row->addWidget(new QLabel(ui_text(texts, "workbench.editor.scope", "Scope"), this));
    scope_edit_ = new QLineEdit(this);
    scope_edit_->setObjectName("systemScopeEdit");
    scope_edit_->setPlaceholderText(ui_text(texts, "workbench.editor.scope_name", "Scope name"));
    connect(scope_edit_, &QLineEdit::textChanged, this, [this] { refresh_controls(); });
    scope_row->addWidget(scope_edit_, 1);
    add_scope_button_ = new QPushButton(ui_text(texts, "workbench.editor.add_scope", "Add scope"), this);
    add_scope_button_->setObjectName("systemAddScopeButton");
    connect(add_scope_button_, &QPushButton::clicked, this, &SystemEditorPanel::add_scope);
    scope_row->addWidget(add_scope_button_);
    scope_row->addSpacing(12);
    scope_row->addWidget(new QLabel(ui_text(texts, "workbench.editor.interface_name", "Interface name"), this));
    interface_edit_ = new QLineEdit(this);
    interface_edit_->setObjectName("systemInterfaceEdit");
    connect(interface_edit_, &QLineEdit::textChanged, this, [this] { refresh_controls(); });
    scope_row->addWidget(interface_edit_, 1);
    forward_button_ = new QPushButton(ui_text(texts, "workbench.editor.forward", "Forward to parent"), this);
    forward_button_->setObjectName("systemForwardButton");
    connect(forward_button_, &QPushButton::clicked, this, &SystemEditorPanel::forward_requirement);
    scope_row->addWidget(forward_button_);
    export_button_ = new QPushButton(ui_text(texts, "workbench.editor.export", "Export to parent"), this);
    export_button_->setObjectName("systemExportButton");
    connect(export_button_, &QPushButton::clicked, this, &SystemEditorPanel::export_symbol);
    scope_row->addWidget(export_button_);
    layout->addLayout(scope_row);

    // 系统结构：作用域与实例、需求、公开项与接口来源。
    system_tree_ = new QTreeWidget(this);
    system_tree_->setObjectName("systemTree");
    system_tree_->setColumnCount(3);
    system_tree_->setHeaderLabels({ui_text(texts, "workbench.editor.column_object", "Object"),
                                   ui_text(texts, "workbench.editor.column_kind", "Kind / type"),
                                   ui_text(texts, "workbench.editor.column_provider", "Provider / target")});
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
    remove_button_ = new QPushButton(ui_text(texts, "workbench.editor.remove", "Remove"), this);
    remove_button_->setObjectName("systemRemoveButton");
    connect(remove_button_, &QPushButton::clicked, this, &SystemEditorPanel::remove_selected);
    wire_row->addWidget(remove_button_);
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
    const QString current_scope = selected_scope();
    definition_combo_->clear();
    for (const auto& definition : state.model.available_modules) definition_combo_->addItem(from_utf8(definition));
    if (!current_definition.isEmpty()) {
        const int index = definition_combo_->findText(current_definition);
        if (index >= 0) definition_combo_->setCurrentIndex(index);
    }
    if (instance_edit_->text().isEmpty() && definition_combo_->count() > 0) {
        instance_edit_->setText(
            suggest_instance_name(definition_combo_->currentText(), state, current_scope));
    }

    // 目录节点索引：转接与导出记录用于标注作用域接口来源。
    std::map<std::string, const session::ModuleNodeView*> nodes;
    for (const auto& node : state.model.catalog.modules) nodes[node.path] = &node;
    const session::ModuleNodeView* root = find_node(nodes, {});
    const auto kind_text = [&texts](ascend::SymbolKind kind) {
        return kind == ascend::SymbolKind::method ? ui_text(texts, "workbench.kind.method", "Method")
                                                  : ui_text(texts, "workbench.kind.value", "Value");
    };
    const auto find_forward = [&](const QString& scope, const QString& name) -> const session::ForwardView* {
        const auto* node = find_node(nodes, scope);
        if (node == nullptr) return nullptr;
        for (const auto& forward : node->forwards) {
            if (from_utf8(forward.requirement) == name) return &forward;
        }
        return nullptr;
    };
    const auto find_export = [&](const QString& scope, const QString& name) -> const session::ExportView* {
        const auto* node = find_node(nodes, scope);
        if (node == nullptr) return nullptr;
        for (const auto& symbol : node->exports) {
            if (from_utf8(symbol.name) == name) return &symbol;
        }
        return nullptr;
    };
    const auto find_export_target = [&](const QString& scope, const QString& module,
                                        const QString& symbol) -> const session::ExportView* {
        const auto* node = find_node(nodes, scope);
        if (node == nullptr) return nullptr;
        for (const auto& exported : node->exports) {
            if (from_utf8(exported.target.module) == module &&
                from_utf8(exported.target.symbol) == symbol) {
                return &exported;
            }
        }
        return nullptr;
    };
    struct LinkStatus {
        std::optional<ascend::Reference> direct;
        std::optional<ascend::Reference> forward;
    };
    const auto link_status = [&](const QString& scope, const ascend::Reference& reference) {
        LinkStatus status;
        const auto* node = find_node(nodes, scope);
        if (node == nullptr) return status;
        for (const auto& connection : node->connections) {
            if (connection.requirement.module != reference.module ||
                connection.requirement.symbol != reference.symbol) {
                continue;
            }
            if (connection.forwarded) {
                status.forward = connection.provider;
            } else {
                status.direct = connection.provider;
            }
        }
        return status;
    };

    const QString selected_key = item_key(system_tree_->currentItem());
    system_tree_->clear();
    std::map<std::string, QTreeWidgetItem*> scope_items;
    // 先建作用域节点，父路径按排序先于子路径出现；实例模块路径不是作用域。
    for (const auto& node : state.model.catalog.modules) {
        if (node.path.empty() || !node.scope) continue;
        const QString path = from_utf8(node.path);
        const QString parent = parent_path(path);
        QTreeWidgetItem* item = nullptr;
        if (parent.isEmpty()) {
            item = new QTreeWidgetItem(system_tree_);
        } else {
            const auto found = scope_items.find(parent.toStdString());
            if (found == scope_items.end()) continue;
            item = new QTreeWidgetItem(found->second);
        }
        item->setText(0, leaf_name(path));
        item->setText(1, ui_text(texts, "workbench.editor.kind.scope", "Scope"));
        item->setText(2, ui_text(texts, "workbench.editor.scope_counts", "%1 forwards · %2 exports")
                               .arg(node.forwards.size())
                               .arg(node.exports.size()));
        item->setData(0, kKindRole, QStringLiteral("scope"));
        item->setData(0, kScopeRole, parent);
        item->setData(0, kModuleRole, leaf_name(path));
        item->setData(0, kOwnerScopeRole, path);
        item->setExpanded(true);
        scope_items[node.path] = item;
    }

    // 实例节点：按所属作用域挂到作用域节点或根层。
    std::map<std::string, QTreeWidgetItem*> instance_items;
    for (const auto& instance : state.model.instances) {
        const QString scope = from_utf8(instance.scope);
        const QString name = from_utf8(instance.name);
        const QString full_path = join_scope(scope, name);
        QTreeWidgetItem* item = nullptr;
        if (scope.isEmpty()) {
            item = new QTreeWidgetItem(system_tree_);
        } else {
            const auto found = scope_items.find(scope.toStdString());
            if (found == scope_items.end()) continue;
            item = new QTreeWidgetItem(found->second);
        }
        item->setText(0, name);
        item->setText(1, from_utf8(instance.definition));
        item->setData(0, kKindRole, QStringLiteral("instance"));
        item->setData(0, kScopeRole, scope);
        item->setData(0, kModuleRole, name);
        item->setData(0, kOwnerScopeRole, full_path);
        item->setExpanded(true);
        instance_items[full_path.toStdString()] = item;
    }

    // 公开项与需求：引用在父作用域内检索，作用域接口额外标注来源。
    const auto add_requirements = [&](QTreeWidgetItem* owner, const QString& connection_scope,
                                      const QString& owner_scope, const QString& module,
                                      const QString& full_module, bool scope_interface) {
        const auto* node = find_node(nodes, connection_scope);
        if (node == nullptr) return;
        for (const auto& requirement : node->requirements) {
            if (from_utf8(requirement.reference.module) != full_module) continue;
            const QString symbol = from_utf8(requirement.reference.symbol);
            auto* child = new QTreeWidgetItem(owner);
            child->setText(0, symbol);
            child->setText(1, QStringLiteral("%1 · %2").arg(kind_text(requirement.kind),
                                                            from_utf8(requirement.result_type)));
            const auto link = link_status(connection_scope, requirement.reference);
            if (scope_interface) {
                const auto* forward = find_forward(owner_scope, symbol);
                QString detail = forward != nullptr
                                     ? ui_text(texts, "workbench.editor.forward_target", "forward → %1")
                                           .arg(reference_text(forward->target))
                                     : ui_text(texts, "workbench.editor.unconnected", "not connected");
                if (link.direct.has_value()) {
                    detail += QStringLiteral(" · ") + reference_text(*link.direct);
                } else if (link.forward.has_value()) {
                    detail += QStringLiteral(" · ") +
                              ui_text(texts, "workbench.editor.forwarded_as", "forwarded as %1")
                                  .arg(from_utf8(link.forward->symbol));
                }
                child->setText(2, detail);
                child->setData(0, kInterfaceRole, QStringLiteral("forward"));
                child->setData(0, kForwardedRole, link.forward.has_value() ? 1 : 0);
            } else if (link.direct.has_value()) {
                child->setText(2, reference_text(*link.direct));
            } else if (link.forward.has_value()) {
                child->setText(2, ui_text(texts, "workbench.editor.forwarded_as", "forwarded as %1")
                                      .arg(from_utf8(link.forward->symbol)));
                child->setData(0, kForwardedRole, 1);
            } else {
                child->setText(2, ui_text(texts, "workbench.editor.unconnected", "not connected"));
            }
            child->setData(0, kKindRole, QStringLiteral("requirement"));
            child->setData(0, kScopeRole, connection_scope);
            child->setData(0, kModuleRole, module);
            child->setData(0, kSymbolRole, symbol);
            child->setData(0, kConnectedRole, link.direct.has_value() ? 1 : 0);
            child->setData(0, kOwnerScopeRole, owner_scope);
        }
    };
    const auto add_declarations = [&](QTreeWidgetItem* owner, const QString& containing_scope,
                                      const QString& owner_scope, const QString& module,
                                      const QString& full_module, bool scope_export) {
        const auto* node = find_node(nodes, containing_scope);
        if (node == nullptr) return;
        for (const auto& declaration : node->declarations) {
            if (from_utf8(declaration.reference.module) != full_module) continue;
            const QString symbol = from_utf8(declaration.reference.symbol);
            auto* child = new QTreeWidgetItem(owner);
            child->setText(0, symbol);
            child->setText(1, QStringLiteral("%1 · %2").arg(kind_text(declaration.kind),
                                                            from_utf8(declaration.result_type)));
            if (scope_export) {
                const auto* symbol_export = find_export(owner_scope, symbol);
                QString detail = symbol_export != nullptr
                                     ? ui_text(texts, "workbench.editor.export_target", "export → %1")
                                           .arg(reference_text(symbol_export->target))
                                     : ui_text(texts, "workbench.editor.export_target", "export → %1")
                                           .arg(QString());
                if (const auto* parent_export = find_export_target(containing_scope, module, symbol);
                    parent_export != nullptr) {
                    detail += QStringLiteral(" · ") +
                              ui_text(texts, "workbench.editor.exported_as", "exported as %1")
                                  .arg(from_utf8(parent_export->name));
                    child->setData(0, kExportedRole, 1);
                }
                child->setText(2, detail);
                child->setData(0, kInterfaceRole, QStringLiteral("export"));
            } else if (const auto* source_export =
                           find_export_target(containing_scope, module, symbol);
                       source_export != nullptr) {
                child->setText(2, ui_text(texts, "workbench.editor.exported_as", "exported as %1")
                                      .arg(from_utf8(source_export->name)));
                child->setData(0, kExportedRole, 1);
            }
            child->setData(0, kKindRole, QStringLiteral("declaration"));
            child->setData(0, kScopeRole, containing_scope);
            child->setData(0, kModuleRole, module);
            child->setData(0, kSymbolRole, symbol);
            child->setData(0, kOwnerScopeRole, owner_scope);
        }
    };

    for (const auto& instance : state.model.instances) {
        const QString scope = from_utf8(instance.scope);
        const QString name = from_utf8(instance.name);
        const QString full_path = join_scope(scope, name);
        const auto found = instance_items.find(full_path.toStdString());
        if (found == instance_items.end()) continue;
        add_requirements(found->second, scope, scope, name, full_path, false);
        add_declarations(found->second, scope, scope, name, full_path, false);
    }
    for (const auto& node : state.model.catalog.modules) {
        if (node.path.empty() || !node.scope) continue;
        const QString path = from_utf8(node.path);
        const QString parent = parent_path(path);
        const QString name = leaf_name(path);
        const auto found = scope_items.find(node.path);
        if (found == scope_items.end()) continue;
        add_requirements(found->second, parent, path, name, path, true);
        add_declarations(found->second, parent, path, name, path, true);
    }

    // 规格：推进入口（根作用域公开无参方法）与观测（根作用域公开量，含子作用域导出）。
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
    if (auto* item = find_item_by_key(system_tree_, selected_key)) {
        system_tree_->setCurrentItem(item);
    }
    editor_updating_ = false;
    on_system_selection_changed();
}

void SystemEditorPanel::on_system_selection_changed() {
    if (editor_updating_ || provider_combo_ == nullptr) return;
    const WorkspaceState& state = context_.workspace->state();
    editor_updating_ = true;
    provider_combo_->clear();
    const QString kind = selected_kind();
    const QString previous_default = interface_default_;
    interface_default_.clear();

    if (kind == QStringLiteral("requirement")) {
        // 连接在需求可见的父作用域内建立；作用域接口条目的新增目标则是其自身作用域。
        const QString scope = system_tree_->currentItem()->data(0, kScopeRole).toString();
        const QString module = selected_module();
        const QString symbol = selected_symbol();
        const QString full = join_scope(scope, module);
        std::map<std::string, const session::ModuleNodeView*> nodes;
        for (const auto& node : state.model.catalog.modules) nodes[node.path] = &node;
        const auto found = nodes.find(scope.toStdString());
        if (found != nodes.end()) {
            const session::ModuleNodeView* node = found->second;
            const session::RequirementView* requirement = nullptr;
            for (const auto& candidate : node->requirements) {
                if (from_utf8(candidate.reference.module) == full &&
                    from_utf8(candidate.reference.symbol) == symbol) {
                    requirement = &candidate;
                    break;
                }
            }
            if (requirement != nullptr) {
                // 候选提供方：同一作用域中种类、结果类型与参数签名一致的其他直接子模块符号。
                for (const auto& declaration : node->declarations) {
                    if (from_utf8(declaration.reference.module) == full) continue;
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
        interface_default_ = symbol;
    } else if (kind == QStringLiteral("declaration")) {
        interface_default_ = selected_symbol();
    }

    if (!interface_default_.isEmpty() &&
        (interface_edit_->text().isEmpty() || interface_edit_->text() == previous_default)) {
        interface_edit_->setText(interface_default_);
    } else if (interface_default_.isEmpty() && interface_edit_->text() == previous_default) {
        interface_edit_->clear();
    }
    editor_updating_ = false;
    refresh_controls();
}

QString SystemEditorPanel::selected_kind() const {
    QTreeWidgetItem* item = system_tree_->currentItem();
    return item != nullptr ? item->data(0, kKindRole).toString() : QString();
}

QString SystemEditorPanel::selected_scope() const {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr) return {};
    const QString kind = item->data(0, kKindRole).toString();
    if (kind == QStringLiteral("scope")) return item->data(0, kOwnerScopeRole).toString();
    const QString interface = item->data(0, kInterfaceRole).toString();
    if (interface == QStringLiteral("forward") || interface == QStringLiteral("export")) {
        // 作用域接口条目显示在作用域节点下；新增模块/作用域落在该作用域内。
        return item->data(0, kOwnerScopeRole).toString();
    }
    return item->data(0, kScopeRole).toString();
}

QString SystemEditorPanel::selected_module() const {
    QTreeWidgetItem* item = system_tree_->currentItem();
    return item != nullptr ? item->data(0, kModuleRole).toString() : QString();
}

QString SystemEditorPanel::selected_symbol() const {
    QTreeWidgetItem* item = system_tree_->currentItem();
    return item != nullptr ? item->data(0, kSymbolRole).toString() : QString();
}

QString SystemEditorPanel::selected_interface() const {
    QTreeWidgetItem* item = system_tree_->currentItem();
    return item != nullptr ? item->data(0, kInterfaceRole).toString() : QString();
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
    scope_edit_->setEnabled(editable);
    add_scope_button_->setEnabled(editable && !scope_edit_->text().trimmed().isEmpty());
    interface_edit_->setEnabled(editable && (selected_kind() == QStringLiteral("requirement") ||
                                             selected_kind() == QStringLiteral("declaration")));
    advance_combo_->setEnabled(editable);
    observation_list_->setEnabled(editable);

    QTreeWidgetItem* item = system_tree_->currentItem();
    const QString kind = selected_kind();
    const bool requirement = kind == QStringLiteral("requirement");
    const bool declaration = kind == QStringLiteral("declaration");
    const bool instance = kind == QStringLiteral("instance");
    const bool scope = kind == QStringLiteral("scope");
    const QString interface = selected_interface();
    // 转接/导出动作落在连接作用域（父作用域）；作用域接口条目可在父作用域继续转接/导出。
    const QString connection_scope =
        item != nullptr ? item->data(0, kScopeRole).toString() : QString();
    const bool root_scope = connection_scope.isEmpty();
    const bool has_interface_name = !interface_edit_->text().trimmed().isEmpty();
    const bool forwarded = item != nullptr && item->data(0, kForwardedRole).toInt() == 1;
    const bool connected = item != nullptr && item->data(0, kConnectedRole).toInt() == 1;
    const bool exported = item != nullptr && item->data(0, kExportedRole).toInt() == 1;

    forward_button_->setEnabled(editable && requirement && !forwarded && !connected &&
                                !root_scope && has_interface_name);
    export_button_->setEnabled(editable && declaration && !exported && !root_scope &&
                               has_interface_name);
    remove_button_->setEnabled(editable && (instance || scope || requirement ||
                                                    (declaration && interface == QStringLiteral("export"))));
    provider_combo_->setEnabled(editable && requirement && !forwarded);
    connect_button_->setEnabled(editable && requirement && !forwarded &&
                                provider_combo_->count() > 0);
    disconnect_button_->setEnabled(editable && requirement && !forwarded && connected);
}

void SystemEditorPanel::refresh_controls() { update_controls(shell_closing_); }

void SystemEditorPanel::on_system_definition_changed() {
    if (editor_updating_) return;
    if (definition_combo_->count() == 0) return;
    instance_edit_->setText(suggest_instance_name(definition_combo_->currentText(),
                                                  context_.workspace->state(), selected_scope()));
    refresh_controls();
}

void SystemEditorPanel::add_scope() {
    const QString name = scope_edit_->text().trimmed();
    if (name.isEmpty()) return;
    context_.add_scope(name, selected_scope());
    scope_edit_->clear();
}

void SystemEditorPanel::add_module() {
    if (definition_combo_->count() == 0) return;
    const QString definition = definition_combo_->currentText();
    const QString scope = selected_scope();
    QString instance = instance_edit_->text().trimmed();
    if (instance.isEmpty()) {
        instance = suggest_instance_name(definition, context_.workspace->state(), scope);
    }
    context_.add_module(definition, instance, scope);
}

void SystemEditorPanel::remove_selected() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr) return;
    const QString kind = item->data(0, kKindRole).toString();
    const QString scope = item->data(0, kScopeRole).toString();
    const QString module = item->data(0, kModuleRole).toString();
    const QString symbol = item->data(0, kSymbolRole).toString();
    const QString owner = item->data(0, kOwnerScopeRole).toString();
    const QString interface = item->data(0, kInterfaceRole).toString();
    if (kind == QStringLiteral("scope")) {
        context_.remove_scope(module, scope);
    } else if (kind == QStringLiteral("instance")) {
        context_.remove_module(module, scope);
    } else if (kind == QStringLiteral("requirement")) {
        if (interface == QStringLiteral("forward")) {
            context_.remove_forward(symbol, owner);
        } else {
            context_.remove_module(module, scope);
        }
    } else if (kind == QStringLiteral("declaration") && interface == QStringLiteral("export")) {
        context_.remove_export(symbol, owner);
    }
}

void SystemEditorPanel::connect_requirement() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr || selected_kind() != QStringLiteral("requirement")) return;
    const QString provider = provider_combo_->currentData().toString();
    if (provider.isEmpty()) return;
    const int slash = provider.lastIndexOf(QLatin1Char('/'));
    if (slash <= 0) return;
    context_.connect_requirement(item->data(0, kModuleRole).toString(),
                                 item->data(0, kSymbolRole).toString(), provider.left(slash),
                                 provider.mid(slash + 1), item->data(0, kScopeRole).toString());
}

void SystemEditorPanel::disconnect_requirement() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr || selected_kind() != QStringLiteral("requirement")) return;
    context_.disconnect_requirement(item->data(0, kModuleRole).toString(),
                                    item->data(0, kSymbolRole).toString(),
                                    item->data(0, kScopeRole).toString());
}

void SystemEditorPanel::forward_requirement() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr || selected_kind() != QStringLiteral("requirement")) return;
    const QString name = interface_edit_->text().trimmed();
    if (name.isEmpty()) return;
    context_.forward_requirement(name, item->data(0, kModuleRole).toString(),
                                 item->data(0, kSymbolRole).toString(),
                                 item->data(0, kScopeRole).toString());
}

void SystemEditorPanel::export_symbol() {
    QTreeWidgetItem* item = system_tree_->currentItem();
    if (item == nullptr || selected_kind() != QStringLiteral("declaration")) return;
    const QString name = interface_edit_->text().trimmed();
    if (name.isEmpty()) return;
    context_.export_symbol(name, item->data(0, kModuleRole).toString(),
                           item->data(0, kSymbolRole).toString(),
                           item->data(0, kScopeRole).toString());
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
