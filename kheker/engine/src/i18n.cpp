#include <ascend/i18n.hpp>
#include <ascend/engine.hpp>

#include "engine_internal.hpp"
#include "json.hpp"

#include <fstream>

namespace ascend {
namespace {
[[noreturn]] void fail_i18n(ErrorCode code, const std::string& source, const std::string& path,
                           TextRef text) {
    throw EngineError({code, {}, std::move(text), source, path});
}

void check_identity(const std::string& locale, const TextKey& key) {
    if (locale.empty()) detail::fail_text(ErrorCode::invalid_i18n, {},
        {{engine_text_domain, "i18n.locale.empty"}, "Locale must not be empty"});
    if (key.domain.empty()) detail::fail_text(ErrorCode::invalid_i18n, {},
        {{engine_text_domain, "i18n.domain.empty"}, "Text domain must not be empty"});
    if (key.key.empty()) detail::fail_text(ErrorCode::invalid_i18n, {},
        {{engine_text_domain, "i18n.key.empty"}, "Text key must not be empty"});
}
}  // namespace

void TextCatalog::define(std::string locale, TextKey key, std::string text, TextConflict conflict) {
    check_identity(locale, key);
    if (conflict == TextConflict::reject && contains(locale, key))
        detail::fail_text(ErrorCode::invalid_i18n, {},
            {{engine_text_domain, "i18n.entry.duplicate"}, "Duplicate text '{domain}:{key}' in locale '{locale}'",
             {{"domain", key.domain}, {"key", key.key}, {"locale", locale}}});
    entries_[std::move(locale)].insert_or_assign(std::move(key), std::move(text));
}

void TextCatalog::load(const I18nResource& resource, TextConflict conflict) {
    if (resource.domain.empty() || resource.path.empty())
        detail::fail_text(ErrorCode::invalid_i18n, {},
            {{engine_text_domain, "i18n.resource.invalid"}, "Resource domain and path must be non-empty"});
    const auto& path = resource.path;
    std::ifstream stream(path, std::ios::binary);
    if (!stream.is_open()) fail_i18n(ErrorCode::io_failure, path, {},
        {{engine_text_domain, "i18n.io.open"}, "Cannot open text catalog for reading"});
    std::string text;
    char chunk[4096];
    while (stream) {
        stream.read(chunk, sizeof(chunk));
        text.append(chunk, static_cast<std::size_t>(stream.gcount()));
    }
    if (!stream.eof()) fail_i18n(ErrorCode::io_failure, path, {},
        {{engine_text_domain, "i18n.io.read"}, "Cannot read text catalog"});

    const Config document = detail::parse_json(text, path);
    if (document.kind() != Config::Kind::object) fail_i18n(ErrorCode::invalid_i18n, path, {},
        {{engine_text_domain, "i18n.record.object"}, "Text catalog must be a JSON object"});
    for (const auto& member : document.members()) {
        if (member.first != "format" && member.first != "version" && member.first != "domain" &&
            member.first != "locale" && member.first != "entries")
            fail_i18n(ErrorCode::invalid_i18n, path, detail::json_pointer_join("", member.first),
                {{engine_text_domain, "i18n.field.unknown"}, "Unknown field '{field}'", {{"field", member.first}}});
    }
    const auto field = [&](const std::string& name, Config::Kind kind) -> const Config& {
        const auto* value = document.find(name);
        if (!value || value->kind() != kind)
            fail_i18n(ErrorCode::invalid_i18n, path, '/' + name,
                {{engine_text_domain, "i18n.field.type"}, "Missing or incorrectly typed field '{field}'", {{"field", name}}});
        return *value;
    };
    const auto& format = field("format", Config::Kind::string).string();
    if (format != i18n_format) fail_i18n(ErrorCode::invalid_i18n, path, "/format",
        {{engine_text_domain, "i18n.field.format_unknown"}, "Unknown text catalog format '{format}'", {{"format", format}}});
    const auto version = field("version", Config::Kind::integer).integer();
    if (version != i18n_format_version) fail_i18n(ErrorCode::unsupported_format_version, path, "/version",
        {{engine_text_domain, "i18n.field.version_unsupported"},
         "Unsupported text catalog version {found}; this build reads version {expected}",
         {{"found", std::to_string(version)}, {"expected", std::to_string(i18n_format_version)}}});
    const auto& domain = field("domain", Config::Kind::string).string();
    if (domain != resource.domain) fail_i18n(ErrorCode::invalid_i18n, path, "/domain",
        {{engine_text_domain, "i18n.domain.mismatch"}, "Expected text domain '{expected}', received '{received}'",
         {{"expected", resource.domain}, {"received", domain}}});
    const auto& locale = field("locale", Config::Kind::string).string();
    if (locale.empty()) fail_i18n(ErrorCode::invalid_i18n, path, "/locale",
        {{engine_text_domain, "i18n.locale.empty"}, "Locale must not be empty"});
    const auto& entries = field("entries", Config::Kind::object).members();
    for (const auto& entry : entries) {
        const auto record = detail::json_pointer_join("/entries", entry.first);
        if (entry.first.empty()) fail_i18n(ErrorCode::invalid_i18n, path, record,
            {{engine_text_domain, "i18n.key.empty"}, "Text key must not be empty"});
        if (entry.second.kind() != Config::Kind::string) fail_i18n(ErrorCode::invalid_i18n, path, record,
            {{engine_text_domain, "i18n.entry.type"}, "Text entry '{key}' must be a string", {{"key", entry.first}}});
        if (conflict == TextConflict::reject && contains(locale, {domain, entry.first}))
            fail_i18n(ErrorCode::invalid_i18n, path, record,
                {{engine_text_domain, "i18n.entry.duplicate"}, "Duplicate text '{domain}:{key}' in locale '{locale}'",
                 {{"domain", domain}, {"key", entry.first}, {"locale", locale}}});
    }
    // 在副本上准备完整结果，分配或校验失败均不发布部分覆盖。
    auto updated = entries_;
    auto& target = updated[locale];
    for (const auto& entry : entries) target.insert_or_assign(TextKey{domain, entry.first}, entry.second.string());
    entries_.swap(updated);
}

void TextCatalog::set_default_locale(std::string locale) { default_locale_ = std::move(locale); }
const std::string& TextCatalog::default_locale() const noexcept { return default_locale_; }
bool TextCatalog::contains(const std::string& locale, const TextKey& key) const {
    const auto found = entries_.find(locale);
    return found != entries_.end() && found->second.count(key);
}
std::vector<std::string> TextCatalog::locales() const {
    std::vector<std::string> result;
    for (const auto& entry : entries_) result.push_back(entry.first);
    return result;
}
const std::string* TextCatalog::find(const std::string& locale, const TextKey& key) const {
    const auto lookup = [&](const std::string& candidate) -> const std::string* {
        const auto language = entries_.find(candidate);
        if (language == entries_.end()) return nullptr;
        const auto entry = language->second.find(key);
        return entry == language->second.end() ? nullptr : &entry->second;
    };
    const auto language = [&](const std::string& candidate) -> const std::string* {
        if (candidate.empty()) return nullptr;
        if (const auto* exact = lookup(candidate)) return exact;
        const auto separator = candidate.find('-');
        return separator == std::string::npos ? nullptr : lookup(candidate.substr(0, separator));
    };
    if (const auto* target = language(locale)) return target;
    return language(default_locale_);
}
std::string TextCatalog::resolve(const std::string& locale, const TextRef& text) const {
    return render_text(text, this, locale);
}

}  // namespace ascend
