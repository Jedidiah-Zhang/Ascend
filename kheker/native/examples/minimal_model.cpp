#include <ascend/engine.hpp>

#include <cstdint>
#include <iostream>
#include <memory>

namespace {

using Integer = std::int64_t;

struct State {
    Integer a;
    Integer b;
};

Integer run(Integer initial_a) {
    auto state = std::make_shared<State>(State{initial_a, 0});
    ascend::Module model("model");
    model.add_value<Integer>("a", [state] { return state->a; }, "Initial input");
    model.add_value<Integer>("b", [state] { return state->b; }, "Accumulated output");

    ascend::MethodOptions advance;
    advance.description = "a_next = a; b_next = b + 2 * a";
    advance.reads = {{"model", "a"}, {"model", "b"}};
    advance.writes = {{"model", "b"}};
    model.add_method<void>("advance", {}, [state] { state->b += 2 * state->a; }, advance);

    ascend::Engine engine;
    engine.add(std::move(model));
    engine.seal();
    const auto step = engine.bind_method<void>({"model", "advance"});
    const auto output = engine.bind_value<Integer>({"model", "b"});
    step();
    step();
    return output.read();
}

}  // namespace

int main() {
    try {
        const auto baseline = run(2);
        const auto alternate = run(3);
        std::cout << "baseline=" << baseline << " alternate=" << alternate << '\n';
        return baseline == 8 && alternate == 12 ? 0 : 1;
    } catch (const ascend::EngineError& error) {
        const auto& diagnostic = error.diagnostic();
        std::cerr << diagnostic.target.module << '/' << diagnostic.target.symbol
                  << ": " << diagnostic.message << '\n';
        return 1;
    }
}
