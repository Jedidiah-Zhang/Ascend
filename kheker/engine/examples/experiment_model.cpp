#include <ascend/example/experiment_model.hpp>

#include <limits>
#include <memory>
#include <stdexcept>
#include <utility>

namespace ascend::example {
namespace {

TextRef described(const char* key, const char* fallback) {
    return TextRef(TextKey{text_domain, key}, fallback);
}

// 有符号 64 位加法检查；溢出时抛出，不产生有符号溢出。
Integer checked_add(Integer left, Integer right, const char* expression) {
    if (right > 0 && left > std::numeric_limits<Integer>::max() - right) {
        throw std::overflow_error(std::string(expression) + " overflows int64");
    }
    if (right < 0 && left < std::numeric_limits<Integer>::min() - right) {
        throw std::overflow_error(std::string(expression) + " overflows int64");
    }
    return left + right;
}

Config encode(const Values& values) {
    return Config::object({{"x", Config::integer(values.x)},
                           {"y", Config::integer(values.y)},
                           {"z", Config::integer(values.z)}});
}

Integer require_integer(const Config& state, const char* field) {
    const Config* value = state.find(field);
    if (!value || value->kind() != Config::Kind::integer) {
        throw std::invalid_argument(std::string("state field '") + field + "' must be an integer");
    }
    return value->integer();
}

Values decode(const Config& state) {
    if (state.kind() != Config::Kind::object) {
        throw std::invalid_argument("plant config must be an object with x, y and z");
    }
    return {require_integer(state, "x"), require_integer(state, "y"), require_integer(state, "z")};
}

Module plant(const std::string& instance, const Config& config) {
    Values initial;
    if (!config.is_null()) initial = decode(config);
    auto values = std::make_shared<Values>(initial);

    Module model(instance);
    model.declare_stateless();
    model.require_value<Integer>("input", "example.scalar.v1",
                                 described("input", "External input a for the current step"));

    Module state("state");
    state.add_value<Integer>("x", [values] { return values->x; },
                             described("state.x", "State variable x; next value is x + a"),
                             "example.scalar.v1");
    state.add_value<Integer>("y", [values] { return values->y; },
                             described("state.y", "State variable y; next value is x"),
                             "example.scalar.v1");
    state.add_value<Integer>("z", [values] { return values->z; },
                             described("state.z", "State variable z; next value is y + z"),
                             "example.scalar.v1");
    state.add_method<void, Integer, Integer, Integer>(
        "write", {"x", "y", "z"},
        [values](Integer x, Integer y, Integer z) {
            values->x = x;
            values->y = y;
            values->z = z;
        },
        MethodOptions{described("state.write", "Write the three state variables as given"),
                      {}, {}, "example.write.v1"});
    state.add_state("example.plant.state.v1", [values] { return encode(*values); },
                    [values](const Config& state_value) { *values = decode(state_value); });

    Module update("update");
    update.declare_stateless();
    const auto input = update.require_value<Integer>("input", "example.scalar.v1",
                                                     described("update.input", "Input a used by this advance"));
    const auto old_x = update.require_value<Integer>("x", "example.scalar.v1",
                                                     described("update.x", "State x before the advance"));
    const auto old_y = update.require_value<Integer>("y", "example.scalar.v1",
                                                     described("update.y", "State y before the advance"));
    const auto old_z = update.require_value<Integer>("z", "example.scalar.v1",
                                                     described("update.z", "State z before the advance"));
    const auto write = update.require_method<void, Integer, Integer, Integer>(
        "write", "example.write.v1", described("update.write", "Write back the next state"));
    update.add_method<void>(
        "advance", {},
        [input, old_x, old_y, old_z, write](const Context& context) {
            const Integer x = old_x.read(context);
            const Integer y = old_y.read(context);
            const Integer z = old_z.read(context);
            write(context, checked_add(x, input.read(context), "x + a"),
                  x, checked_add(y, z, "y + z"));
        },
        MethodOptions{described("update.advance",
                                "Advance by x' = x + a, y' = x, z' = y + z, reading step-start values"),
                      {}, {}, "example.advance.v1"});

    model.add(std::move(state));
    model.add(std::move(update));
    model.forward("input", {"update", "input"});
    model.connect({"update", "x"}, {"state", "x"});
    model.connect({"update", "y"}, {"state", "y"});
    model.connect({"update", "z"}, {"state", "z"});
    model.connect({"update", "write"}, {"state", "write"});
    model.export_symbol("advance", {"update", "advance"});
    model.export_symbol("x", {"state", "x"});
    model.export_symbol("y", {"state", "y"});
    model.export_symbol("z", {"state", "z"});
    return model;
}

Module stimulus(const std::string& instance, const Config& config) {
    Integer initial = 0;
    if (!config.is_null()) {
        if (config.kind() != Config::Kind::integer) {
            throw std::invalid_argument("stimulus config must be an integer");
        }
        initial = config.integer();
    }
    Module module(instance);
    auto value = std::make_shared<Integer>(initial);
    module.add_value<Integer>("value", [value] { return *value; },
                              described("value", "Common input value driven by the host"),
                              "example.scalar.v1");
    module.add_method<void, Integer>("drive", {"next"}, [value](Integer next) { *value = next; },
                                     MethodOptions{described("drive", "Update the common input value"),
                                                   {}, {}, "example.scalar-drive.v1"});
    module.add_state("example.stimulus.state.v1", [value] { return Config::integer(*value); },
                     [value](const Config& state) {
                         if (state.kind() != Config::Kind::integer) {
                             throw std::invalid_argument("input state must be an integer");
                         }
                         *value = state.integer();
                     });
    return module;
}

}  // namespace

std::vector<I18nResource> i18n_resources(const std::string& resource_root) {
    if (resource_root.empty()) return {};
    return {{text_domain, resource_root + "/i18n/zh-CN.json"}};
}

ModuleFactoryDirectory factories(const std::string& resource_root) {
    ModuleFactoryDirectory result;
    const auto resources = i18n_resources(resource_root);
    result.add_definition(plant_definition, plant, resources);
    result.add_definition(stimulus_definition, stimulus, resources);
    return result;
}

AssemblyDefinition environment(const Values& initial, Integer input) {
    AssemblyDefinition result;
    result.add_instance(plant_definition, "plant", encode(initial));
    result.add_instance(stimulus_definition, "input", Config::integer(input));
    result.connect({"plant", "input"}, {"input", "value"});
    return result;
}

ExperimentSpec specification() {
    return ExperimentSpec{{"plant", "advance"},
                          {{"a", {"input", "drive"}}},
                          {{"x", {"plant", "x"}}, {"y", {"plant", "y"}}, {"z", {"plant", "z"}}}};
}

}  // namespace ascend::example
