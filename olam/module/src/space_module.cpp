#include <olam/module/space_module.hpp>

#include <olam/space.hpp>

#include <algorithm>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <memory>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace olam::module {
namespace {

using Integer = std::int64_t;
using ascend::TextKey;
using ascend::TextRef;

constexpr Integer default_origin = 0;
constexpr Integer default_width = 1000;
constexpr Integer default_height = 1000;
constexpr Integer default_layer = 0;

TextRef described(const char* key, const char* fallback) {
    return TextRef(TextKey{space_text_domain, key}, fallback);
}

[[noreturn]] void fail_config(const std::string& instance, const char* key, const char* fallback,
                              TextRef::Arguments arguments = {}) {
    throw ascend::EngineError(
        {ascend::ErrorCode::invalid_config, {instance, {}},
         TextRef(TextKey{space_text_domain, key}, fallback, std::move(arguments))});
}

const char* error_key(SpaceErrorCode code) {
    switch (code) {
        case SpaceErrorCode::invalid_identifier: return "error.invalid_identifier";
        case SpaceErrorCode::invalid_size: return "error.invalid_size";
        case SpaceErrorCode::negative_origin: return "error.negative_origin";
        case SpaceErrorCode::arithmetic_overflow: return "error.arithmetic_overflow";
        case SpaceErrorCode::duplicate_identifier: return "error.duplicate_identifier";
        case SpaceErrorCode::same_layer_overlap: return "error.same_layer_overlap";
    }
    return "error.unknown";
}

const char* error_fallback(SpaceErrorCode code) {
    switch (code) {
        case SpaceErrorCode::invalid_identifier: return "Space identifier must not be empty";
        case SpaceErrorCode::invalid_size: return "Space size must be positive";
        case SpaceErrorCode::negative_origin: return "Space origin must be non-negative";
        case SpaceErrorCode::arithmetic_overflow: return "Space range exceeds the tick range";
        case SpaceErrorCode::duplicate_identifier: return "Space identifier is already registered";
        case SpaceErrorCode::same_layer_overlap:
            return "Space overlaps another space on the same layer";
    }
    return "Space description is invalid";
}

// 空间集合创建失败时保留原错误类别语义，并把原因作为嵌套消息交给诊断链。
[[noreturn]] void fail_space(const std::string& instance, const SpaceError& error) {
    const TextRef reason(TextKey{space_text_domain, error_key(error.code)}, error_fallback(error.code));
    fail_config(instance, "error.space", "Space '{id}' is invalid: {reason}",
                {{"id", TextRef(error.space.value)}, {"reason", reason}});
}

const ascend::Config* member(const ascend::Config& object, const char* name) {
    return object.find(name);
}

std::int64_t integer_field(const ascend::Config& object, const char* name, std::int64_t fallback,
                           const std::string& instance) {
    const ascend::Config* value = member(object, name);
    if (value == nullptr) return fallback;
    if (value->kind() != ascend::Config::Kind::integer) {
        fail_config(instance, "config.integer", "Space config field '{field}' must be an integer",
                    {{"field", TextRef(name)}});
    }
    return value->integer();
}

std::string identifier_field(const ascend::Config& object, const std::string& fallback,
                             const std::string& instance) {
    const ascend::Config* value = member(object, "id");
    if (value == nullptr) return fallback;
    if (value->kind() != ascend::Config::Kind::string) {
        fail_config(instance, "config.identifier", "Space identifier must be a string");
    }
    return value->string();
}

void reject_unknown(const ascend::Config& object, const std::string& instance,
                    std::initializer_list<const char*> allowed) {
    for (const auto& entry : object.members()) {
        const bool known = std::any_of(allowed.begin(), allowed.end(),
                                       [&](const char* name) { return entry.first == name; });
        if (!known) {
            fail_config(instance, "config.unknown", "Unknown space config field '{field}'",
                        {{"field", TextRef(entry.first)}});
        }
    }
}

SpaceDescription describe_space(const ascend::Config& object, const std::string& fallback_id,
                                const std::string& instance) {
    if (object.kind() != ascend::Config::Kind::object) {
        fail_config(instance, "config.object", "Each space config must be an object");
    }
    reject_unknown(object, instance, {"id", "layer", "origin_x", "origin_y", "width", "height"});
    const Integer layer = integer_field(object, "layer", default_layer, instance);
    if (layer < std::numeric_limits<Layer>::min() || layer > std::numeric_limits<Layer>::max()) {
        fail_config(instance, "config.layer", "Space layer is outside the signed 32-bit range");
    }
    SpaceDescription description;
    description.id = SpaceId{identifier_field(object, fallback_id, instance)};
    description.layer = static_cast<Layer>(layer);
    description.origin = AbsolutePosition{integer_field(object, "origin_x", default_origin, instance),
                                          integer_field(object, "origin_y", default_origin, instance)};
    description.size = Size{integer_field(object, "width", default_width, instance),
                            integer_field(object, "height", default_height, instance)};
    return description;
}

std::shared_ptr<SpaceSet> build_spaces(const std::string& instance,
                                       const ascend::Config& config) {
    std::vector<SpaceDescription> descriptions;
    const ascend::Config* spaces = member(config, "spaces");
    if (spaces != nullptr) {
        if (spaces->kind() != ascend::Config::Kind::array) {
            fail_config(instance, "config.spaces", "Space config field 'spaces' must be an array");
        }
        for (const auto& entry : config.members()) {
            if (entry.first != "spaces") {
                fail_config(instance, "config.unknown", "Unknown space config field '{field}'",
                            {{"field", TextRef(entry.first)}});
            }
        }
        for (std::size_t index = 0; index < spaces->elements().size(); ++index) {
            const std::string fallback = instance + "#" + std::to_string(index);
            descriptions.push_back(describe_space(spaces->elements()[index], fallback, instance));
        }
    } else {
        descriptions.push_back(describe_space(config, instance, instance));
    }

    auto collection = std::make_shared<SpaceSet>();
    for (const auto& description : descriptions) {
        if (const auto error = collection->add(description)) fail_space(instance, *error);
    }
    return collection;
}

// 状态契约 olam.space.state.v1：spaces 数组保存每个空间的身份、层序、原点与尺寸。
ascend::Config encode_spaces(const SpaceSet& spaces) {
    std::vector<ascend::Config> entries;
    entries.reserve(spaces.size());
    for (const auto& space : spaces.spaces()) {
        const auto& origin = space.origin();
        const auto& size = space.size();
        entries.push_back(ascend::Config::object(
            {{"id", ascend::Config::string(space.id().value)},
             {"layer", ascend::Config::integer(space.layer())},
             {"origin_x", ascend::Config::integer(origin.x)},
             {"origin_y", ascend::Config::integer(origin.y)},
             {"width", ascend::Config::integer(size.width)},
             {"height", ascend::Config::integer(size.height)}}));
    }
    return ascend::Config::object({{"spaces", ascend::Config::array(std::move(entries))}});
}

void validate_state_space(const ascend::Config& entry, const std::string& instance) {
    if (entry.kind() != ascend::Config::Kind::object) {
        fail_config(instance, "state.shape", "Each space state entry must be an object");
    }
    const auto require = [&](const char* field, ascend::Config::Kind kind) {
        const ascend::Config* value = member(entry, field);
        if (value == nullptr) {
            fail_config(instance, "state.field", "Space state entry is missing field '{field}'",
                        {{"field", TextRef(field)}});
        }
        if (value->kind() != kind) {
            fail_config(instance, "state.type", "Space state field '{field}' has the wrong type",
                        {{"field", TextRef(field)}});
        }
    };
    require("id", ascend::Config::Kind::string);
    require("layer", ascend::Config::Kind::integer);
    require("origin_x", ascend::Config::Kind::integer);
    require("origin_y", ascend::Config::Kind::integer);
    require("width", ascend::Config::Kind::integer);
    require("height", ascend::Config::Kind::integer);
}

void restore_spaces(SpaceSet& target, const std::string& instance, const ascend::Config& state) {
    const ascend::Config* spaces = member(state, "spaces");
    if (state.kind() != ascend::Config::Kind::object || spaces == nullptr ||
        spaces->kind() != ascend::Config::Kind::array) {
        fail_config(instance, "state.shape",
                    "Space state must be an object containing a 'spaces' array");
    }
    for (const auto& entry : spaces->elements()) validate_state_space(entry, instance);
    auto rebuilt = build_spaces(instance, state);
    target = std::move(*rebuilt);
}

// 返回按层序与绝对位置命中的登记序号；未命中为 -1。
Integer locate_index(const SpaceSet& spaces, Integer layer, Integer x, Integer y) {
    for (std::size_t index = 0; index < spaces.spaces().size(); ++index) {
        const Space& space = spaces.spaces()[index];
        if (static_cast<Integer>(space.layer()) == layer && space.contains({x, y})) {
            return static_cast<Integer>(index);
        }
    }
    return -1;
}

Integer local_coordinate(const SpaceSet& spaces, Integer layer, Integer x, Integer y, bool axis_x) {
    const Integer index = locate_index(spaces, layer, x, y);
    if (index < 0) return -1;
    const Space& space = spaces.spaces()[static_cast<std::size_t>(index)];
    const auto local = space.to_local({x, y});
    return local.has_value() ? (axis_x ? local->x : local->y) : Integer{-1};
}

Integer primary_origin_x(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().origin().x);
}

