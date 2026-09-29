#include <ascend/experiment.hpp>

#include <any>
#include <array>
#include <cstdint>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using namespace ascend;
using Integer = std::int64_t;
using Tuple = std::array<Integer, 3>;
constexpr const char* kPlantImplementation = "example.plant.integer.v1";
constexpr const char* kStimulusImplementation = "example.stimulus.integer.v1";

MethodOptions contract(std::string name) {
    MethodOptions options;
    options.contract = std::move(name);
    return options;
}

// 确定性三变量模型：x' = x + a; y' = x; z' = y + z。
struct Values {
    Integer x = 0;
    Integer y = 0;
    Integer z = 0;
};

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
    if (state.kind() != Config::Kind::object) throw std::invalid_argument("state must be an object");
    return {require_integer(state, "x"), require_integer(state, "y"), require_integer(state, "z")};
}

Module plant(const std::string& instance, const Config& config) {
    Values initial;
    if (!config.is_null()) initial = decode(config);
    auto values = std::make_shared<Values>(initial);

    Module model(instance);
    model.declare_stateless();
    model.require_value<Integer>("input", "example.scalar.v1");

    Module state("state");
    state.add_value<Integer>("x", [values] { return values->x; }, "x", "example.scalar.v1");
    state.add_value<Integer>("y", [values] { return values->y; }, "y", "example.scalar.v1");
    state.add_value<Integer>("z", [values] { return values->z; }, "z", "example.scalar.v1");
    state.add_method<void, Integer, Integer, Integer>(
        "write", {"x", "y", "z"},
        [values](Integer x, Integer y, Integer z) {
            values->x = x;
            values->y = y;
            values->z = z;
        },
        contract("example.write.v1"));
    state.add_state("example.plant.state.v1", [values] { return encode(*values); },
                    [values](const Config& state_value) { *values = decode(state_value); });

    Module update("update");
    update.declare_stateless();
    const auto input = update.require_value<Integer>("input", "example.scalar.v1");
    const auto old_x = update.require_value<Integer>("x", "example.scalar.v1");
    const auto old_y = update.require_value<Integer>("y", "example.scalar.v1");
    const auto old_z = update.require_value<Integer>("z", "example.scalar.v1");
    const auto write = update.require_method<void, Integer, Integer, Integer>("write", "example.write.v1");
    update.add_method<void>("advance", {}, [input, old_x, old_y, old_z, write](const Context& context) {
        const Integer x = old_x.read(context);
        const Integer y = old_y.read(context);
        const Integer z = old_z.read(context);
        write(context, x + input.read(context), x, y + z);
    }, contract("example.advance.v1"));

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
    if (!config.is_null()) initial = config.integer();
    Module module(instance);
    auto value = std::make_shared<Integer>(initial);
    module.add_value<Integer>("value", [value] { return *value; }, "Input value", "example.scalar.v1");
    module.add_method<void, Integer>("drive", {"next"}, [value](Integer next) { *value = next; },
                                     contract("example.scalar-drive.v1"));
    module.add_state("example.stimulus.state.v1", [value] { return Config::integer(*value); },
                     [value](const Config& state) {
                         if (state.kind() != Config::Kind::integer) {
                             throw std::invalid_argument("input state must be an integer");
                         }
                         *value = state.integer();
                     });
    return module;
}

AssemblyDefinition environment() {
    AssemblyDefinition result;
    result.add_instance("example.plant", "plant", encode({0, 0, 0}));
    result.add_instance("example.stimulus", "input", Config::integer(0));
    result.connect({"plant", "input"}, {"input", "value"});
    return result;
}

ModuleFactoryDirectory factories() {
    ModuleFactoryDirectory result;
    result.add_definition("example.plant", plant);
    result.add_definition("example.stimulus", stimulus);
    return result;
}

ExperimentSpec specification() {
    return ExperimentSpec{{"plant", "advance"},
                          {{"a", {"input", "drive"}}},
                          {{"x", {"plant", "x"}}, {"y", {"plant", "y"}}, {"z", {"plant", "z"}}}};
}

Tuple observed(const Sample& sample) {
    return {std::any_cast<Integer>(sample.observations.at("x")),
            std::any_cast<Integer>(sample.observations.at("y")),
            std::any_cast<Integer>(sample.observations.at("z"))};
}

// 主体函数只有观测数据，无权取得运行、检查点或研究真值。
Tuple subject_observation(const Observation& observation) {
    return {std::any_cast<Integer>(observation.values.at("x")),
            std::any_cast<Integer>(observation.values.at("y")),
            std::any_cast<Integer>(observation.values.at("z"))};
}

std::string show(const Tuple& values) {
    return "(" + std::to_string(values[0]) + "," + std::to_string(values[1]) + "," +
           std::to_string(values[2]) + ")";
}

void advance(ExperimentRun& run, Integer input) {
    run.drive("a", {std::any(input)});
    run.step();
}

