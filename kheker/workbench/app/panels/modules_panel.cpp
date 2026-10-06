#include "modules_panel.hpp"

#include "ui_format.hpp"

#include <QFont>
#include <QHeaderView>
#include <QTabWidget>
#include <QTreeWidget>
#include <QVBoxLayout>

#include <vector>

namespace ascend::workbench {
namespace {

// 分组显示时的局部引用：去掉所属模块前缀（模块自身显示为符号名）。
QString local_reference_text(const ascend::Reference& reference, const std::string& group) {
    if (group.empty()) return reference_text(reference);
    if (reference.module == group) {
        ascend::Reference local = reference;
        local.module.clear();
        return reference_text(local);
    }
    if (reference.module.rfind(group + "/", 0) == 0) {
        ascend::Reference local = reference;
        local.module = local.module.substr(group.size() + 1);
        return reference_text(local);
    }
    return reference_text(reference);
}

// 公开声明详情：由选中项生成 HTML 后经外壳转交右侧状态面板展示。
QString declaration_details_html(const UiTexts& texts, const session::DeclarationView& declaration) {
    QString html;
    html += QStringLiteral("<h3>%1</h3>").arg(reference_text(declaration.reference).toHtmlEscaped());
    html += ui_text(texts, "workbench.detail.kind_type_suffix", "<p><b>Kind:</b> %1 · <b>Type:</b> %2%3</p>")
                .arg(declaration.kind == ascend::SymbolKind::method ? ui_text(texts, "workbench.kind.public_method", "Public method")
                                                                     : ui_text(texts, "workbench.kind.value", "Value"))
                .arg(from_utf8(declaration.result_type).toHtmlEscaped())
                .arg(declaration.result_supported ? QString() : ui_text(texts, "workbench.detail.unsupported_suffix", " (no registered adapter)"));
    if (!declaration.parameters.empty()) {
        html += ui_text(texts, "workbench.detail.parameters", "<p><b>Parameters:</b><br>");
        for (const auto& parameter : declaration.parameters) {
            html += QStringLiteral("· %1：%2%3<br>")
                        .arg(from_utf8(parameter.name).toHtmlEscaped())
                        .arg(from_utf8(parameter.type).toHtmlEscaped())
                        .arg(parameter.supported ? QString() : ui_text(texts, "workbench.detail.unsupported_suffix", " (no registered adapter)"));
        }
        html += QStringLiteral("</p>");
    }
    if (!declaration.description.empty()) {
        html += ui_text(texts, "workbench.detail.description", "<p><b>Description:</b> %1</p>").arg(from_utf8(declaration.description).toHtmlEscaped());
    }
    if (!declaration.contract.empty()) {
        html += ui_text(texts, "workbench.detail.contract", "<p><b>Contract:</b> %1</p>").arg(from_utf8(declaration.contract).toHtmlEscaped());
    }
    if (!declaration.reads.empty() || !declaration.writes.empty()) {
        QString reads;
        for (const auto& item : declaration.reads) reads += reference_text(item).toHtmlEscaped() + " ";
        QString writes;
        for (const auto& item : declaration.writes) writes += reference_text(item).toHtmlEscaped() + " ";
        html += ui_text(texts, "workbench.detail.reads_writes", "<p><b>Declared reads:</b> %1<br><b>Declared writes:</b> %2</p>")
                    .arg(reads.isEmpty() ? QStringLiteral("—") : reads)
                    .arg(writes.isEmpty() ? QStringLiteral("—") : writes);
        html += ui_text(texts, "workbench.detail.declaration_note", "<p style='color:#666'>Declared relations describe the model structure; the execution path is shown by run records; declarations are not a proof of actual access.</p>");
    }
    return html;
}

// 需求详情：由选中项生成 HTML 后经外壳转交右侧状态面板展示。
QString requirement_details_html(const UiTexts& texts, const session::RequirementView& requirement) {
    QString html;
    html += ui_text(texts, "workbench.detail.requirement_title", "<h3>Requirement %1</h3>")
                .arg(reference_text(requirement.reference).toHtmlEscaped());
    html += ui_text(texts, "workbench.detail.kind_type", "<p><b>Kind:</b> %1 · <b>Type:</b> %2</p>")
                .arg(requirement.kind == ascend::SymbolKind::method ? ui_text(texts, "workbench.kind.method", "Method")
                                                                    : ui_text(texts, "workbench.kind.value", "Value"))
                .arg(from_utf8(requirement.result_type).toHtmlEscaped());
    if (!requirement.contract.empty()) {
        html += ui_text(texts, "workbench.detail.contract", "<p><b>Contract:</b> %1</p>")
                    .arg(from_utf8(requirement.contract).toHtmlEscaped());
    }
    if (!requirement.description.empty()) {
        html += ui_text(texts, "workbench.detail.description", "<p><b>Description:</b> %1</p>")
                    .arg(from_utf8(requirement.description).toHtmlEscaped());
    }
    return html;
}

}  // namespace

ModulesPanel::ModulesPanel(const PanelContext& context, QWidget* parent)
    : QWidget(parent), context_(context) {
    const UiTexts& texts = *context_.texts;
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(0, 0, 0, 0);

    tabs_ = new QTabWidget(this);
    tabs_->setObjectName("leftPane");
    tabs_->setMinimumWidth(260);

    module_tree_ = new QTreeWidget(tabs_);
    module_tree_->setObjectName("moduleTree");
    module_tree_->setColumnCount(2);
    module_tree_->setHeaderLabels({ui_text(texts, "workbench.table.module_symbol", "Module / symbol"),
                                   ui_text(texts, "workbench.table.type", "Type")});
    module_tree_->header()->setStretchLastSection(false);
    module_tree_->header()->setSectionResizeMode(0, QHeaderView::Stretch);
    module_tree_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    tabs_->addTab(module_tree_, ui_text(texts, "workbench.pane.modules", "Module tree"));

    spec_tree_ = new QTreeWidget(tabs_);
    spec_tree_->setObjectName("specTree");
    spec_tree_->setHeaderHidden(true);
    tabs_->addTab(spec_tree_, ui_text(texts, "workbench.pane.spec", "Spec"));

    requirements_tree_ = new QTreeWidget(tabs_);
    requirements_tree_->setObjectName("requirementsTree");
    requirements_tree_->setColumnCount(3);
    requirements_tree_->setHeaderLabels({ui_text(texts, "workbench.table.symbol", "Symbol"),
                                         ui_text(texts, "workbench.table.type", "Type"),
                                         ui_text(texts, "workbench.table.description", "Description")});
    requirements_tree_->header()->setStretchLastSection(false);
    requirements_tree_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    requirements_tree_->header()->setSectionResizeMode(1, QHeaderView::ResizeToContents);
    requirements_tree_->header()->setSectionResizeMode(2, QHeaderView::Stretch);
    tabs_->addTab(requirements_tree_, ui_text(texts, "workbench.pane.requirements", "Requirements"));

    connections_tree_ = new QTreeWidget(tabs_);
    connections_tree_->setObjectName("connectionsTree");
    connections_tree_->setColumnCount(3);
    connections_tree_->setHeaderLabels({ui_text(texts, "workbench.table.requirement", "Requirement"),
                                        ui_text(texts, "workbench.table.provider", "Provider"),
                                        ui_text(texts, "workbench.table.forwarded", "Forwarded")});
    connections_tree_->header()->setStretchLastSection(false);
    connections_tree_->header()->setSectionResizeMode(0, QHeaderView::ResizeToContents);
    connections_tree_->header()->setSectionResizeMode(1, QHeaderView::Stretch);
    connections_tree_->header()->setSectionResizeMode(2, QHeaderView::ResizeToContents);
    tabs_->addTab(connections_tree_, ui_text(texts, "workbench.pane.connections", "Connections"));

    connect(module_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = module_tree_->selectedItems();
        if (selected.isEmpty()) return;
        QTreeWidgetItem* item = selected.front();
        if (item->data(0, Qt::UserRole + 1).toInt() != 1) return;
        show_declaration_at(item->data(0, Qt::UserRole).toInt());
    });
    connect(requirements_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = requirements_tree_->selectedItems();
        if (selected.isEmpty()) return;
        QTreeWidgetItem* item = selected.front();
        if (item->data(0, Qt::UserRole + 1).toInt() != 1) return;
        show_requirement_at(item->data(0, Qt::UserRole).toInt());
    });
    connect(connections_tree_, &QTreeWidget::itemSelectionChanged, this, [this] {
        const auto selected = connections_tree_->selectedItems();
        if (selected.isEmpty()) return;
        QTreeWidgetItem* item = selected.front();
        if (item->data(0, Qt::UserRole + 1).toInt() != 1) return;
        QString html = ui_text(*context_.texts, "workbench.detail.connection_title", "<h3>Connection</h3><p>%1</p>")
                           .arg(QStringLiteral("%1 ← %2").arg(item->text(0), item->text(1)).toHtmlEscaped());
        html += ui_text(*context_.texts, "workbench.detail.connection_note",
                        "<p style='color:#666'>Connections show the actual assembly; the environment implementation defines execution order; wiring does not imply a complete causal graph.</p>");
        context_.show_details(html);
    });

    layout->addWidget(tabs_);
}

