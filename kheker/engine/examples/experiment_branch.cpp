// 确定性三变量示例的命令行入口：模型、装配与规格来自可复用示例组件。
#include <ascend/example/experiment_model.hpp>

#include <any>
#include <array>
#include <cstdint>
#include <iostream>
#include <string>
#include <utility>
#include <vector>

namespace {
using namespace ascend;
using Integer = std::int64_t;
using Tuple = std::array<Integer, 3>;
using example::Values;

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

Tuple tuple(const Values& values) { return {values.x, values.y, values.z}; }

std::string show(const Tuple& values) {
    return "(" + std::to_string(values[0]) + "," + std::to_string(values[1]) + "," +
           std::to_string(values[2]) + ")";
}

void advance(ExperimentRun& run, Integer input) {
    run.drive("a", {std::any(input)});
    run.step();
}

bool check(const char* label, const Tuple& actual, const Tuple& expected) {
    if (actual == expected) return true;
    std::cerr << label << " expected " << show(expected) << ", received " << show(actual) << '\n';
    return false;
}
}  // namespace

int main() {
    try {
        ExperimentRecord record{example::environment(), example::specification(),
                                {{example::plant_definition, example::plant_implementation},
                                 {example::stimulus_definition, example::stimulus_implementation}},
                                {}, {}};
        const auto directory = example::factories();

        // 先在初始边界之后运行到边界 2，作为两个分支的共同起点。
        ExperimentRun source(record.assembly, directory, record.spec, "source");
        advance(source, 1);
        advance(source, 1);
        const auto origin = source.checkpoint();

        // 对照分支恢复原状态；干预分支恢复改动后的状态，两者互不影响。
        record.branches = {
            {"control", origin, {}, {}, {}, {}},
            {"treated",
             origin,
             {{example::reference_intervention_module, example::reference_intervention_field,
               Config::integer(example::reference_intervention_value)}},
             {},
             {},
             {}}};
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
        ok &= check("control origin", subject_observation(control.observe()),
                    tuple(example::reference_control[2]));
        ok &= check("treated origin", subject_observation(treated.observe()),
                    tuple(example::reference_treated[2]));
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
            ok &= check("control", observed(control_trace.samples.back()),
                        tuple(example::reference_control[boundary]));
            ok &= check("treated", observed(treated_trace.samples.back()),
                        tuple(example::reference_treated[boundary]));
            std::cout << "boundary=" << boundary << " control=" << show(observed(control_trace.samples.back()))
                       << " treated=" << show(observed(treated_trace.samples.back())) << '\n';
        }

        // 仅依据记录重建；宿主先核对实现身份，再用记录中的配置、接线、规格及输入重放。
        if (record.implementations.at(example::plant_definition) != example::plant_implementation ||
            record.implementations.at(example::stimulus_definition) != example::stimulus_implementation) return 1;
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
