#include "config_editor.hpp"

#include <ascend/engine.hpp>

#include <QLabel>
#include <QLineEdit>
#include <QFormLayout>

#include <memory>
#include <utility>

namespace ascend::workbench {
namespace {

QString display(const ascend::Config& value);

QString display(const ascend::Config& value) {
    using Kind = ascend::Config::Kind;
    switch (value.kind()) {
        case Kind::null_value: return QStringLiteral("null");
        case Kind::boolean: return value.boolean() ? QStringLiteral("true") : QStringLiteral("false");
        case Kind::integer: return QString::number(static_cast<qlonglong>(value.integer()));
        case Kind::number: return QString::number(value.number(), 'g', 17);
        case Kind::string: return QString::fromStdString(value.string());
        case Kind::array: {
            QStringList parts;
            for (const auto& element : value.elements()) parts << display(element);
            return "[" + parts.join(", ") + "]";
        }
        case Kind::object: {
            QStringList parts;
            for (const auto& member : value.members()) {
                parts << QString::fromStdString(member.first) + ": " + display(member.second);
            }
            return "{" + parts.join(", ") + "}";
        }
    }
    return {};
}

ascend::Config withLeaf(const ascend::Config& base, const QStringList& path, int index,
                        const ascend::Config& value) {
    if (index == path.size()) return value;
    std::vector<std::pair<std::string, ascend::Config>> members;
    if (base.kind() == ascend::Config::Kind::object) members = base.members();
    const std::string name = path[index].toStdString();
    bool replaced = false;
    for (auto& member : members) {
        if (member.first != name) continue;
        member.second = withLeaf(member.second, path, index + 1, value);
        replaced = true;
    }
    if (!replaced) return base;
    return ascend::Config::object(std::move(members));
}

}  // namespace

ConfigEditor::ConfigEditor(const session::ValueAdapter& adapter, const UiTexts& texts, QWidget* parent)
    : QWidget(parent), adapter_(adapter), texts_(texts) {
    form_ = new QFormLayout(this);
    form_->setContentsMargins(0, 0, 0, 0);
    form_->setFieldGrowthPolicy(QFormLayout::AllNonFixedFieldsGrow);
}

void ConfigEditor::setConfig(const ascend::Config& config) {
    // 模型回显与编辑器当前值一致时保留控件，避免打断正在进行的输入。
    if (built_ && config_ == config) return;
    config_ = config;
    rebuild();
}

void ConfigEditor::rebuild() {
    while (form_->rowCount() > 0) {
        QFormLayout::TakeRowResult row = form_->takeRow(0);
        delete row.labelItem->widget();
        delete row.fieldItem->widget();
        delete row.labelItem;
        delete row.fieldItem;
    }
    leaves_.clear();
    const bool json_editable =
        config_.is_null() || (config_.kind() == ascend::Config::Kind::object && config_.members().empty());
    if (json_editable) {
        // 空配置没有字段可展开：直接编辑整份配置的 JSON 值（如 1 或 {"x": 0}）。
        auto leaf = std::make_unique<Leaf>();
        leaf->json = true;
        leaf->edit = new QLineEdit(config_.is_null() ? QString() : QStringLiteral("{}"), this);
        leaf->edit->setObjectName("configValueEdit");
        leaf->edit->setPlaceholderText(
            ui_text(texts_, "workbench.config.value_hint", "JSON value, e.g. 1 or {\"x\": 0}"));
        Leaf* stored = leaf.get();
        QObject::connect(leaf->edit, &QLineEdit::editingFinished, this, [this, stored] { commit(*stored); });
        form_->addRow(ui_text(texts_, "workbench.config.scalar_label", "Value"), leaf->edit);
        leaves_.push_back(std::move(leaf));
    } else {
        addValue({}, config_);
    }
    built_ = true;
}

void ConfigEditor::addValue(const QString& prefix, const ascend::Config& value) {
    if (value.kind() == ascend::Config::Kind::object && !value.members().empty()) {
        for (const auto& member : value.members()) {
            const QString name = QString::fromStdString(member.first);
            addValue(prefix.isEmpty() ? name : prefix + "/" + name, member.second);
        }
        return;
    }
    const QString label = prefix.isEmpty() ? ui_text(texts_, "workbench.config.scalar_label", "Value") : prefix;
    if (value.kind() == ascend::Config::Kind::integer && adapter_.editable()) {
        auto leaf = std::make_unique<Leaf>();
        leaf->path = prefix.split('/', Qt::SkipEmptyParts);
        leaf->edit = new QLineEdit(QString::number(static_cast<qlonglong>(value.integer())), this);
        leaf->edit->setObjectName("configLeaf_" + (prefix.isEmpty() ? QStringLiteral("value") : prefix));
        leaf->committed = leaf->edit->text();
        Leaf* stored = leaf.get();
        QObject::connect(leaf->edit, &QLineEdit::editingFinished, this, [this, stored] { commit(*stored); });
        form_->addRow(label, leaf->edit);
        leaves_.push_back(std::move(leaf));
        return;
    }
    form_->addRow(label, new QLabel(display(value), this));
}

void ConfigEditor::commit(Leaf& leaf) {
    if (leaf.json) {
        // 整份配置的 JSON 值：空文本表示恢复为空配置。
        const QString text = leaf.edit->text().trimmed();
        if (text.isEmpty()) {
            clearError(leaf.edit);
            if (!config_.is_null()) {
                config_ = ascend::Config{};
                emit configEdited(config_);
            }
            return;
        }
        try {
            ascend::Config parsed = ascend::Config::parse(text.toStdString());
            clearError(leaf.edit);
            if (parsed == config_) return;
            config_ = std::move(parsed);
            emit configEdited(config_);
        } catch (const ascend::EngineError& error) {
            markError(leaf.edit, QString::fromStdString(
                                     render_diagnostic(error.diagnostic(), &texts_.catalog, texts_.locale)));
        }
        return;
    }
    TextRef error;
    const auto parsed = adapter_.parse(leaf.edit->text().toStdString(), error);
    if (!parsed.has_value()) {
        markError(leaf.edit, QString::fromStdString(render_text(error, &texts_.catalog, texts_.locale)));
        return;
    }
    clearError(leaf.edit);
    if (leaf.edit->text() == leaf.committed) return;
    leaf.committed = leaf.edit->text();
    config_ = withLeaf(config_, leaf.path, 0, ascend::Config::integer(std::any_cast<std::int64_t>(*parsed)));
    emit configEdited(config_);
}

void ConfigEditor::markError(QLineEdit* edit, const QString& message) {
    edit->setStyleSheet(QStringLiteral("border: 1px solid #d62728;"));
    edit->setToolTip(message);
}

void ConfigEditor::clearError(QLineEdit* edit) {
    edit->setStyleSheet({});
    edit->setToolTip({});
}

}  // namespace ascend::workbench