void ModulesPanel::render() {
    const WorkspaceState& state = context_.workspace->state();
    rebuild_module_tree();
    tabs_->setTabText(0, state.model.catalog_stale
                             ? ui_text(*context_.texts, "workbench.pane.modules_stale", "Module tree (stale)")
                             : ui_text(*context_.texts, "workbench.pane.modules", "Module tree"));
    rebuild_spec();
    rebuild_requirements();
    rebuild_connections();
}

QTreeWidgetItem* ModulesPanel::module_node_for(QTreeWidget* tree, std::map<std::string, QTreeWidgetItem*>& nodes,
                                              const std::string& path) {
    const auto found = nodes.find(path);
    if (found != nodes.end()) return found->second;
    QTreeWidgetItem* parent = nullptr;
    if (!path.empty()) {
        const auto slash = path.rfind('/');
        parent = module_node_for(tree, nodes, slash == std::string::npos ? std::string{} : path.substr(0, slash));
    }
    auto* node = parent == nullptr ? new QTreeWidgetItem(tree) : new QTreeWidgetItem(parent);
    if (path.empty()) {
        node->setText(0, ui_text(*context_.texts, "workbench.tree.root_scope", "(root scope)"));
    } else {
        const auto slash = path.rfind('/');
        node->setText(0, from_utf8(slash == std::string::npos ? path : path.substr(slash + 1)));
        node->setToolTip(0, from_utf8(path));
    }
    node->setData(0, Qt::UserRole + 1, 0);
    nodes.emplace(path, node);
    return node;
}

