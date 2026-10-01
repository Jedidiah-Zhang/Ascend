#pragma once

#include <ascend/i18n.hpp>

#include <QString>

#include <string>
#include <vector>

namespace ascend::workbench {

// 界面文本域；语言资源位于 app/i18n/，缺失时按默认模板回退。
inline constexpr const char* ui_text_domain = "ascend.workbench";

// 界面文案上下文：已加载的语言目录与当前语言。构造后只读，可跨线程共享。
// 模板使用 Qt 风格 %1 占位符：先按语言渲染，再由 QString::arg 填充参数。
struct UiTexts {
    TextCatalog catalog;
    std::string locale = "zh-CN";
    std::vector<std::string> load_errors;  // 资源加载失败的展示文本
};

// 加载界面语言资源；失败时记录错误并继续使用默认模板。
UiTexts load_ui_texts(const std::string& resource_path, std::string locale);

// 渲染界面文案。
QString ui_text(const UiTexts& texts, const char* key, const char* fallback);

}  // namespace ascend::workbench
