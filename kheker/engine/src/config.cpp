#include <ascend/config.hpp>

#include <cmath>
#include <stdexcept>

namespace ascend {

Config Config::boolean(bool value) {
    Config config;
    config.kind_ = Kind::boolean;
    config.boolean_ = value;
    return config;
}

Config Config::integer(std::int64_t value) {
    Config config;
    config.kind_ = Kind::integer;
    config.integer_ = value;
    return config;
}

Config Config::number(double value) {
    if (!std::isfinite(value)) throw std::invalid_argument("Config number must be finite");
    Config config;
    config.kind_ = Kind::number;
    config.number_ = value;
    return config;
}

Config Config::string(std::string value) {
    Config config;
    config.kind_ = Kind::string;
    config.string_ = std::move(value);
    return config;
}

Config Config::array(std::vector<Config> values) {
    Config config;
    config.kind_ = Kind::array;
    config.elements_ = std::move(values);
    return config;
}

Config Config::object(std::vector<std::pair<std::string, Config>> members) {
    for (std::size_t index = 0; index < members.size(); ++index) {
        for (std::size_t other = 0; other < index; ++other) {
            if (members[index].first == members[other].first) {
                throw std::invalid_argument("Duplicate object member '" + members[index].first + "'");
            }
        }
    }
    Config config;
    config.kind_ = Kind::object;
    config.members_ = std::move(members);
    return config;
}

Config::Kind Config::kind() const noexcept { return kind_; }
bool Config::is_null() const noexcept { return kind_ == Kind::null_value; }

bool Config::boolean() const {
    if (kind_ != Kind::boolean) throw std::invalid_argument("Config value is not a boolean");
    return boolean_;
}

std::int64_t Config::integer() const {
    if (kind_ != Kind::integer) throw std::invalid_argument("Config value is not an integer");
    return integer_;
}

double Config::number() const {
    if (kind_ != Kind::number) throw std::invalid_argument("Config value is not a number");
    return number_;
}

const std::string& Config::string() const {
    if (kind_ != Kind::string) throw std::invalid_argument("Config value is not a string");
    return string_;
}

const std::vector<Config>& Config::elements() const {
    if (kind_ != Kind::array) throw std::invalid_argument("Config value is not an array");
    return elements_;
}

const std::vector<std::pair<std::string, Config>>& Config::members() const {
    if (kind_ != Kind::object) throw std::invalid_argument("Config value is not an object");
    return members_;
}

const Config* Config::find(const std::string& name) const {
    if (kind_ != Kind::object) return nullptr;
    for (const auto& member : members_) {
        if (member.first == name) return &member.second;
    }
    return nullptr;
}

bool Config::operator==(const Config& other) const {
    if (kind_ != other.kind_) return false;
    switch (kind_) {
        case Kind::null_value: return true;
        case Kind::boolean: return boolean_ == other.boolean_;
        case Kind::integer: return integer_ == other.integer_;
        case Kind::number: return number_ == other.number_;
        case Kind::string: return string_ == other.string_;
        case Kind::array: return elements_ == other.elements_;
        case Kind::object:
            if (members_.size() != other.members_.size()) return false;
            for (const auto& member : members_) {
                const Config* found = other.find(member.first);
                if (!found || !(*found == member.second)) return false;
            }
            return true;
    }
    return false;
}

}  // namespace ascend
