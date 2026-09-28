#include "json.hpp"

#include <ascend/text.hpp>

#include <charconv>
#include <cmath>
#include <cstdio>
#include <iomanip>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <system_error>

namespace ascend::detail {
namespace {

bool is_digit(char character) {
    return character >= '0' && character <= '9';
}

// 解析与序列化共用 UTF-8 检查，返回码点的字节数；0 表示无效序列。
std::size_t utf8_size(const std::string& text, std::size_t start) {
    const auto lead = static_cast<unsigned char>(text[start]);
    std::size_t count;
    unsigned int code_point;
    if ((lead & 0xE0) == 0xC0) {
        count = 2;
        code_point = lead & 0x1F;
    } else if ((lead & 0xF0) == 0xE0) {
        count = 3;
        code_point = lead & 0x0F;
    } else if ((lead & 0xF8) == 0xF0) {
        count = 4;
        code_point = lead & 0x07;
    } else {
        return 0;
    }
    if (text.size() - start < count) return 0;
    for (std::size_t index = 1; index < count; ++index) {
        const auto next = static_cast<unsigned char>(text[start + index]);
        if ((next & 0xC0) != 0x80) return 0;
        code_point = (code_point << 6) | (next & 0x3F);
    }
    const unsigned int minimum = count == 2 ? 0x80 : count == 3 ? 0x800 : 0x10000;
    if (code_point < minimum || code_point > 0x10FFFF || (code_point >= 0xD800 && code_point <= 0xDFFF)) return 0;
    return count;
}

[[noreturn]] void fail_json(const std::string& source, const std::string& path, TextRef message);

void validate_string(const std::string& value, const std::string& source, const std::string& path) {
    for (std::size_t index = 0; index < value.size();) {
        if (static_cast<unsigned char>(value[index]) < 0x80) {
            ++index;
        } else {
            const auto count = utf8_size(value, index);
            if (count == 0) {
                fail_json(source, path,
                   {{engine_text_domain, "json.utf8.invalid_string"}, "Invalid UTF-8 sequence in string"});
            }
            index += count;
        }
    }
}

[[noreturn]] void fail_json(const std::string& source, const std::string& path, TextRef message) {
    throw EngineError({ErrorCode::invalid_json, {}, std::move(message), source, path});
}

class Parser {
public:
    Parser(const std::string& text, const std::string& source) : text_(text), source_(source) {}

    Config parse() {
        skip_space();
        Config value = parse_value("", 0);
        skip_space();
        if (position_ != text_.size()) error({{engine_text_domain, "json.document.trailing_content"}, "Unexpected content after the document"}, "");
        return value;
    }

private:
    [[noreturn]] void error(TextRef message, const std::string& path) const {
        // 解析器内部消息必须带默认模板，才能追加行列；遗漏属于代码声明错误。
        if (!message.fallback()) {
            throw std::logic_error("JSON parser diagnostic requires a default template");
        }
        auto arguments = message.arguments();
        arguments.push_back({"line", std::to_string(line_)});
        arguments.push_back({"column", std::to_string(column_)});
        fail_json(source_, path, {message.key(), *message.fallback() + " at line {line} column {column}",
                                  std::move(arguments)});
    }

    char peek() const { return position_ < text_.size() ? text_[position_] : '\0'; }

    char take() {
        if (position_ >= text_.size()) return '\0';
        const char character = text_[position_++];
        if (character == '\n') {
            ++line_;
            column_ = 1;
        } else {
            ++column_;
        }
        return character;
    }

    void skip_space() {
        while (peek() == ' ' || peek() == '\t' || peek() == '\n' || peek() == '\r') take();
    }

    void expect_literal(const char* literal, const std::string& path) {
        for (const char* cursor = literal; *cursor != '\0'; ++cursor) {
            if (take() != *cursor) error({{engine_text_domain, "json.literal.invalid"}, "Invalid literal"}, path);
        }
    }