void ModulesPanel::rebuild_module_tree() {
    module_tree_->clear();

    // 目录里每个公开符号只出现一次，按声明模块（reference.module）分组。
    struct Entry {
        int index;
        const session::DeclarationView* declaration;
    };
    std::map<std::string, std::vector<Entry>> by_module;
    int index = 0;
    for (const auto& module : context_.workspace->state().model.catalog.modules) {
        for (const auto& declaration : module.declarations) {
            by_module[declaration.reference.module].push_back(Entry{index++, &declaration});
        }
    }

    // 作用域按路径排序，父模块先于子模块；节点创建后先挂本模块的公开符号，
    // 子模块节点随后追加，形成“模块 → 符号 → 子模块”的结构。
    QFont method_font = module_tree_->font();
    method_font.setItalic(true);
    std::map<std::string, QTreeWidgetItem*> nodes;
    for (const auto& module : context_.workspace->state().model.catalog.modules) {
        auto* node = module_node_for(module_tree_, nodes, module.path);

        const auto found = by_module.find(module.path);
        if (found == by_module.end()) continue;
        const auto add_declaration = [&](const Entry& entry) {
            const auto& declaration = *entry.declaration;
            const bool method = declaration.kind == ascend::SymbolKind::method;
            auto* item = new QTreeWidgetItem(node);
            item->setText(0, method ? from_utf8(declaration.reference.symbol) + "()"
                                    : from_utf8(declaration.reference.symbol));
            if (method) item->setFont(0, method_font);
            item->setText(1, from_utf8(declaration.result_type));
            if (!declaration.description.empty()) item->setToolTip(0, from_utf8(declaration.description));
            item->setData(0, Qt::UserRole, entry.index);
            item->setData(0, Qt::UserRole + 1, 1);
        };
        for (const auto& entry : found->second) {
            if (entry.declaration->kind == ascend::SymbolKind::method) add_declaration(entry);
        }
        for (const auto& entry : found->second) {
            if (entry.declaration->kind != ascend::SymbolKind::method) add_declaration(entry);
        }
    }
    module_tree_->expandAll();
}

