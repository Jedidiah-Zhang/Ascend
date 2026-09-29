#pragma once

#include <cstdint>
#include <string>
#include <utility>
#include <vector>

namespace ascend {

// 引擎可结构化的值：构造配置、运行状态与实验记录共用的数据表示。
// 整数保持 std::int64_t 精确值；访问器不做隐式转换，按 kind() 选用，
// 类型不符时抛出 std::invalid_argument。
class Config {
public:
    enum class Kind { null_value, boolean, integer, number, string, array, object };

    Config() = default;
    static Config boolean(bool value);
    static Config integer(std::int64_t value);
    // 数值必须有限；非有限值抛出 std::invalid_argument。
    static Config number(double value);
    static Config string(std::string value);
    static Config array(std::vector<Config> values);
    // 对象成员名重复时抛出 std::invalid_argument。
    static Config object(std::vector<std::pair<std::string, Config>> members);

    Kind kind() const noexcept;
    bool is_null() const noexcept;
    bool boolean() const;
    std::int64_t integer() const;
    double number() const;
    const std::string& string() const;
    const std::vector<Config>& elements() const;
    const std::vector<std::pair<std::string, Config>>& members() const;
    // 对象成员查找；非对象或成员不存在时返回空指针。
    const Config* find(const std::string& name) const;

    // 对象成员按名称比较，不要求顺序；数组按位置比较。
    bool operator==(const Config& other) const;
    bool operator!=(const Config& other) const { return !(*this == other); }

private:
    Kind kind_ = Kind::null_value;
    bool boolean_ = false;
    std::int64_t integer_ = 0;
    double number_ = 0.0;
    std::string string_;
    std::vector<Config> elements_;
    std::vector<std::pair<std::string, Config>> members_;
};

}  // namespace ascend
