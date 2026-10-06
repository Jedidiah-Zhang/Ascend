#pragma once

// 界面展示辅助：UTF-8 转换、引用与诊断渲染。状态层面板与会话桥共用的纯函数，
// 不依赖 Qt Widgets。文本域与回退规则见 ui_text.hpp。

#include "ui_text.hpp"

#include <ascend/session/session.hpp>

#include <QString>

#include <string>

namespace ascend::workbench {

QString from_utf8(const std::string& text);
QString reference_text(const ascend::Reference& reference);
QString code_text(const UiTexts& texts, ascend::ErrorCode code);
// 诊断原因链的 HTML 片段（用于原因链视图）。
QString causes_html(const UiTexts& texts, const session::DiagnosticView& view, int depth);

}  // namespace ascend::workbench