// 手工核对序列：输入 a = 1 恒定；干预在边界 2 施加 x := 10。
constexpr std::array<Tuple, 6> kReferenceControl = {Tuple{0, 0, 0}, Tuple{1, 0, 0}, Tuple{2, 1, 0},
                                                   Tuple{3, 2, 1}, Tuple{4, 3, 3}, Tuple{5, 4, 6}};
constexpr std::array<Tuple, 6> kReferenceTreated = {Tuple{0, 0, 0}, Tuple{1, 0, 0}, Tuple{10, 1, 0},
                                                   Tuple{11, 10, 1}, Tuple{12, 11, 11}, Tuple{13, 12, 22}};

bool check(const char* label, const Tuple& actual, const Tuple& expected) {
    if (actual == expected) return true;
    std::cerr << label << " expected " << show(expected) << ", received " << show(actual) << '\n';
    return false;
}
}  // namespace

int main() {
    try {
        ExperimentRecord record{environment(), specification(),
                                {{"example.plant", kPlantImplementation},
                                 {"example.stimulus", kStimulusImplementation}}, {}, {}};
        const auto directory = factories();

        // 先在初始边界之后运行到边界 2，作为两个分支的共同起点。
        ExperimentRun source(record.assembly, directory, record.spec, "source");
        advance(source, 1);
        advance(source, 1);
        const auto origin = source.checkpoint();

        // 对照分支恢复原状态；干预分支恢复改动后的状态，两者互不影响。
        record.branches = {{"control", origin, {}, {}, {}},
                           {"treated", origin, {{"plant/state", "x", Config::integer(10)}}, {}, {}}};
        auto& control_trace = record.branches[0];
        auto& treated_trace = record.branches[1];
        ExperimentRun control(record.assembly, directory, record.spec, control_trace.label);
        control.restore(control_trace.origin);
        ExperimentRun treated(record.assembly, directory, record.spec, treated_trace.label);
        treated.restore(apply_interventions(treated_trace.origin, treated_trace.interventions));

        // 预测起点采样：干预的直接结果在观测中可见，下游尚未传播。
        bool ok = true;
        control_trace.samples.push_back(control.sample());
        treated_trace.samples.push_back(treated.sample());
        ok &= check("control origin", subject_observation(control.observe()), kReferenceControl[2]);
        ok &= check("treated origin", subject_observation(treated.observe()), kReferenceTreated[2]);
        std::cout << "origin boundary=" << origin.boundary << " control="
                  << show(observed(control_trace.samples.front())) << " treated="
                  << show(observed(treated_trace.samples.front()))
                  << '\n';

        // 宿主记录实际驱动的共同输入；记录边界是驱动前的位置。
        for (Integer boundary = 2; boundary < 5; ++boundary) record.inputs.push_back({boundary, "a", {Integer{1}}});
        for (const auto& input : record.inputs) {
            if (control.boundary() != input.boundary || treated.boundary() != input.boundary) return 1;
            control.drive(input.name, input.arguments);
            treated.drive(input.name, input.arguments);
            control.step();
            treated.step();
            const auto boundary = control.boundary();
            control_trace.samples.push_back(control.sample());
            treated_trace.samples.push_back(treated.sample());
            ok &= check("control", observed(control_trace.samples.back()), kReferenceControl[boundary]);
            ok &= check("treated", observed(treated_trace.samples.back()), kReferenceTreated[boundary]);
            std::cout << "boundary=" << boundary << " control=" << show(observed(control_trace.samples.back()))
                       << " treated=" << show(observed(treated_trace.samples.back())) << '\n';
        }

        // 仅依据记录重建；宿主先核对实现身份，再用记录中的配置、接线、规格及输入重放。
        if (record.implementations.at("example.plant") != kPlantImplementation ||
            record.implementations.at("example.stimulus") != kStimulusImplementation) return 1;
        const auto loaded = AssemblyDefinition::parse(record.assembly.to_json());
        for (const auto& trace : record.branches) {
            ExperimentRun replay(loaded, directory, record.spec, trace.label);
            replay.restore(apply_interventions(trace.origin, trace.interventions));
            std::size_t index = 0;
            const auto verify = [&] {
                const auto actual = replay.sample();
                const auto& expected = trace.samples.at(index++);
                if (actual.boundary != expected.boundary || actual.truth.modules.size() != expected.truth.modules.size()) return false;
                for (std::size_t module = 0; module < actual.truth.modules.size(); ++module) {
                    const auto& a = actual.truth.modules[module];
                    const auto& b = expected.truth.modules[module];
                    if (a.path != b.path || a.contract != b.contract || a.stateless != b.stateless || a.state != b.state) return false;
                }
                return check("replay", observed(actual), observed(expected));
            };
            ok &= verify();
            for (const auto& input : record.inputs) {
                if (replay.boundary() != input.boundary) return 1;
                replay.drive(input.name, input.arguments);
                replay.step();
                ok &= verify();
            }
        }
        if (!ok) return 1;
        std::cout << "replay=ok\n";
        std::cout << "reference=ok\n";
        return 0;
    } catch (const EngineError& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
