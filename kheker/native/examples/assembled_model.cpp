#include <ascend/engine.hpp>

#include <array>
#include <cstdint>
#include <iostream>
#include <memory>

namespace {
using Integer = std::int64_t;

ascend::MethodOptions contract(std::string name) {
    ascend::MethodOptions options;
    options.contract = std::move(name);
    return options;
}

// 工厂每次创建新的状态；模块名称由装配方提供。
ascend::Module accumulator(std::string name, Integer initial) {
    ascend::Module model(std::move(name));
    model.require_value<Integer>("input", "example.scalar.v1");

    auto value = std::make_shared<Integer>(initial);
    ascend::Module state("state");
    state.add_value<Integer>("value", [value] { return *value; },
                            "Accumulated output", "example.scalar.v1");
    state.add_method<void, Integer>("write", {"next"}, [value](Integer next) {
        *value = next;
    }, contract("example.write.v1"));

    ascend::Module update("update");
    const auto input = update.require_value<Integer>("input", "example.scalar.v1");
    const auto old = update.require_value<Integer>("old", "example.scalar.v1");
    const auto write = update.require_method<void, Integer>("write", "example.write.v1");
    update.add_method<void>("advance", {}, [input, old, write](const ascend::Context& context) {
        const auto next = old.read(context) + 2 * input.read(context);
        write(context, next);
    }, contract("example.advance.v1"));

    model.add(std::move(state));
    model.add(std::move(update));
    model.forward("input", {"update", "input"});
    model.connect({"update", "old"}, {"state", "value"});
    model.connect({"update", "write"}, {"state", "write"});
    model.export_symbol("advance", {"update", "advance"});
    model.export_symbol("output", {"state", "value"});
    return model;
}

ascend::Module system(std::string name, Integer initial) {
    ascend::Module result(std::move(name));
    result.require_value<Integer>("input", "example.scalar.v1");
    result.add(accumulator("model", initial));
    result.forward("input", {"model", "input"});
    result.export_symbol("advance", {"model", "advance"});
    result.export_symbol("output", {"model", "output"});
    return result;
}

ascend::Module stimulus(std::string name, Integer initial) {
    ascend::Module result(std::move(name));
    auto value = std::make_shared<Integer>(initial);
    result.add_value<Integer>("value", [value] { return *value; },
                             "Testbench-driven input", "example.scalar.v1");
    result.add_method<void, Integer>("drive", {"next"}, [value](Integer next) {
        *value = next;
    }, contract("example.scalar-drive.v1"));
    return result;
}
}  // namespace

int main() {
    try {
        ascend::Engine engine;
        engine.add(system("baseline", 0));
        engine.add(system("alternate", 0));
        engine.add(stimulus("input_baseline", 0));
        engine.add(stimulus("input_alternate", 0));
        engine.connect({"baseline", "input"}, {"input_baseline", "value"});
        engine.connect({"alternate", "input"}, {"input_alternate", "value"});
        for (const auto& diagnostic : engine.check()) {
            std::cerr << diagnostic.target.module << '/' << diagnostic.target.symbol
                      << ": " << diagnostic.message << '\n';
        }
        engine.seal();

        const auto baseline_step = engine.bind_method<void>({"baseline", "advance"});
        const auto alternate_step = engine.bind_method<void>({"alternate", "advance"});
        const auto drive_baseline = engine.bind_method<void, Integer>({"input_baseline", "drive"});
        const auto drive_alternate = engine.bind_method<void, Integer>({"input_alternate", "drive"});
        const auto baseline_output = engine.bind_value<Integer>({"baseline", "output"});
        const auto alternate_output = engine.bind_value<Integer>({"alternate", "output"});

        // testbench 每步先驱动输入，再显式推进 DUT，最后采样公开输出。
        struct Stimulus {
            Integer baseline;
            Integer alternate;
        };
        constexpr std::array<Stimulus, 3> stimuli = {{{2, 3}, {1, 2}, {1, 1}}};
        for (std::size_t index = 0; index < stimuli.size(); ++index) {
            drive_baseline(stimuli[index].baseline);
            drive_alternate(stimuli[index].alternate);
            baseline_step();
            alternate_step();
            std::cout << "pulse=" << (index + 1) << " baseline=" << baseline_output.read()
                      << " alternate=" << alternate_output.read() << '\n';
        }
        return baseline_output.read() == 8 && alternate_output.read() == 12 ? 0 : 1;
    } catch (const ascend::EngineError& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
