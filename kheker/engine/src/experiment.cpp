#include <ascend/experiment.hpp>

#include <algorithm>
#include <limits>
#include <set>

namespace ascend {
namespace {

[[noreturn]] void fail_experiment(ErrorCode code, const Reference& target, TextRef text) {
    throw EngineError({code, target, std::move(text)});
}

template <class Entries>
void validate_names(const Entries& entries, const char* key, const char* message) {
    std::set<std::string> names;
    for (const auto& entry : entries) {
        if (entry.first.empty() || !names.insert(entry.first).second) {
            fail_experiment(ErrorCode::invalid_declaration, {},
                            {{engine_text_domain, key}, message, {{"name", entry.first}}});
        }
    }
}

ExperimentSpec validate_spec(ExperimentSpec spec) {
    if (spec.advance.module.empty() || spec.advance.symbol.empty() ||
        spec.advance.module.find('/') != std::string::npos) {
        fail_experiment(ErrorCode::invalid_declaration, spec.advance,
                        {{engine_text_domain, "experiment.advance.invalid"},
                         "Advance entry must be a top-level public method", {}});
    }
    validate_names(spec.inputs, "experiment.input.names", "Input names must be non-empty and unique");
    validate_names(spec.observations, "experiment.observation.names",
                   "Observation names must be non-empty and unique");
    return spec;
}

Engine instantiate(const AssemblyDefinition& definition, const ModuleFactoryDirectory& factories) {
    Engine engine = definition.instantiate(factories);
    const auto diagnostics = engine.check();
    if (!diagnostics.empty()) throw EngineError(diagnostics.front());
    engine.seal();
    return engine;
}

std::vector<std::string> split_field(const std::string& field, const Intervention& request) {
    std::vector<std::string> segments;
    std::size_t begin = 0;
    while (true) {
        const auto end = field.find('/', begin);
        const auto segment = field.substr(begin, end == std::string::npos ? end : end - begin);
        if (segment.empty()) {
            fail_experiment(ErrorCode::invalid_intervention, {request.module, {}},
                            {{engine_text_domain, "experiment.intervention.field_path"},
                             "Invalid state field path: {field}", {{"field", field}}});
        }
        segments.push_back(segment);
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return segments;
}

Config descend(const Config& state, const std::vector<std::string>& segments, std::size_t index,
               const Config& value, const Intervention& request, const std::string& target) {
    if (index == segments.size()) return value;
    if (state.kind() != Config::Kind::object) {
        fail_experiment(ErrorCode::invalid_intervention, {request.module, {}},
                        {{engine_text_domain, "experiment.intervention.not_object"},
                         "Field path crosses a non-object value: {target}", {{"target", target}}});
    }
    const auto& members = state.members();
    const auto found = std::find_if(members.begin(), members.end(),
                                    [&](const auto& member) { return member.first == segments[index]; });
    if (found == members.end()) {
        fail_experiment(ErrorCode::invalid_intervention, {request.module, {}},
                        {{engine_text_domain, "experiment.intervention.field_missing"},
                         "State field not found: {target}", {{"target", target}}});
    }
    std::vector<std::pair<std::string, Config>> updated = members;
    const auto position = static_cast<std::size_t>(found - members.begin());
    updated[position].second = descend(found->second, segments, index + 1, value, request, target);
    return Config::object(std::move(updated));
}

}  // namespace

// 同一运行的作者回调可能重入宿主。覆盖全部操作而非仅推进，避免恢复或采样中
// 产生混合状态；退出（包括异常退出）后恢复可调用状态。
class ExperimentRun::Operation {
public:
    Operation(const ExperimentRun& run, const char* operation) : active_(run.operating_) {
        if (active_) {
            fail_experiment(ErrorCode::execution_failed, {run.label_, operation},
                            {{engine_text_domain, "experiment.operation.reentry"},
                             "Cannot {operation} while an experiment operation is in progress",
                             {{"operation", operation}}});
        }
        active_ = true;
    }
    ~Operation() { active_ = false; }
    Operation(const Operation&) = delete;
    Operation& operator=(const Operation&) = delete;

private:
    bool& active_;
};

Checkpoint apply_interventions(const Checkpoint& checkpoint, const std::vector<Intervention>& requests) {
    Checkpoint result = checkpoint;
    for (const auto& request : requests) {
        const auto module = std::find_if(result.truth.modules.begin(), result.truth.modules.end(),
                                         [&](const ModuleState& item) { return item.path == request.module; });
        if (request.module.empty() || module == result.truth.modules.end()) {
            fail_experiment(ErrorCode::invalid_intervention, {request.module, {}},
                            {{engine_text_domain, "experiment.intervention.target_missing"},
                             "Intervention target module not found: {module}", {{"module", request.module}}});
        }
        if (module->stateless) {
            fail_experiment(ErrorCode::invalid_intervention, {request.module, {}},
                            {{engine_text_domain, "experiment.intervention.stateless"},
                             "Module '{module}' is stateless and cannot be an intervention target",
                             {{"module", request.module}}});
        }
        if (request.field.empty()) {
            module->state = request.value;
            continue;
        }
        std::string target = request.module + "#" + request.field;
        module->state = descend(module->state, split_field(request.field, request), 0, request.value,
                                request, target);
    }
    return result;
}

ExperimentRun::ExperimentRun(const AssemblyDefinition& definition, const ModuleFactoryDirectory& factories,
                             ExperimentSpec spec, std::string label)
    : spec_(validate_spec(std::move(spec))),
      engine_(instantiate(definition, factories)),
      advance_(engine_.bind_method<void>(spec_.advance)),
      label_(std::move(label)) {}

void ExperimentRun::drive(const std::string& input, std::vector<std::any> arguments) {
    const Operation operation(*this, "drive");
    const auto found = std::find_if(spec_.inputs.begin(), spec_.inputs.end(),
                                    [&](const auto& item) { return item.first == input; });
    if (found == spec_.inputs.end()) {
        fail_experiment(ErrorCode::invalid_declaration, {},
                        {{engine_text_domain, "experiment.input.unknown"},
                         "No input named '{name}' in the experiment spec", {{"name", input}}});
    }
    engine_.call(found->second, arguments);
}

void ExperimentRun::step() {
    const Operation operation(*this, "step");
    if (boundary_ == std::numeric_limits<std::int64_t>::max()) {
        fail_experiment(ErrorCode::execution_failed, {label_, "step"},
                        {{engine_text_domain, "experiment.boundary.limit"},
                         "Experiment boundary has reached the maximum supported value", {}});
    }
    advance_();
    ++boundary_;
}

std::map<std::string, std::any> ExperimentRun::read_observations() const {
    std::map<std::string, std::any> result;
    for (const auto& item : spec_.observations) {
        result.emplace(item.first, engine_.read(item.second));
    }
    return result;
}

Observation ExperimentRun::observe() const {
    const Operation operation(*this, "observe");
    return {boundary_, read_observations()};
}

Sample ExperimentRun::sample() const {
    const Operation operation(*this, "sample");
    Sample result;
    result.boundary = boundary_;
    result.truth = engine_.capture_state();
    result.observations = read_observations();
    return result;
}

Checkpoint ExperimentRun::checkpoint() const {
    const Operation operation(*this, "checkpoint");
    return {boundary_, engine_.capture_state()};
}

void ExperimentRun::restore(const Checkpoint& checkpoint) {
    const Operation operation(*this, "restore");
    if (checkpoint.boundary < 0) {
        fail_experiment(ErrorCode::invalid_declaration, {},
                        {{engine_text_domain, "experiment.checkpoint.boundary"},
                         "Checkpoint boundary must not be negative", {}});
    }
    engine_.restore_state(checkpoint.truth);
    boundary_ = checkpoint.boundary;
}

}  // namespace ascend
