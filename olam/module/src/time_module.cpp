#include <olam/module/time_module.hpp>

#include <olam/time.hpp>

#include <algorithm>
#include <cstdint>
#include <initializer_list>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace olam::module {
namespace {

using Integer = std::int64_t;
using ascend::TextKey;
using ascend::TextRef;

TextRef described(const char* key, const char* fallback) {
    return TextRef(TextKey{time_text_domain, key}, fallback);
}

[[noreturn]] void fail(const std::string& instance, ascend::ErrorCode code, const char* key,
                       const char* fallback, TextRef::Arguments arguments = {}) {
    throw ascend::EngineError(
        {code, {instance, {}}, TextRef(TextKey{time_text_domain, key}, fallback,
                                       std::move(arguments))});
}

[[noreturn]] void fail_config(const std::string& instance, const char* key, const char* fallback,
                              TextRef::Arguments arguments = {}) {
    fail(instance, ascend::ErrorCode::invalid_config, key, fallback, std::move(arguments));
}

[[noreturn]] void fail_execution(const std::string& instance, const char* key, const char* fallback,
                                 TextRef::Arguments arguments = {}) {
    fail(instance, ascend::ErrorCode::execution_failed, key, fallback, std::move(arguments));
}

const char* time_error_key(TimeErrorCode code) {
    switch (code) {
        case TimeErrorCode::invalid_identifier: return "error.identifier";
        case TimeErrorCode::duplicate_identifier: return "error.duplicate";
        case TimeErrorCode::missing_parent: return "error.parent";
        case TimeErrorCode::duplicate_root: return "error.root";
        case TimeErrorCode::unknown_clock: return "error.unknown";
        case TimeErrorCode::invalid_rate: return "error.rate";
        case TimeErrorCode::arithmetic_overflow: return "error.overflow";
        case TimeErrorCode::step_mismatch: return "error.step";
        case TimeErrorCode::invalid_snapshot: return "error.snapshot";
    }
    return "error.snapshot";
}

const char* time_error_fallback(TimeErrorCode code) {
    switch (code) {
        case TimeErrorCode::invalid_identifier: return "Clock identifier must not be empty";
        case TimeErrorCode::duplicate_identifier: return "Clock identifier is already registered";
        case TimeErrorCode::missing_parent: return "Clock parent is not registered";
        case TimeErrorCode::duplicate_root: return "Only one root clock is allowed";
        case TimeErrorCode::unknown_clock: return "Clock is not registered";
        case TimeErrorCode::invalid_rate: return "Clock rate is invalid";
        case TimeErrorCode::arithmetic_overflow:
            return "Clock arithmetic exceeds the representable range";
        case TimeErrorCode::step_mismatch: return "Clock step does not match the time base";
        case TimeErrorCode::invalid_snapshot: return "Clock state is invalid";
    }
    return "Clock state is invalid";
}

[[noreturn]] void fail_time(const std::string& instance, const TimeError& error,
                            ascend::ErrorCode code) {
    const TextRef reason(TextKey{time_text_domain, time_error_key(error.code)},
                         time_error_fallback(error.code));
    fail(instance, code, "error.time", "Clock '{id}' is invalid: {reason}",
         {{"id", TextRef(error.clock)}, {"reason", reason}});
}

const ascend::Config* member(const ascend::Config& object, const char* name) {
    return object.find(name);
}

Integer integer_field(const ascend::Config& object, const char* name, Integer fallback,
                      const std::string& instance) {
    const ascend::Config* value = member(object, name);
    if (value == nullptr) return fallback;
    if (value->kind() != ascend::Config::Kind::integer) {
        fail_config(instance, "config.integer", "Clock config field '{field}' must be an integer",
                    {{"field", TextRef(name)}});
    }
    return value->integer();
}

std::string identifier_field(const ascend::Config& object, const std::string& fallback,
                             const std::string& instance) {
    const ascend::Config* value = member(object, "id");
    if (value == nullptr) return fallback;
    if (value->kind() != ascend::Config::Kind::string) {
        fail_config(instance, "config.identifier", "Clock identifier must be a string");
    }
    return value->string();
}

void reject_unknown(const ascend::Config& object, const std::string& instance,
                    std::initializer_list<const char*> allowed) {
    for (const auto& entry : object.members()) {
        const bool known = std::any_of(allowed.begin(), allowed.end(),
                                       [&](const char* name) { return entry.first == name; });
        if (!known) {
            fail_config(instance, "config.unknown", "Unknown clock config field '{field}'",
                        {{"field", TextRef(entry.first)}});
        }
    }
}

struct Settings {
    std::string id;
    Rate rate;
    bool paused = false;
};

Settings parse_settings(const ascend::Config& config, const std::string& instance) {
    if (config.kind() != ascend::Config::Kind::object) {
        fail_config(instance, "config.object", "Clock config must be an object");
    }
    reject_unknown(config, instance, {"id", "rate_numerator", "rate_denominator", "paused"});
    Settings settings;
    settings.id = identifier_field(config, instance, instance);
    if (settings.id.empty()) {
        fail_config(instance, "config.identifier_empty",
                    "Clock identifier must not be empty");
    }
    settings.rate.numerator = integer_field(config, "rate_numerator", 1, instance);
    settings.rate.denominator = integer_field(config, "rate_denominator", 1, instance);
    if (settings.rate.numerator < 0 || settings.rate.denominator <= 0) {
        fail_config(instance, "config.rate",
                    "Clock rate numerator must be non-negative and denominator must be positive");
    }
    const Integer paused = integer_field(config, "paused", 0, instance);
    if (paused != 0 && paused != 1) {
        fail_config(instance, "config.paused", "Clock paused field must be 0 or 1");
    }
    settings.paused = paused == 1;
    return settings;
}

const Clock& root_clock(const ClockTree& tree, const std::string& id, const std::string& instance) {
    const Clock* clock = tree.find(id);
    if (clock == nullptr) {
        fail_execution(instance, "error.unknown", "Clock is not registered");
    }
    return *clock;
}

ascend::Config capture_state(const ClockTree& tree, const std::string& id,
                             const std::string& instance) {
    const auto snapshot = tree.snapshot();
    const auto found = std::find_if(snapshot.clocks.begin(), snapshot.clocks.end(),
                                    [&](const ClockRecord& record) { return record.id == id; });
    if (found == snapshot.clocks.end()) {
        fail_execution(instance, "error.snapshot", "Clock state does not contain the root clock");
    }
    return ascend::Config::object(
        {{"id", ascend::Config::string(found->id)},
         {"local_time", ascend::Config::integer(found->local_time)},
         {"frame", ascend::Config::integer(found->frame)},
         {"rate_numerator", ascend::Config::integer(found->relative_rate.numerator)},
         {"rate_denominator", ascend::Config::integer(found->relative_rate.denominator)},
         {"paused", ascend::Config::integer(found->paused ? 1 : 0)}});
}

Integer require_integer(const ascend::Config& object, const char* name, const std::string& instance,
                        ascend::ErrorCode code) {
    const ascend::Config* value = member(object, name);
    if (value == nullptr) {
        fail(instance, code, "state.field", "Clock state is missing field '{field}'",
             {{"field", TextRef(name)}});
    }
    if (value->kind() != ascend::Config::Kind::integer) {
        fail(instance, code, "state.type", "Clock state field '{field}' has the wrong type",
             {{"field", TextRef(name)}});
    }
    return value->integer();
}

std::string require_identifier(const ascend::Config& object, const std::string& instance,
                               ascend::ErrorCode code) {
    const ascend::Config* value = member(object, "id");
    if (value == nullptr) {
        fail(instance, code, "state.field", "Clock state is missing field 'id'");
    }
    if (value->kind() != ascend::Config::Kind::string) {
        fail(instance, code, "state.type", "Clock state field 'id' has the wrong type");
    }
    return value->string();
}

std::optional<TimeError> restore_state(ClockTree& tree, const ascend::Config& state,
                                       const std::string& id, const std::string& instance) {
    if (state.kind() != ascend::Config::Kind::object) {
        fail(instance, ascend::ErrorCode::invalid_state, "state.shape",
             "Clock state must be an object");
    }
    for (const auto& entry : state.members()) {
        const bool known = entry.first == "id" || entry.first == "local_time" ||
                           entry.first == "frame" || entry.first == "rate_numerator" ||
                           entry.first == "rate_denominator" || entry.first == "paused";
        if (!known) {
            fail(instance, ascend::ErrorCode::invalid_state, "state.unknown",
                 "Unknown clock state field '{field}'", {{"field", TextRef(entry.first)}});
        }
    }
    const std::string state_id = require_identifier(state, instance, ascend::ErrorCode::invalid_state);
    const Integer local_time = require_integer(state, "local_time", instance,
                                               ascend::ErrorCode::invalid_state);
    const Integer frame = require_integer(state, "frame", instance, ascend::ErrorCode::invalid_state);
    const Integer numerator = require_integer(state, "rate_numerator", instance,
                                              ascend::ErrorCode::invalid_state);
    const Integer denominator = require_integer(state, "rate_denominator", instance,
                                                ascend::ErrorCode::invalid_state);
    const Integer paused = require_integer(state, "paused", instance, ascend::ErrorCode::invalid_state);
    if (state_id != id) {
        fail(instance, ascend::ErrorCode::invalid_state, "state.identifier",
             "Clock state identifier does not match the module instance");
    }
    if (paused != 0 && paused != 1) {
        fail(instance, ascend::ErrorCode::invalid_state, "state.paused",
             "Clock state paused field must be 0 or 1");
    }
    ClockTreeSnapshot snapshot;
    snapshot.step_microseconds = time_demo_step;
    snapshot.clocks.push_back(
        ClockRecord{state_id, {}, local_time, frame, Rate{numerator, denominator}, paused == 1});
    const auto error = tree.restore(snapshot);
    return error;
}

}  // namespace