void ModulesPanel::rebuild_spec() {
    spec_tree_->clear();
    const UiTexts& texts = *context_.texts;
    const auto& spec = context_.workspace->state().model.spec;
    const auto add_group = [&](const char* key, const char* fallback) {
        auto* group = new QTreeWidgetItem(spec_tree_);
        group->setText(0, ui_text(texts, key, fallback));
        group->setData(0, Qt::UserRole + 1, 0);
        return group;
    };
    const auto add_entry = [&](QTreeWidgetItem* group, const QString& text) {
        auto* item = new QTreeWidgetItem(group);
        item->setText(0, text);
        item->setData(0, Qt::UserRole + 1, 1);
    };
    auto* advance_group = add_group("workbench.spec.advance", "Advance entry");
    const auto& advance = spec.advance;
    add_entry(advance_group,
              advance.module.empty() && advance.symbol.empty()
                  ? ui_text(texts, "workbench.spec.no_advance", "Not selected")
                  : reference_text(advance));
    auto* inputs_group =
        add_group("workbench.spec.inputs", "Inputs (driven in spec order before each advance)");
    for (const auto& entry : spec.inputs) {
        add_entry(inputs_group, QStringLiteral("%1 → %2").arg(from_utf8(entry.first), reference_text(entry.second)));
    }
    auto* observations_group = add_group("workbench.spec.observations", "Observations");
    for (const auto& entry : spec.observations) {
        add_entry(observations_group,
                  QStringLiteral("%1 → %2").arg(from_utf8(entry.first), reference_text(entry.second)));
    }
    spec_tree_->expandAll();
}

void ModulesPanel::rebuild_requirements() {
    requirements_tree_->clear();
    std::map<std::string, QTreeWidgetItem*> nodes;
    int index = 0;
    for (const auto& module : context_.workspace->state().model.catalog.modules) {
        for (const auto& requirement : module.requirements) {
            auto* node = module_node_for(requirements_tree_, nodes, requirement.reference.module);
            auto* item = new QTreeWidgetItem(node);
            item->setText(0, from_utf8(requirement.reference.symbol));
            item->setText(1, from_utf8(requirement.result_type));
            item->setText(2, from_utf8(requirement.description));
            if (!requirement.description.empty()) item->setToolTip(2, from_utf8(requirement.description));
            item->setData(0, Qt::UserRole, index++);
            item->setData(0, Qt::UserRole + 1, 1);
        }
    }
    requirements_tree_->expandAll();
}

void ModulesPanel::rebuild_connections() {
    connections_tree_->clear();
    std::map<std::string, QTreeWidgetItem*> nodes;
    for (const auto& module : context_.workspace->state().model.catalog.modules) {
        for (const auto& connection : module.connections) {
            auto* node = module_node_for(connections_tree_, nodes, module.path);
            auto* item = new QTreeWidgetItem(node);
            item->setText(0, local_reference_text(connection.requirement, module.path));
            item->setText(1, local_reference_text(connection.provider, module.path));
            item->setText(2, connection.forwarded ? ui_text(*context_.texts, "workbench.value.yes", "Yes")
                                                  : ui_text(*context_.texts, "workbench.value.no", "No"));
            item->setToolTip(0, reference_text(connection.requirement));
            item->setToolTip(1, reference_text(connection.provider));
            item->setData(0, Qt::UserRole + 1, 1);
        }
    }
    connections_tree_->expandAll();
}

void ModulesPanel::show_declaration_at(int index) {
    int cursor = 0;
    for (const auto& module : context_.workspace->state().model.catalog.modules) {
        for (const auto& declaration : module.declarations) {
            if (cursor++ == index) {
                context_.show_details(declaration_details_html(*context_.texts, declaration));
                return;
            }
        }
    }
}

void ModulesPanel::show_requirement_at(int index) {
    int cursor = 0;
    for (const auto& module : context_.workspace->state().model.catalog.modules) {
        for (const auto& requirement : module.requirements) {
            if (cursor++ == index) {
                context_.show_details(requirement_details_html(*context_.texts, requirement));
                return;
            }
        }
    }
}

}  // namespace ascend::workbench
