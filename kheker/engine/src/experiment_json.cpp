#include "experiment_json.hpp"

#include <ascend/engine.hpp>
#include <ascend/text.hpp>

#include <string>

namespace ascend::detail {
namespace {

[[noreturn]] void fail(ErrorCode code, const char* key, const char* fallback) {
    throw EngineError({code, {}, TextRef(TextKey{engine_text_domain, key}, std::string(fallback))});
}

const Config& require_member(const Config& object, const char* name, const char* error_key) {
    const auto* member = object.find(name);
    if (member == nullptr) {
        fail(ErrorCode::invalid_json, error_key, "Metadata is missing a required field");
    }
    return *member;
}

std::string require_string(const Config& object, const char* name, const char* error_key) {
    const auto& member = require_member(object, name, error_key);
    if (member.kind() != Config::Kind::string) {
        fail(ErrorCode::invalid_json, error_key, "Metadata field has the wrong type");
    }
    return member.string();
}

const Config& require_object(const Config& object, const char* name, const char* error_key) {
    const auto& member = require_member(object, name, error_key);
    if (member.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, error_key, "Metadata field has the wrong type");
    }
    return member;
}

const Config& require_array(const Config& object, const char* name, const char* error_key) {
    const auto& member = require_member(object, name, error_key);
    if (member.kind() != Config::Kind::array) {
        fail(ErrorCode::invalid_json, error_key, "Metadata field has the wrong type");
    }
    return member;
}

}  // namespace

Config reference_config(const Reference& reference) {
    return Config::object({{"module", Config::string(reference.module)},
                           {"symbol", Config::string(reference.symbol)}});
}

Config spec_config(const ExperimentSpec& spec) {
    std::vector<Config> inputs;
    for (const auto& [name, reference] : spec.inputs) {
        inputs.push_back(Config::object({{"name", Config::string(name)},
                                         {"reference", reference_config(reference)}}));
    }
    std::vector<Config> observations;
    for (const auto& [name, reference] : spec.observations) {
        observations.push_back(Config::object({{"name", Config::string(name)},
                                               {"reference", reference_config(reference)}}));
    }
    return Config::object({{"advance", reference_config(spec.advance)},
                           {"inputs", Config::array(std::move(inputs))},
                           {"observations", Config::array(std::move(observations))}});
}

Reference parse_reference(const Config& value, const char* error_key) {
    Reference reference;
    reference.module = require_string(value, "module", error_key);
    reference.symbol = require_string(value, "symbol", error_key);
    return reference;
}

ExperimentSpec parse_spec(const Config& value, const char* error_key) {
    ExperimentSpec spec;
    spec.advance = parse_reference(require_object(value, "advance", error_key), error_key);
    for (const auto& entry : require_array(value, "inputs", error_key).elements()) {
        spec.inputs.emplace_back(require_string(entry, "name", error_key),
                                 parse_reference(require_object(entry, "reference", error_key), error_key));
    }
    for (const auto& entry : require_array(value, "observations", error_key).elements()) {
        spec.observations.emplace_back(require_string(entry, "name", error_key),
                                       parse_reference(require_object(entry, "reference", error_key), error_key));
    }
    return spec;
}

}  // namespace ascend::detail