ascend::Config time_preset() {
    return ascend::Config::object({{"rate_numerator", ascend::Config::integer(1)},
                                   {"rate_denominator", ascend::Config::integer(1)},
                                   {"paused", ascend::Config::integer(0)}});
}

ascend::Module make_time_module(const std::string& instance, const ascend::Config& config) {
    const ascend::Config effective = config.is_null() ? time_preset() : config;
    const Settings settings = parse_settings(effective, instance);

    auto tree = std::make_shared<ClockTree>(TimeBase{time_demo_step});
    if (const auto error = tree->add(settings.id, {}, settings.rate, settings.paused)) {
        fail_time(instance, *error, ascend::ErrorCode::invalid_config);
    }

    ascend::Module module(instance);
    const auto value = [&](std::string name, auto getter, const char* key, const char* fallback) {
        module.add_value<Integer>(std::move(name), std::move(getter), described(key, fallback),
                                  "olam.clock.scalar.v1");
    };
    value("local_microseconds",
          [tree, id = settings.id, instance] { return root_clock(*tree, id, instance).local_time(); },
          "value.local_microseconds", "Local time of the root clock in microseconds");
    value("frame",
          [tree, id = settings.id, instance] { return root_clock(*tree, id, instance).frame(); },
          "value.frame", "Completed local steps of the root clock");
    value("rate_numerator",
          [tree, id = settings.id, instance] { return root_clock(*tree, id, instance).relative_rate().numerator; },
          "value.rate_numerator", "Relative rate numerator of the root clock");
    value("rate_denominator",
          [tree, id = settings.id, instance] { return root_clock(*tree, id, instance).relative_rate().denominator; },
          "value.rate_denominator", "Relative rate denominator of the root clock");
    value("effective_numerator",
          [tree, id = settings.id, instance] { return root_clock(*tree, id, instance).effective_rate().numerator; },
          "value.effective_numerator", "Effective rate numerator relative to the root clock");
    value("effective_denominator",
          [tree, id = settings.id, instance] { return root_clock(*tree, id, instance).effective_rate().denominator; },
          "value.effective_denominator", "Effective rate denominator relative to the root clock");
    value("paused",
          [tree, id = settings.id, instance] {
              return root_clock(*tree, id, instance).paused() ? Integer{1} : Integer{0};
          },
          "value.paused", "Whether the root clock is paused as 1 or 0");

    module.add_method<void>(
        "advance", {},
        [tree, id = settings.id, instance] {
            const auto result = tree->advance(id);
            if (result == AdvanceResult::overflow) {
                fail_execution(instance, "error.overflow", "Clock step exceeds the time range");
            }
            if (result == AdvanceResult::missing) {
                fail_execution(instance, "error.unknown", "Clock is not registered");
            }
        },
        ascend::MethodOptions{described("method.advance",
                                        "Advance the root clock by one local step; paused clocks do not advance; rate affects scheduling only"),
                              {}, {}, "olam.clock.advance.v1"});
    module.add_method<void, Integer>(
        "set_paused", {"paused"},
        [tree, id = settings.id, instance](Integer paused) {
            if (paused != 0 && paused != 1) {
                fail_execution(instance, "error.paused_argument",
                               "Clock paused argument must be 0 or 1");
            }
            if (!tree->set_paused(id, paused == 1)) {
                fail_execution(instance, "error.unknown", "Clock is not registered");
            }
        },
        ascend::MethodOptions{described("method.set_paused",
                                        "Pause the root clock with 1 or resume it with 0"),
                              {}, {}, "olam.clock.set_paused.v1"});
    module.add_method<void, Integer, Integer>(
        "set_rate", {"numerator", "denominator"},
        [tree, id = settings.id, instance](Integer numerator, Integer denominator) {
            const auto error = tree->set_relative_rate(id, Rate{numerator, denominator});
            if (error) fail_time(instance, *error, ascend::ErrorCode::execution_failed);
        },
        ascend::MethodOptions{described("method.set_rate",
                                        "Set the root clock relative rate as a rational number"),
                              {}, {}, "olam.clock.set_rate.v1"});
    module.add_state(time_state_contract,
                     [tree, id = settings.id, instance] { return capture_state(*tree, id, instance); },
                     [tree, id = settings.id, instance](const ascend::Config& state) {
                         if (const auto error = restore_state(*tree, state, id, instance)) {
                             fail_time(instance, *error, ascend::ErrorCode::invalid_state);
                         }
                     });
    return module;
}

void register_time_module(ascend::ModuleLibrary& library) {
    library.register_implementation(time_definition, time_implementation, make_time_module,
                                    time_preset());
}

ascend::ModulePackage make_time_package(std::vector<ascend::ModuleResource> resources) {
    ascend::Engine engine;
    engine.add(make_time_module("probe", ascend::Config{}));
    ascend::ModulePackage package;
    package.manifest = ascend::export_module_manifest(engine, "", "probe");
    package.manifest.definition = time_definition;
    package.manifest.version = time_version;
    package.manifest.implementation = time_implementation;
    package.resources = std::move(resources);
    return package;
}

std::string time_package_bytes(std::vector<ascend::ModuleResource> resources) {
    return ascend::encode_module_package(make_time_package(std::move(resources)));
}

}  // namespace olam::module
