#include <ascend/session/adapter.hpp>

#include <algorithm>
#include <charconv>
#include <cmath>
#include <iomanip>
#include <locale>
#include <sstream>
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

std::string BoolAdapter::format(const std::any& value) const {
    return std::any_cast<bool>(value) ? "true" : "false";
}

bool BoolAdapter::equal(const std::any& left, const std::any& right) const {
    const auto* first = std::any_cast<bool>(&left);
    const auto* second = std::any_cast<bool>(&right);
    return first && second && *first == *second;
}

std::optional<std::any> BoolAdapter::parse(const std::string& text, TextRef& error) const {
    if (text == "true" || text == "1") return std::any(true);
    if (text == "false" || text == "0") return std::any(false);
    error = adapter_text("session.adapter.error.boolean", "Boolean text must be true, false, 1 or 0");
    return std::nullopt;
}

std::optional<ValueAdapter::Numeric> BoolAdapter::number(const std::any& value) const {
    return Numeric{std::any_cast<bool>(value) ? 1.0 : 0.0, true};
}

std::optional<std::int64_t> BoolAdapter::integer(const std::any& value) const {
    return std::any_cast<bool>(value) ? 1 : 0;
}

std::string DoubleAdapter::format(const std::any& value) const {
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << std::setprecision(17) << std::any_cast<double>(value);
    return stream.str();
}

bool DoubleAdapter::equal(const std::any& left, const std::any& right) const {
    const auto* first = std::any_cast<double>(&left);
    const auto* second = std::any_cast<double>(&right);
    return first && second && *first == *second;
}

std::optional<std::any> DoubleAdapter::parse(const std::string& text, TextRef& error) const {
    if (text.empty()) {
        error = adapter_text("session.adapter.error.empty_number",
                                 "Floating-point text must not be empty");
        return std::nullopt;
    }
    double value = 0.0;
    const auto result = std::from_chars(text.data(), text.data() + text.size(), value);
    if (result.ec != std::errc{} || result.ptr != text.data() + text.size() ||
        !std::isfinite(value)) {
        error = adapter_text("session.adapter.error.double",
                             "Floating-point text must be a finite decimal number");
        return std::nullopt;
    }
    return std::any(value);
}

std::optional<ValueAdapter::Numeric> DoubleAdapter::number(const std::any& value) const {
    return Numeric{std::any_cast<double>(value), false};
}

std::string StringAdapter::format(const std::any& value) const {
    return std::any_cast<std::string>(value);
}

bool StringAdapter::equal(const std::any& left, const std::any& right) const {
    const auto* first = std::any_cast<std::string>(&left);
    const auto* second = std::any_cast<std::string>(&right);
    return first && second && *first == *second;
}

std::optional<std::any> StringAdapter::parse(const std::string& text, TextRef& error) const {
    (void)error;
    return std::any(text);
}

AdapterRegistry::AdapterRegistry() {
    add(std::make_shared<BoolAdapter>());
    add(std::make_shared<Int64Adapter>());
    add(std::make_shared<DoubleAdapter>());
    add(std::make_shared<StringAdapter>());
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
