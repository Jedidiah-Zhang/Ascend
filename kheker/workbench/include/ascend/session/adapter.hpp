#pragma once

#include <ascend/text.hpp>

#include <any>
#include <cstdint>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <typeindex>
#include <vector>

namespace ascend::session {

// 会话文本域；核心语言包位于工作台 i18n/，缺失时按默认模板回退。
inline constexpr const char* session_text_domain = "ascend.session";

// 类型适配器：把模块声明的原生类型映射为界面显示、编辑与绘图操作。
// 适配器按精确原生类型登记，不依赖 type_info 名称推断格式或序列化规则。
// 适配器在会话构造前登记完毕，之后只读使用，可跨线程共享。
class ValueAdapter {
public:
    virtual ~ValueAdapter() = default;

    // 稳定标识（如 "int64"）与人类可读名称，供目录、检查报告与界面展示。
    virtual std::string name() const = 0;
    virtual std::string display_name() const { return name(); }
    virtual std::type_index type() const = 0;
    // 精确显示文本；只用于展示，不参与解析或判等。
    virtual std::string format(const std::any& value) const = 0;
    // 原生值是否精确相等；用于记录重放的精确判等。
    virtual bool equal(const std::any& left, const std::any& right) const = 0;
    // 是否提供编辑解析入口。
    virtual bool editable() const { return false; }
    // 解析编辑文本；失败返回 nullopt，并把结构化错误消息写入 error，
    // 由展示层按语言资源渲染（默认模板为英文，缺失翻译时回退）。
    virtual std::optional<std::any> parse(const std::string& text, TextRef& error) const;
    // 曲线近似坐标；不支持返回 nullopt。坐标可能丢失精度，精确值以表格与详情为准。
    struct Numeric {
        double value = 0.0;
        bool exact = false;  // 坐标是否与原生值精确一致
    };
    virtual std::optional<Numeric> number(const std::any& value) const;
    // 精确 64 位整数视图；不支持或非整数返回 nullopt。只用于精确计算（如游标差值），
    // 显示文本仍由 format 提供且不参与解析或判等。
    virtual std::optional<std::int64_t> integer(const std::any& value) const;
};

// 内建 64 位有符号整数适配器：精确显示与判等，严格解析并检查范围。
class Int64Adapter final : public ValueAdapter {
public:
    std::string name() const override { return "int64"; }
    std::string display_name() const override { return "64 位整数"; }
    std::type_index type() const override { return typeid(std::int64_t); }
    std::string format(const std::any& value) const override;
    bool equal(const std::any& left, const std::any& right) const override;
    bool editable() const override { return true; }
    std::optional<std::any> parse(const std::string& text, TextRef& error) const override;
    std::optional<Numeric> number(const std::any& value) const override;
    std::optional<std::int64_t> integer(const std::any& value) const override;
};

// 适配器登记表：默认登记内建 int64；未登记类型在目录与检查中标注为不支持。
// 登记表在构造后只读，可被多个线程共享（会话不做线程内修改）。
class AdapterRegistry {
public:
    AdapterRegistry();
    // 登记适配器；类型或名称为空、重复登记抛出 std::invalid_argument。
    void add(std::shared_ptr<const ValueAdapter> adapter);

    const ValueAdapter* find(std::type_index type) const;
    // 按名称排序返回全部适配器。
    std::vector<const ValueAdapter*> all() const;

private:
    std::map<std::type_index, std::shared_ptr<const ValueAdapter>> adapters_;
};

}  // namespace ascend::session
