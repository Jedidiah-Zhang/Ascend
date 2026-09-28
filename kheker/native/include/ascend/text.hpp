#pragma once

#include <cstddef>
#include <optional>
#include <string>
#include <tuple>
#include <utility>
#include <vector>

namespace ascend {

struct TextKey {
    std::string domain;
    std::string key;
    bool operator==(const TextKey& other) const noexcept {
        return domain == other.domain && key == other.key;
    }
    bool operator<(const TextKey& other) const noexcept {
        return std::tie(domain, key) < std::tie(other.domain, other.key);
    }
};

inline constexpr std::size_t text_max_depth = 128;

// 不可原地修改的消息树。字符串隐式构造原文，只有显式 TextKey 才参与本地化。
// 参数同样为文本：字符串是数据，TextRef 可以表达嵌套消息；不存在渲染结果回填入口。
class TextRef {
public:
    using Arguments = std::vector<std::pair<std::string, TextRef>>;
    TextRef() = default;
    TextRef(const char* literal) : TextRef(std::string(literal)) {}
    TextRef(std::string literal) : literal_(std::move(literal)) {}
    // 域、键及参数名必须非空，参数名不得重复或含花括号，树深度不超过上限。
    // 错误的代码声明抛出 std::invalid_argument；nullopt 与空默认模板不同。
    TextRef(TextKey key, std::optional<std::string> fallback = std::nullopt,
                     Arguments arguments = {});
    bool is_literal() const noexcept { return key_.domain.empty(); }
    const TextKey& key() const noexcept { return key_; }
    const std::string& literal() const noexcept { return literal_; }
    const std::optional<std::string>& fallback() const noexcept { return fallback_; }
    const Arguments& arguments() const noexcept { return arguments_; }
    std::size_t depth() const noexcept { return depth_; }

private:
    TextKey key_;
    std::string literal_;
    std::optional<std::string> fallback_;
    Arguments arguments_;
    std::size_t depth_ = 0;
};

class TextCatalog;
// 只扫描被选中的模板，参数渲染结果按原文插入；不持有全局语言状态。
std::string render_text(const TextRef& text, const TextCatalog* catalog = nullptr,
                        const std::string& locale = {});

// 模块定义登记资源，宿主决定加载哪些资源。路径由注册方解析，不依赖实例名。
struct I18nResource {
    std::string domain;
    std::string path;
};

}  // namespace ascend
