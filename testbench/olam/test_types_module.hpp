#pragma once

#include <ascend/assembly.hpp>
#include <ascend/engine.hpp>

#include <cstdint>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>

namespace test_types {

inline constexpr const char* definition = "test.types";
inline constexpr const char* implementation = "test.types.v1";
inline constexpr const char* version = "1.0";
inline constexpr const char* state_contract = "test.types.state.v1";

struct Values {
    bool flag = true;
    double ratio = 0.5;
    std::string label = "hello";
    std::int64_t count = 0;
};

inline ascend::Module module(const std::string& instance, const ascend::Config& config) {
    Values initial;
    if (!config.is_null()) {
        if (config.kind() != ascend::Config::Kind::object) {
            throw std::invalid_argument("test.types config must be an object");
        }
        if (const auto* flag = config.find("flag")) initial.flag = flag->boolean();
        if (const auto* ratio = config.find("ratio")) initial.ratio = ratio->number();
        if (const auto* label = config.find("label")) initial.label = label->string();
        if (const auto* count = config.find("count")) initial.count = count->integer();
    }
    auto values = std::make_shared<Values>(initial);
    ascend::Module result(instance);
    result.add_value<bool>("flag", [values] { return values->flag; }, {}, "test.types.v1");
    result.add_value<double>("ratio", [values] { return values->ratio; }, {}, "test.types.v1");
    result.add_value<std::string>("label", [values] { return values->label; }, {}, "test.types.v1");
    result.add_value<std::int64_t>("count", [values] { return values->count; }, {}, "test.types.v1");
    result.add_method<void, bool>("set_flag", {"value"},
                                  [values](bool value) { values->flag = value; });
    result.add_method<void, double>("set_ratio", {"value"},
                                    [values](double value) { values->ratio = value; });
    result.add_method<void, std::string>("set_label", {"value"},
                                         [values](std::string value) {
                                             values->label = std::move(value);
                                         });
    result.add_method<void>("advance", {}, [values] { ++values->count; });
    result.add_method<double, double, double>("add", {"left", "right"},
                                               [](double left, double right) {
                                                   return left + right;
                                               });
    result.add_method<std::string, std::string>("echo", {"value"},
                                                 [](std::string value) { return value; });
    result.add_state(
        state_contract,
        [values] {
            return ascend::Config::object(
                {{"flag", ascend::Config::boolean(values->flag)},
                 {"ratio", ascend::Config::number(values->ratio)},
                 {"label", ascend::Config::string(values->label)},
                 {"count", ascend::Config::integer(values->count)}});
        },
        [values](const ascend::Config& state) {
            const auto* flag = state.find("flag");
            const auto* ratio = state.find("ratio");
            const auto* label = state.find("label");
            const auto* count = state.find("count");
            if (flag == nullptr || ratio == nullptr || label == nullptr || count == nullptr) {
                throw std::invalid_argument("test.types state is incomplete");
            }
            values->flag = flag->boolean();
            values->ratio = ratio->number();
            values->label = label->string();
            values->count = count->integer();
        });
    return result;
}

inline ascend::Config default_config() {
    return ascend::Config::object(
        {{"flag", ascend::Config::boolean(false)},
         {"ratio", ascend::Config::number(1.5)},
         {"label", ascend::Config::string("initial")},
         {"count", ascend::Config::integer(0)}});
}

}  // namespace test_types