Integer primary_origin_y(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().origin().y);
}

Integer primary_width(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().size().width);
}

Integer primary_height(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().size().height);
}

Integer primary_layer(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().layer());
}

Integer primary_upper_x(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().upper_bound().x);
}

Integer primary_upper_y(const SpaceSet& spaces) {
    if (spaces.empty()) return -1;
    return static_cast<Integer>(spaces.spaces().front().upper_bound().y);
}

}  // namespace

ascend::Config space_preset() {
    return ascend::Config::object({{"origin_x", ascend::Config::integer(default_origin)},
                                   {"origin_y", ascend::Config::integer(default_origin)},
                                   {"width", ascend::Config::integer(default_width)},
                                   {"height", ascend::Config::integer(default_height)},
                                   {"layer", ascend::Config::integer(default_layer)}});
}

ascend::Module make_space_module(const std::string& instance, const ascend::Config& config) {
    // 空配置是模块库探测实例的约定入口；与默认预填配置保持一致。
    const ascend::Config effective = config.is_null() ? space_preset() : config;
    if (effective.kind() != ascend::Config::Kind::object) {
        fail_config(instance, "config.kind",
                    "Space module config must be an object with space fields or a 'spaces' array");
    }
    const auto spaces = build_spaces(instance, effective);

    ascend::Module module(instance);

    const auto value = [&](std::string name, auto getter, const char* key, const char* fallback) {
        module.add_value<Integer>(std::move(name), std::move(getter), described(key, fallback),
                                  "olam.space.scalar.v1");
    };
    value("space_count",
          [spaces] { return static_cast<Integer>(spaces->size()); },
          "value.space_count", "Number of registered spaces");
    value("layer_count",
          [spaces] {
              std::set<Layer> layers;
              for (const auto& space : spaces->spaces()) layers.insert(space.layer());
              return static_cast<Integer>(layers.size());
          },
          "value.layer_count", "Number of distinct layers used by the registered spaces");
    value("origin_x", [spaces] { return primary_origin_x(*spaces); },
          "value.origin_x", "Absolute origin x of the first registered space; -1 when empty");
    value("origin_y", [spaces] { return primary_origin_y(*spaces); },
          "value.origin_y", "Absolute origin y of the first registered space; -1 when empty");
    value("width", [spaces] { return primary_width(*spaces); },
          "value.width", "Width of the first registered space in ticks; -1 when empty");
    value("height", [spaces] { return primary_height(*spaces); },
          "value.height", "Height of the first registered space in ticks; -1 when empty");
    value("layer", [spaces] { return primary_layer(*spaces); },
          "value.layer", "Layer of the first registered space; -1 when empty");
    value("upper_x",
          [spaces] { return primary_upper_x(*spaces); },
          "value.upper_x", "Exclusive upper bound x of the first registered space; -1 when empty");
    value("upper_y",
          [spaces] { return primary_upper_y(*spaces); },
          "value.upper_y", "Exclusive upper bound y of the first registered space; -1 when empty");

    module.add_method<Integer, Integer, Integer, Integer>(
        "locate", {"layer", "x", "y"},
        [spaces](Integer layer, Integer x, Integer y) { return locate_index(*spaces, layer, x, y); },
        ascend::MethodOptions{described("method.locate",
                                        "Locate a space by layer and absolute position; returns its registration index or -1"),
                              {}, {}, "olam.space.locate.v1"});
    module.add_method<Integer, Integer, Integer, Integer>(
        "contains", {"layer", "x", "y"},
        [spaces](Integer layer, Integer x, Integer y) {
            return locate_index(*spaces, layer, x, y) >= 0 ? Integer{1} : Integer{0};
        },
        ascend::MethodOptions{described("method.contains",
                                        "Report whether a layer contains an absolute position as 1 or 0"),
                              {}, {}, "olam.space.contains.v1"});
    module.add_method<Integer, Integer, Integer, Integer>(
        "local_x", {"layer", "x", "y"},
        [spaces](Integer layer, Integer x, Integer y) {
            return local_coordinate(*spaces, layer, x, y, true);
        },
        ascend::MethodOptions{described("method.local_x",
                                        "Local x of an absolute position inside its space; -1 when not located"),
                              {}, {}, "olam.space.local_x.v1"});
    module.add_method<Integer, Integer, Integer, Integer>(
        "local_y", {"layer", "x", "y"},
        [spaces](Integer layer, Integer x, Integer y) {
            return local_coordinate(*spaces, layer, x, y, false);
        },
        ascend::MethodOptions{described("method.local_y",
                                        "Local y of an absolute position inside its space; -1 when not located"),
                              {}, {}, "olam.space.local_y.v1"});
    module.add_state(space_state_contract, [spaces] { return encode_spaces(*spaces); },
                     [spaces, instance](const ascend::Config& state) {
                         restore_spaces(*spaces, instance, state);
                     });
    return module;
}

void register_space_module(ascend::ModuleLibrary& library) {
    library.register_implementation(space_definition, space_implementation, make_space_module,
                                    space_preset());
}

ascend::ModulePackage make_space_package(std::vector<ascend::ModuleResource> resources) {
    ascend::Engine engine;
    engine.add(make_space_module("probe", ascend::Config{}));
    ascend::ModulePackage package;
    package.manifest = ascend::export_module_manifest(engine, "", "probe");
    package.manifest.definition = space_definition;
    package.manifest.version = space_version;
    package.manifest.implementation = space_implementation;
    package.resources = std::move(resources);
    return package;
}

std::string space_package_bytes(std::vector<ascend::ModuleResource> resources) {
    return ascend::encode_module_package(make_space_package(std::move(resources)));
}

}  // namespace olam::module