    Config parse_value(const std::string& path, std::size_t depth) {
        if ((peek() == '{' || peek() == '[') && depth >= assembly_json_max_depth) {
            error({{engine_text_domain, "json.depth.limit_location"}, "JSON container nesting limit exceeded"}, path);
        }
        switch (peek()) {
            case '{': return parse_object(path, depth + 1);
            case '[': return parse_array(path, depth + 1);
            case '"': return Config::string(parse_string(path));
            case 't': expect_literal("true", path); return Config::boolean(true);
            case 'f': expect_literal("false", path); return Config::boolean(false);
            case 'n': expect_literal("null", path); return Config();
            default:
                if (peek() == '-' || is_digit(peek())) return parse_number(path);
                error({{engine_text_domain, "json.value.unexpected_character"}, "Unexpected character in value"}, path);
        }
    }

    Config parse_object(const std::string& path, std::size_t depth) {
        take();  // '{'
        std::vector<std::pair<std::string, Config>> members;
        skip_space();
        if (peek() == '}') {
            take();
            return Config::object(std::move(members));
        }
        while (true) {
            skip_space();
            if (peek() != '"') error({{engine_text_domain, "json.object.member_name_expected"}, "Expected an object member name"}, path);
            std::string key = parse_string(path);
            const std::string member_path = json_pointer_join(path, key);
            for (const auto& member : members) {
                if (member.first == key) error({{engine_text_domain, "json.object.duplicate_member"}, "Duplicate object member '{member}'",
                    {{"member", key}}}, member_path);
            }
            skip_space();
            if (take() != ':') error({{engine_text_domain, "json.object.colon_expected"}, "Expected ':' after the member name"}, member_path);
            skip_space();
            members.emplace_back(std::move(key), parse_value(member_path, depth));
            skip_space();
            const char separator = take();
            if (separator == ',') continue;
            if (separator == '}') break;
            error({{engine_text_domain, "json.object.separator_expected"}, "Expected ',' or '}' in object"}, path);
        }
        return Config::object(std::move(members));
    }

    Config parse_array(const std::string& path, std::size_t depth) {
        take();  // '['
        std::vector<Config> elements;
        skip_space();
        if (peek() == ']') {
            take();
            return Config::array(std::move(elements));
        }
        std::size_t index = 0;
        while (true) {
            skip_space();
            elements.push_back(parse_value(json_pointer_join(path, std::to_string(index)), depth));
            ++index;
            skip_space();
            const char separator = take();
            if (separator == ',') continue;
            if (separator == ']') break;
            error({{engine_text_domain, "json.array.separator_expected"}, "Expected ',' or ']' in array"}, path);
        }
        return Config::array(std::move(elements));
    }

    void append_codepoint(std::string& result, unsigned int code_point, const std::string& path) {
        if (code_point >= 0xD800 && code_point <= 0xDBFF) {
            if (take() != '\\' || take() != 'u') error({{engine_text_domain, "json.string.high_surrogate"}, "High surrogate without a low surrogate"}, path);
            const unsigned int low = parse_hex(path);
            if (low < 0xDC00 || low > 0xDFFF) error({{engine_text_domain, "json.string.high_surrogate"}, "High surrogate without a low surrogate"}, path);
            code_point = 0x10000 + ((code_point - 0xD800) << 10) + (low - 0xDC00);
        } else if (code_point >= 0xDC00 && code_point <= 0xDFFF) {
            error({{engine_text_domain, "json.string.low_surrogate"}, "Unexpected low surrogate"}, path);
        }
        if (code_point < 0x80) {
            result += static_cast<char>(code_point);
        } else if (code_point < 0x800) {
            result += static_cast<char>(0xC0 | (code_point >> 6));
            result += static_cast<char>(0x80 | (code_point & 0x3F));
        } else if (code_point < 0x10000) {
            result += static_cast<char>(0xE0 | (code_point >> 12));
            result += static_cast<char>(0x80 | ((code_point >> 6) & 0x3F));
            result += static_cast<char>(0x80 | (code_point & 0x3F));
        } else {
            result += static_cast<char>(0xF0 | (code_point >> 18));
            result += static_cast<char>(0x80 | ((code_point >> 12) & 0x3F));
            result += static_cast<char>(0x80 | ((code_point >> 6) & 0x3F));
            result += static_cast<char>(0x80 | (code_point & 0x3F));
        }
    }

