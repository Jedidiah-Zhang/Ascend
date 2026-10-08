#include "ui_format.hpp"

namespace ascend::workbench {

QString from_utf8(const std::string& text) { return QString::fromStdString(text); }

QString reference_text(const ascend::Reference& reference) {
    if (reference.module.empty()) return from_utf8(reference.symbol);
    if (reference.symbol.empty()) return from_utf8(reference.module);
    return from_utf8(reference.module) + "/" + from_utf8(reference.symbol);
}

QString code_text(const UiTexts& texts, ascend::ErrorCode code) {
    switch (code) {
        case ascend::ErrorCode::invalid_declaration:
            return ui_text(texts, "workbench.error.invalid_declaration", "Invalid declaration");
        case ascend::ErrorCode::missing_symbol: return ui_text(texts, "workbench.error.missing_symbol", "Missing symbol");
        case ascend::ErrorCode::wrong_kind: return ui_text(texts, "workbench.error.wrong_kind", "Kind mismatch");
        case ascend::ErrorCode::type_mismatch: return ui_text(texts, "workbench.error.type_mismatch", "Type mismatch");
        case ascend::ErrorCode::argument_count: return ui_text(texts, "workbench.error.argument_count", "Argument count");
        case ascend::ErrorCode::validation_failed:
            return ui_text(texts, "workbench.error.validation_failed", "Validation failed");
        case ascend::ErrorCode::execution_failed:
            return ui_text(texts, "workbench.error.execution_failed", "Execution failed");
        case ascend::ErrorCode::missing_module: return ui_text(texts, "workbench.error.missing_module", "Missing module");
        case ascend::ErrorCode::missing_requirement:
            return ui_text(texts, "workbench.error.missing_requirement", "Missing requirement");
        case ascend::ErrorCode::unconnected_requirement:
            return ui_text(texts, "workbench.error.unconnected_requirement", "Unconnected requirement");
        case ascend::ErrorCode::contract_mismatch:
            return ui_text(texts, "workbench.error.contract_mismatch", "Contract mismatch");
        case ascend::ErrorCode::invalid_config: return ui_text(texts, "workbench.error.invalid_config", "Invalid configuration");
        case ascend::ErrorCode::invalid_assembly: return ui_text(texts, "workbench.error.invalid_assembly", "Invalid assembly");
        case ascend::ErrorCode::invalid_json: return ui_text(texts, "workbench.error.invalid_json", "Record syntax");
        case ascend::ErrorCode::invalid_i18n: return ui_text(texts, "workbench.error.invalid_i18n", "Text resources");
        case ascend::ErrorCode::io_failure: return ui_text(texts, "workbench.error.io_failure", "File operation failed");
        case ascend::ErrorCode::state_incomplete: return ui_text(texts, "workbench.error.state_incomplete", "Incomplete state");
        case ascend::ErrorCode::state_mismatch: return ui_text(texts, "workbench.error.state_mismatch", "State mismatch");
        case ascend::ErrorCode::invalid_state: return ui_text(texts, "workbench.error.invalid_state", "Invalid state");
        case ascend::ErrorCode::invalid_intervention:
            return ui_text(texts, "workbench.error.invalid_intervention", "Invalid intervention");
        case ascend::ErrorCode::duplicate_module: return ui_text(texts, "workbench.error.duplicate_module", "Duplicate module");
        case ascend::ErrorCode::duplicate_symbol: return ui_text(texts, "workbench.error.duplicate_symbol", "Duplicate symbol");
        default: return ui_text(texts, "workbench.error.generic", "Diagnostic");
    }
}

QString causes_html(const UiTexts& texts, const session::DiagnosticView& view, int depth) {
    QString text;
    const QString indent = QString(depth * 16, ' ');
    text += indent + QStringLiteral("<b>%1</b>").arg(code_text(texts, view.code).toHtmlEscaped());
    if (!view.target.module.empty() || !view.target.symbol.empty()) {
        text += " · " + reference_text(view.target).toHtmlEscaped();
    }
    if (!view.path.empty()) text += " · " + from_utf8(view.path).toHtmlEscaped();
    text += "<br>" + indent + from_utf8(view.message).toHtmlEscaped() + "<br>";
    if (!view.source.empty()) {
        text += indent
                + ui_text(texts, "workbench.diag.source_html", "Source: %1<br>")
                      .arg(from_utf8(view.source).toHtmlEscaped());
    }
    for (const auto& cause : view.causes) {
        text += indent + ui_text(texts, "workbench.diag.cause_html", "Cause:<br>")
                + causes_html(texts, cause, depth + 1);
    }
    return text;
}

}  // namespace ascend::workbench
