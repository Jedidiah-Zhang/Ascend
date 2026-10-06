#include "package_library_panel.hpp"

#include "ui_format.hpp"

#include <QHBoxLayout>
#include <QHeaderView>
#include <QMessageBox>
#include <QPushButton>
#include <QTableWidget>
#include <QVBoxLayout>

namespace ascend::workbench {

PackageLibraryPanel::PackageLibraryPanel(const PanelContext& context, QWidget* parent)
    : QWidget(parent), context_(context) {
    const UiTexts& texts = *context_.texts;
    setObjectName("packagesPage");
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(4, 4, 4, 4);
    layout->setSpacing(4);
    auto* actions = new QHBoxLayout();
    load_button_ = new QPushButton(ui_text(texts, "workbench.action.module_load", "Load…"), this);
    load_button_->setObjectName("moduleLoadButton");
    connect(load_button_, &QPushButton::clicked, this, [this] { context_.load_packages(); });
    actions->addWidget(load_button_);
    load_folder_button_ = new QPushButton(ui_text(texts, "workbench.action.module_load_folder", "Load folder…"), this);
    load_folder_button_->setObjectName("moduleLoadFolderButton");
    connect(load_folder_button_, &QPushButton::clicked, this, [this] { context_.load_package_folder(); });
    actions->addWidget(load_folder_button_);
    unload_button_ = new QPushButton(ui_text(texts, "workbench.action.module_unload", "Unload"), this);
    unload_button_->setObjectName("moduleUnloadButton");
    connect(unload_button_, &QPushButton::clicked, this, [this] {
        const QString definition = selected_definition();
        if (definition.isEmpty()) {
            QMessageBox::information(this,
                                     ui_text(*context_.texts, "workbench.module.unload_title", "Unload module package"),
                                     ui_text(*context_.texts, "workbench.module.select_first", "Select a loaded module package first."));
            return;
        }
        context_.unload_package(definition);
    });
    actions->addWidget(unload_button_);
    actions->addStretch(1);
    layout->addLayout(actions);
    table_ = new QTableWidget(this);
    table_->setObjectName("modulePackageTable");
    table_->setColumnCount(7);
    table_->setHorizontalHeaderLabels(
        {ui_text(texts, "workbench.table.package_definition", "Definition"),
         ui_text(texts, "workbench.table.package_version", "Version"),
         ui_text(texts, "workbench.table.package_implementation", "Implementation"),
         ui_text(texts, "workbench.table.package_state", "State"),
         ui_text(texts, "workbench.table.package_declarations", "Declarations"),
         ui_text(texts, "workbench.table.package_requirements", "Requirements"),
         ui_text(texts, "workbench.table.package_resources", "Resources")});
    table_->setSelectionBehavior(QAbstractItemView::SelectRows);
    table_->setSelectionMode(QAbstractItemView::SingleSelection);
    table_->setEditTriggers(QAbstractItemView::NoEditTriggers);
    table_->verticalHeader()->setVisible(false);
    table_->horizontalHeader()->setStretchLastSection(true);
    table_->horizontalHeader()->setSectionResizeMode(0, QHeaderView::Stretch);
    layout->addWidget(table_, 1);
}

void PackageLibraryPanel::render(const std::vector<session::ModulePackageView>& packages) {
    table_->setRowCount(0);
    for (const auto& package : packages) {
        const int row = table_->rowCount();
        table_->insertRow(row);
        table_->setItem(row, 0, new QTableWidgetItem(from_utf8(package.definition)));
        table_->setItem(row, 1, new QTableWidgetItem(from_utf8(package.version)));
        table_->setItem(row, 2, new QTableWidgetItem(from_utf8(package.implementation)));
        const QString state = package.stateless
                                  ? ui_text(*context_.texts, "workbench.module.stateless", "Stateless")
                                  : from_utf8(package.state_contract);
        table_->setItem(row, 3, new QTableWidgetItem(state));
        table_->setItem(row, 4, new QTableWidgetItem(QString::number(package.declarations)));
        table_->setItem(row, 5, new QTableWidgetItem(QString::number(package.requirements)));
        table_->setItem(row, 6, new QTableWidgetItem(QString::number(package.resources)));
    }
}

void PackageLibraryPanel::set_load_enabled(bool enabled) {
    load_button_->setEnabled(enabled);
    load_folder_button_->setEnabled(enabled);
}

void PackageLibraryPanel::set_unload_enabled(bool enabled) { unload_button_->setEnabled(enabled); }

QString PackageLibraryPanel::selected_definition() const {
    const auto selected = table_->selectedItems();
    if (selected.isEmpty()) return {};
    const QTableWidgetItem* item = table_->item(selected.front()->row(), 0);
    return item != nullptr ? item->text() : QString();
}

}  // namespace ascend::workbench