    unsigned int parse_hex(const std::string& path) {
        unsigned int value = 0;
        for (int index = 0; index < 4; ++index) {
            const char character = take();
            unsigned int digit = 0;
            if (character >= '0' && character <= '9') digit = static_cast<unsigned int>(character - '0');
            else if (character >= 'a' && character <= 'f') digit = static_cast<unsigned int>(character - 'a' + 10);
            else if (character >= 'A' && character <= 'F') digit = static_cast<unsigned int>(character - 'A' + 10);
            else error({{engine_text_domain, "json.string.invalid_escape_hex"}, "Invalid hexadecimal escape"}, path);
            value = (value << 4) | digit;
        }
        return value;
    }

    void append_utf8(std::string& result, const std::string& path) {
        const std::size_t start = position_ - 1;  // 首字节已由 parse_string 消费。
        const auto count = utf8_size(text_, start);
        if (count == 0) error({{engine_text_domain, "json.utf8.invalid"}, "Invalid UTF-8 sequence"}, path);
        for (std::size_t index = 1; index < count; ++index) take();
        result.append(text_, start, count);
    }

    std::string parse_string(const std::string& path) {
        take();  // '"'
        std::string result;
        while (true) {
            if (position_ >= text_.size()) error({{engine_text_domain, "json.string.unterminated"}, "Unterminated string"}, path);
            const auto character = static_cast<unsigned char>(take());
            if (character == '"') break;
            if (character == '\\') {
                switch (take()) {
                    case '"': result += '"'; break;
                    case '\\': result += '\\'; break;
                    case '/': result += '/'; break;
                    case 'b': result += '\b'; break;
                    case 'f': result += '\f'; break;
                    case 'n': result += '\n'; break;
                    case 'r': result += '\r'; break;
                    case 't': result += '\t'; break;
                    case 'u': append_codepoint(result, parse_hex(path), path); break;
                    default: error({{engine_text_domain, "json.string.invalid_escape"}, "Invalid escape sequence"}, path);
                }
            } else if (character < 0x20) {
                error({{engine_text_domain, "json.string.control_character"}, "Unescaped control character in string"}, path);
            } else if (character < 0x80) {
                result += static_cast<char>(character);
            } else {
                append_utf8(result, path);
            }
        }
        return result;
    }

    Config parse_number(const std::string& path) {
        const std::size_t start = position_;
        if (peek() == '-') take();
        if (peek() == '0') {
            take();
        } else if (is_digit(peek())) {
            while (is_digit(peek())) take();
        } else {
            error({{engine_text_domain, "json.number.invalid"}, "Invalid number"}, path);
        }
        bool integral = true;
        if (peek() == '.') {
            integral = false;
            take();
            if (!is_digit(peek())) error({{engine_text_domain, "json.number.digit_after_point"}, "Expected a digit after the decimal point"}, path);
            while (is_digit(peek())) take();
        }
        if (peek() == 'e' || peek() == 'E') {
            integral = false;
            take();
            if (peek() == '+' || peek() == '-') take();
            if (!is_digit(peek())) error({{engine_text_domain, "json.number.digit_in_exponent"}, "Expected a digit in the exponent"}, path);
            while (is_digit(peek())) take();
        }
        const std::string literal = text_.substr(start, position_ - start);
        if (integral) {
            std::int64_t value = 0;
            const auto result = std::from_chars(literal.data(), literal.data() + literal.size(), value);
            if (result.ec != std::errc() || result.ptr != literal.data() + literal.size()) {
                error({{engine_text_domain, "json.number.integer_range"}, "Integer is out of range for a signed 64-bit value"}, path);
            }
            return Config::integer(value);
        }
        // 小数与指数只作数据搬运；首版验证范围是有符号整数。
        // 用经典区域设置的流解析，记录中的 '.' 不受 LC_NUMERIC 影响。
        std::istringstream stream(literal);
        stream.imbue(std::locale::classic());
        double value = 0.0;
        stream >> value;
        if (!stream || stream.peek() != std::char_traits<char>::eof() || !std::isfinite(value)) {
            error({{engine_text_domain, "json.number.range"}, "Number is out of range"}, path);
        }
        return Config::number(value);
    }

