#include <ascend/i18n.hpp>

#include <algorithm>
#include <map>
#include <set>
#include <stdexcept>

namespace ascend {

TextRef::TextRef(TextKey key, std::optional<std::string> fallback, Arguments arguments)
    : key_(std::move(key)), fallback_(std::move(fallback)), arguments_(std::move(arguments)), depth_(1) {
    if (key_.domain.empty() || key_.key.empty())
        throw std::invalid_argument("Text domain and key must be non-empty");
    std::set<std::string> names;
    for (const auto& argument : arguments_) {
        if (argument.first.empty() || argument.first.find_first_of("{}") != std::string::npos ||
            !names.insert(argument.first).second)
            throw std::invalid_argument("Text argument names must be non-empty, unique and contain no braces");
        depth_ = std::max(depth_, argument.second.depth() + 1);
    }
    if (depth_ > text_max_depth) throw std::invalid_argument("Text nesting limit exceeded");
}

std::string render_text(const TextRef& text, const TextCatalog* catalog, const std::string& locale) {
    if (text.is_literal()) return text.literal();
    const std::string* pattern = catalog ? catalog->find(locale, text.key()) : nullptr;
    if (!pattern && text.fallback()) pattern = &*text.fallback();
    if (!pattern) return text.key().domain + ':' + text.key().key;

    std::map<std::string, const TextRef*> arguments;
    for (const auto& argument : text.arguments()) arguments.emplace(argument.first, &argument.second);
    std::string result;
    std::size_t literal_start = 0;
    std::size_t opening = std::string::npos;
    for (std::size_t index = 0; index < pattern->size(); ++index) {
        if ((*pattern)[index] == '{') {
            opening = index;
        } else if ((*pattern)[index] == '}' && opening != std::string::npos) {
            const auto found = arguments.find(pattern->substr(opening + 1, index - opening - 1));
            if (found != arguments.end()) {
                result.append(*pattern, literal_start, opening - literal_start);
                result += render_text(*found->second, catalog, locale);
                literal_start = index + 1;
            }
            opening = std::string::npos;
        }
    }
    result.append(*pattern, literal_start, std::string::npos);
    return result;
}

}  // namespace ascend
