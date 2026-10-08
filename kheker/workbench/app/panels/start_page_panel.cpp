#include "start_page_panel.hpp"

#include <QFont>
#include <QHBoxLayout>
#include <QLabel>
#include <QPushButton>
#include <QVBoxLayout>

namespace ascend::workbench {

StartPagePanel::StartPagePanel(const PanelContext& context, QWidget* parent)
    : QWidget(parent), context_(context) {
    const UiTexts& texts = *context_.texts;
    setObjectName("startPage");
    auto* layout = new QVBoxLayout(this);
    layout->setContentsMargins(28, 28, 28, 28);
    layout->setSpacing(10);
    layout->addStretch(1);

    auto* title = new QLabel(ui_text(texts, "workbench.app.display_name", "Ascend Causal Modeling Workbench"), this);
    QFont title_font = title->font();
    title_font.setPointSize(title_font.pointSize() + 6);
    title_font.setBold(true);
    title->setFont(title_font);
    layout->addWidget(title);
    auto* subtitle = new QLabel(ui_text(texts, "workbench.start.subtitle", "Start"), this);
    subtitle->setStyleSheet(QStringLiteral("color: #666;"));
    layout->addWidget(subtitle);

    auto* buttons = new QHBoxLayout();
    new_button_ = new QPushButton(ui_text(texts, "workbench.start.new_research", "New research…"), this);
    new_button_->setObjectName("startNewResearchButton");
    connect(new_button_, &QPushButton::clicked, this, [this] { context_.new_research(); });
    buttons->addWidget(new_button_);
    open_button_ = new QPushButton(ui_text(texts, "workbench.start.open_project", "Open project…"), this);
    open_button_->setObjectName("startOpenProjectButton");
    connect(open_button_, &QPushButton::clicked, this, [this] { context_.open_research(); });
    buttons->addWidget(open_button_);
    example_button_ = new QPushButton(ui_text(texts, "workbench.start.open_example", "Open built-in example"), this);
    example_button_->setObjectName("startOpenExampleButton");
    connect(example_button_, &QPushButton::clicked, this, [this] { context_.open_example(); });
    buttons->addWidget(example_button_);
    buttons->addStretch(1);
    layout->addLayout(buttons);

    auto* guide = new QLabel(
        ui_text(texts, "workbench.start.guide",
                "Module packages (.amod) are loaded into the module library.\n"
                "A causal system (.aasm) picks modules and wires them; the system editor edits it.\n"
                "A research (.aexp) adds the experiment specification and run records on top of a system.\n"
                "Creating a research asks you to create a new causal system or open an existing one."),
        this);
    guide->setWordWrap(true);
    guide->setStyleSheet(QStringLiteral("color: #444;"));
    layout->addWidget(guide);
    layout->addStretch(2);
}

void StartPagePanel::set_open_enabled(bool enabled) {
    new_button_->setEnabled(enabled);
    open_button_->setEnabled(enabled);
    example_button_->setEnabled(enabled);
}

}  // namespace ascend::workbench