    const std::string& text_;
    const std::string& source_;
    std::size_t position_ = 0;
    std::size_t line_ = 1;
    std::size_t column_ = 1;
};

void write_indent(std::string& output, std::size_t depth) {
    output.append(depth * 2, ' ');
}

void write_string(std::string& output, const std::string& value) {
    output += '"';
    for (const auto character : value) {
        switch (character) {
            case '"': output += "\\\""; break;
            case '\\': output += "\\\\"; break;
            case '\b': output += "\\b"; break;
            case '\f': output += "\\f"; break;
            case '\n': output += "\\n"; break;
            case '\r': output += "\\r"; break;
            case '\t': output += "\\t"; break;
            default:
                if (static_cast<unsigned char>(character) < 0x20) {
                    char buffer[7];
                    std::snprintf(buffer, sizeof(buffer), "\\u%04x", static_cast<unsigned char>(character));
                    output += buffer;
                } else {
                    output += character;
                }
        }
    }
    output += '"';
}

void write_number(std::string& output, double value) {
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << std::setprecision(17) << value;
    std::string text = stream.str();
    if (text.find_first_of(".eE") == std::string::npos) text += ".0";
    output += text;
}

void write_value(std::string& output, const Config& value, std::size_t depth) {
    switch (value.kind()) {
        case Config::Kind::null_value: output += "null"; break;
        case Config::Kind::boolean: output += value.boolean() ? "true" : "false"; break;
        case Config::Kind::integer: output += std::to_string(value.integer()); break;
        case Config::Kind::number: write_number(output, value.number()); break;
        case Config::Kind::string: write_string(output, value.string()); break;
        case Config::Kind::array: {
            const auto& elements = value.elements();
            if (elements.empty()) {
                output += "[]";
                break;
            }
            output += "[\n";
            for (std::size_t index = 0; index < elements.size(); ++index) {
                write_indent(output, depth + 1);
                write_value(output, elements[index], depth + 1);
                output += index + 1 < elements.size() ? ",\n" : "\n";
            }
            write_indent(output, depth);
            output += ']';
            break;
        }
        case Config::Kind::object: {
            const auto& members = value.members();
            if (members.empty()) {
                output += "{}";
                break;
            }
            output += "{\n";
            for (std::size_t index = 0; index < members.size(); ++index) {
                write_indent(output, depth + 1);
                write_string(output, members[index].first);
                output += ": ";
                write_value(output, members[index].second, depth + 1);
                output += index + 1 < members.size() ? ",\n" : "\n";
            }
            write_indent(output, depth);
            output += '}';
            break;
        }
    }
}

}  // namespace

Config parse_json(const std::string& text, const std::string& source) {
    Parser parser(text, source);
    return parser.parse();
}

std::string json_pointer_join(const std::string& parent, const std::string& segment) {
    std::string result = parent + '/';
    for (char character : segment) {
        if (character == '~') result += "~0";
        else if (character == '/') result += "~1";
        else result += character;
    }
    return result;
}

void validate_json(const Config& value, const std::string& source, const std::string& path, std::size_t depth) {
    const auto kind = value.kind();
    if ((kind == Config::Kind::object || kind == Config::Kind::array) && depth >= assembly_json_max_depth) {
        fail_json(source, path,
                   {{engine_text_domain, "json.depth.limit"}, "JSON container nesting limit exceeded"});
    }
    if (kind == Config::Kind::string) {
        validate_string(value.string(), source, path);
    } else if (kind == Config::Kind::array) {
        const auto& elements = value.elements();
        for (std::size_t index = 0; index < elements.size(); ++index) {
            validate_json(elements[index], source, json_pointer_join(path, std::to_string(index)), depth + 1);
        }
    } else if (kind == Config::Kind::object) {
        for (const auto& member : value.members()) {
            validate_string(member.first, source, path);
            validate_json(member.second, source, json_pointer_join(path, member.first), depth + 1);
        }
    }
}

std::string write_json(const Config& value, const std::string& source) {
    validate_json(value, source);
    std::string output;
    write_value(output, value, 0);
    return output;
}

}  // namespace ascend::detail
