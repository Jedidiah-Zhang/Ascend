#include <ascend/session/adapter.hpp>

#include <algorithm>
#include <charconv>
#include <stdexcept>
#include <system_error>

namespace ascend::session {
namespace {

// 适配器消息属于会话文本域；默认模板为英文，中文由语言资源提供。
TextRef adapter_text(const char* key, const char* fallback) {
    return TextRef(TextKey{session_text_domain, key}, fallback);
}

}  // namespace

std::optional<std::any> ValueAdapter::parse(const std::string& text, TextRef& error) const {
    (void)text;
    error = adapter_text("session.adapter.error.unsupported", "This type has no registered editor parser");
    return std::nullopt;
}

std::optional<ValueAdapter::Numeric> ValueAdapter::number(const std::any& value) const {
    (void)value;
    return std::nullopt;
}

std::optional<std::int64_t> ValueAdapter::integer(const std::any& value) const {
    (void)value;
    return std::nullopt;
}

std::string Int64Adapter::format(const std::any& value) const {
    return std::to_string(std::any_cast<std::int64_t>(value));
}

bool Int64Adapter::equal(const std::any& left, const std::any& right) const {
    const auto* first = std::any_cast<std::int64_t>(&left);
    const auto* second = std::any_cast<std::int64_t>(&right);
    return first && second && *first == *second;
}

std::optional<std::any> Int64Adapter::parse(const std::string& text, TextRef& error) const {
    if (text.empty()) {
        error = adapter_text("session.adapter.error.empty", "Integer text must not be empty");
        return std::nullopt;
    }
    std::size_t begin = 0;
    if (text.front() == '+' || text.front() == '-') begin = 1;
    if (begin == text.size()) {
        error = adapter_text("session.adapter.error.digits", "Integer text has no digits");
        return std::nullopt;
    }
    for (std::size_t index = begin; index < text.size(); ++index) {
        if (text[index] < '0' || text[index] > '9') {
            error = adapter_text("session.adapter.error.characters",
                                 "Integer text may only contain an optional sign and decimal digits");
            return std::nullopt;
        }
    }
    std::int64_t value = 0;
    const auto* first = text.data() + (text.front() == '+' ? 1 : 0);
    const auto* last = text.data() + text.size();
    const auto result = std::from_chars(first, last, value, 10);
    if (result.ec == std::errc::result_out_of_range) {
        error = adapter_text("session.adapter.error.range", "Integer is outside the signed 64-bit range");
        return std::nullopt;
    }
    if (result.ec != std::errc{} || result.ptr != last) {
        error = adapter_text("session.adapter.error.invalid", "Integer text is invalid");
        return std::nullopt;
    }
    return std::any(value);
}

std::optional<std::int64_t> Int64Adapter::integer(const std::any& value) const {
    const auto* integer = std::any_cast<std::int64_t>(&value);
    return integer == nullptr ? std::optional<std::int64_t>{} : std::optional<std::int64_t>{*integer};
}

std::optional<ValueAdapter::Numeric> Int64Adapter::number(const std::any& value) const {
    const auto integer = std::any_cast<std::int64_t>(value);
    constexpr std::int64_t exact_limit = 9007199254740992;  // 2^53
    return Numeric{static_cast<double>(integer), integer >= -exact_limit && integer <= exact_limit};
}

AdapterRegistry::AdapterRegistry() {
    add(std::make_shared<Int64Adapter>());
}

void AdapterRegistry::add(std::shared_ptr<const ValueAdapter> adapter) {
    if (!adapter) throw std::invalid_argument("value adapter must not be null");
    if (adapter->name().empty()) throw std::invalid_argument("value adapter name must not be empty");
    if (adapters_.count(adapter->type()) != 0) {
        throw std::invalid_argument("value adapter for this type is already registered");
    }
    adapters_.emplace(adapter->type(), std::move(adapter));
}

const ValueAdapter* AdapterRegistry::find(std::type_index type) const {
    const auto found = adapters_.find(type);
    return found == adapters_.end() ? nullptr : found->second.get();
}

std::vector<const ValueAdapter*> AdapterRegistry::all() const {
    std::vector<const ValueAdapter*> result;
    result.reserve(adapters_.size());
    for (const auto& item : adapters_) result.push_back(item.second.get());
    std::sort(result.begin(), result.end(),
              [](const ValueAdapter* left, const ValueAdapter* right) { return left->name() < right->name(); });
    return result;
}

}  // namespace ascend::session
