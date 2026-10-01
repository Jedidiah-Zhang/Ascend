#include "ui_text.hpp"

#include <ascend/engine.hpp>

#include <utility>

namespace ascend::workbench {

UiTexts load_ui_texts(const std::string& resource_path, std::string locale) {
    UiTexts texts;
    texts.locale = locale.empty() ? "zh-CN" : std::move(locale);
    try {
        texts.catalog.load({ui_text_domain, resource_path});
    } catch (const EngineError& error) {
        texts.load_errors.push_back(render_diagnostic(error.diagnostic()));
    }
    return texts;
}

QString ui_text(const UiTexts& texts, const char* key, const char* fallback) {
    const TextRef reference(TextKey{ui_text_domain, key}, fallback);
    return QString::fromStdString(render_text(reference, &texts.catalog, texts.locale));
}

}  // namespace ascend::workbench
