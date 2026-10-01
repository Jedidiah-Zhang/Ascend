#pragma once

#include <ascend/text.hpp>

#include <cstdint>
#include <map>

namespace ascend {

inline constexpr const char* i18n_format = "ascend.i18n";
inline constexpr std::int64_t i18n_format_version = 1;
enum class TextConflict { reject, replace };

// 一条内存语言资源：文本域、语言文件文本与来源标识（诊断用，可为空）。
struct I18nText {
    std::string domain;
    std::string text;
    std::string source;
};

// 文本按语言、文本域与局部键组织；默认拒绝重复，覆盖必须显式指定。
class TextCatalog {
public:
    void define(std::string locale, TextKey key, std::string text,
                TextConflict conflict = TextConflict::reject);
    // 文件携带的 domain 必须与登记资源一致。整份资源检查成功后一次提交。
    void load(const I18nResource& resource, TextConflict conflict = TextConflict::reject);
    // 内存资源：结构规则与 load 相同；source 仅用于诊断来源（可为空）。
    void load_text(const I18nText& resource, TextConflict conflict = TextConflict::reject);
    void set_default_locale(std::string locale);
    const std::string& default_locale() const noexcept;
    bool contains(const std::string& locale, const TextKey& key) const;
    // 按名称排序返回已登记语言；成功加载空 entries 也会登记语言。
    // 语言存在不表示具有任何条目或完整翻译；set_default_locale 不登记语言。
    std::vector<std::string> locales() const;
    std::string resolve(const std::string& locale, const TextRef& text) const;

private:
    friend std::string render_text(const TextRef&, const TextCatalog*, const std::string&);
    // 精确语言 → 主标签 → 默认语言 → 默认主标签；不跨文本域回退。
    // 标识按原文匹配，以 '-' 分主标签，不进行大小写或 '_' 规范化。
    const std::string* find(const std::string& locale, const TextKey& key) const;
    std::map<std::string, std::map<TextKey, std::string>> entries_;
    std::string default_locale_;
};

}  // namespace ascend
