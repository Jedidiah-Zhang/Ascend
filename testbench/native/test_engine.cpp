#include <ascend/engine.hpp>

#include <any>
#include <array>
#include <cstdint>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {

using namespace ascend;
using Integer = std::int64_t;

void require(bool condition, const char* expression, int line) {
    if (!condition) {
        throw std::runtime_error("line " + std::to_string(line) + ": " + expression);
    }
}

#define CHECK(expression) require((expression), #expression, __LINE__)

template <class Function>
Diagnostic expect_error(ErrorCode code, Reference target, Function&& function) {
    try {
        function();
    } catch (const EngineError& error) {
        CHECK(error.diagnostic().code == code);
        CHECK(error.diagnostic().target == target);
        CHECK(!error.diagnostic().message.empty());
        return error.diagnostic();
    }
    throw std::runtime_error("expected EngineError");
}

struct State {
    Integer a = 2;
    Integer b = 0;
};

Module model_module(const std::string& name, const std::shared_ptr<State>& state) {
    Module module(name);
    module.add_value<Integer>("a", [state] { return state->a; }, "External input");
    module.add_value<Integer>("b", [state] { return state->b; }, "Accumulated output");
    MethodOptions options;
    options.description = "b_next = b + 2 * a";
    options.reads = {{name, "a"}, {name, "b"}};
    options.writes = {{name, "b"}};
    module.add_method<void>("advance", {}, [state] {
        state->b += 2 * state->a;
    }, options);
    return module;
}

void non_spatial_model() {
    auto state = std::make_shared<State>();
    Engine baseline;
    baseline.add(model_module("model", state));
    CHECK(baseline.check().empty());
    baseline.seal();
    const auto advance = baseline.bind_method<void>({"model", "advance"});
    const auto output = baseline.bind_value<Integer>({"model", "b"});
    const auto first = output.read();
    advance();
    advance();
    CHECK(first == 0);
    CHECK(output.read() == 8);

    auto other = std::make_shared<State>();
    other->a = 3;
    Engine alternate;
    alternate.add(model_module("model", other));
    alternate.seal();
    alternate.call({"model", "advance"}, {});
    alternate.call({"model", "advance"}, {});
    CHECK(std::any_cast<Integer>(alternate.read({"model", "b"})) == 12);
    CHECK(output.read() == 8);
}

// 自定义数据结构由模块提供，核心没有空间或张量的内置假设。
struct Matrix {
    std::array<std::array<Integer, 2>, 2> entries;
};

void custom_values() {
    const Matrix matrix{{{{1, 2}, {3, 4}}}};
    Module module("matrix");
    module.add_value<Matrix>("data", [matrix] { return matrix; });
    MethodOptions options;
    options.description = "Select one element; indices must be in [0, 2).";
    module.add_method<Integer, Matrix, Integer, Integer>(
        "element", {"matrix", "row", "column"},
        [](const Matrix& value, Integer row, Integer column) {
            if (row < 0 || row >= 2 || column < 0 || column >= 2) {
                throw std::out_of_range("matrix index outside [0, 2)");
            }
            return value.entries[static_cast<std::size_t>(row)]
                                [static_cast<std::size_t>(column)];
        }, options);
    Engine engine;
    engine.add(std::move(module));
    engine.seal();
    const auto value = engine.bind_value<Matrix>({"matrix", "data"}).read();
    const auto element = engine.bind_method<Integer, Matrix, Integer, Integer>(
        {"matrix", "element"});
    CHECK(element(value, 1, 0) == 3);
    expect_error(ErrorCode::execution_failed, {"matrix", "element"}, [&] {
        element(value, -1, 0);
    });
}

void transport_values() {
    // std::any 也可以是模块自己的值类型，传输封装不能将其意外展开。
    Module module("m");
    module.add_value<std::any>("payload", [] { return std::any(Integer{7}); });
    module.add_method<std::any, std::any>("echo", {"payload"},
                                        [](const std::any& value) { return value; });
    Engine engine;
    engine.add(module);
    engine.seal();
    const auto payload = engine.bind_value<std::any>({"m", "payload"}).read();
    CHECK(std::any_cast<Integer>(payload) == 7);
    const auto echo = engine.bind_method<std::any, std::any>({"m", "echo"});
    CHECK(std::any_cast<Integer>(echo(payload)) == 7);
    const auto result = engine.call({"m", "echo"}, {std::make_any<std::any>(payload)});
    CHECK(std::any_cast<Integer>(std::any_cast<std::any>(result)) == 7);
}

void catalog_is_passive() {
    int calls = 0;
    Module module("z");
    module.add_value<Integer>("value", [&] { ++calls; return Integer{7}; }, "Length in mm");
    MethodOptions options;
    options.description = "A declaration, not a correctness proof";
    options.reads = {{"z", "value"}};
    module.add_method<Integer, Integer>("method", {"input"}, [&](Integer input) {
        ++calls;
        return input;
    }, options);
    Module first("a");
    first.add_value<bool>("flag", [] { return true; });
    Engine engine;
    engine.add(module);
    engine.add(first);
    auto catalog = engine.catalog();
    CHECK(calls == 0);
    CHECK(catalog.size() == 3);
    CHECK((catalog[0].reference == Reference{"a", "flag"}));
    CHECK((catalog[1].reference == Reference{"z", "method"}));
    CHECK(catalog[1].parameters[0].name == "input");
    CHECK(catalog[1].parameters[0].type == typeid(Integer));
    CHECK(catalog[1].result_type == typeid(Integer));
    CHECK(catalog[1].reads == options.reads);
    CHECK(catalog[2].description == "Length in mm");
    catalog[2].description = "changed outside engine";
    CHECK(engine.catalog()[2].description == "Length in mm");
    engine.seal();
    CHECK(calls == 0);
}

void scoped_references() {
    Module left("left");
    left.add_value<Integer>("value", [] { return Integer{2}; });
    Module right("right");
    right.add_value<Integer>("value", [] { return Integer{9}; });
    Engine engine;
    engine.add(left);
    engine.add(right);
    // 注册保存接口快照，之后修改构建对象不改变已注册目录。
    left.add_value<Integer>("later", [] { return Integer{0}; });
    engine.seal();
    CHECK(engine.bind_value<Integer>({"left", "value"}).read() == 2);
    CHECK(engine.bind_value<Integer>({"right", "value"}).read() == 9);
    expect_error(ErrorCode::missing_symbol, {"left", "later"}, [&] {
        engine.read({"left", "later"});
    });
}

void invalid_declarations() {
    expect_error(ErrorCode::invalid_declaration, {"", ""}, [] { Module module(""); });
    Module module("m");
    expect_error(ErrorCode::invalid_declaration, {"m", ""}, [&] {
        module.add_value<Integer>("", [] { return Integer{1}; });
    });
    expect_error(ErrorCode::invalid_declaration, {"m", "empty"}, [&] {
        module.add_value<Integer>("empty", std::function<Integer()>{});
    });
    expect_error(ErrorCode::invalid_declaration, {"m", "call"}, [&] {
        module.add_method<void>("call", {}, std::function<void()>{});
    });
    expect_error(ErrorCode::invalid_declaration, {"m", "arity"}, [&] {
        module.add_method<Integer, Integer>("arity", {}, [](Integer x) { return x; });
    });
    expect_error(ErrorCode::invalid_declaration, {"m", "names"}, [&] {
        module.add_method<void, Integer, Integer>("names", {"x", "x"},
                                                 [](Integer, Integer) {});
    });
    expect_error(ErrorCode::invalid_declaration, {"m", "unnamed"}, [&] {
        module.add_method<void, Integer>("unnamed", {""}, [](Integer) {});
    });
    MethodOptions invalid;
    invalid.reads = {{"", "value"}};
    expect_error(ErrorCode::invalid_declaration, {"m", "bad_ref"}, [&] {
        module.add_method<void>("bad_ref", {}, [] {}, invalid);
    });
}

void duplicate_registration() {
    Module module("m");
    module.add_value<Integer>("value", [] { return Integer{1}; });
    expect_error(ErrorCode::duplicate_symbol, {"m", "value"}, [&] {
        module.add_method<void>("value", {}, [] {});
    });
    Engine engine;
    engine.add(module);
    Module duplicate("m");
    duplicate.add_value<Integer>("other", [] { return Integer{9}; });
    expect_error(ErrorCode::duplicate_module, {"m", ""}, [&] { engine.add(duplicate); });
    engine.seal();
    CHECK(engine.catalog().size() == 1);
    CHECK(engine.bind_value<Integer>({"m", "value"}).read() == 1);
}

void configuration_validation() {
    bool valid = false;
    int validations = 0;
    Module module("config", [&] {
        ++validations;
        if (!valid) {
            throw std::invalid_argument("width must be positive");
        }
    });
    module.add_value<Integer>("width", [] { return Integer{1}; });
    Engine engine;
    const auto diagnostic = expect_error(ErrorCode::validation_failed, {"config", ""}, [&] {
        engine.add(module);
    });
    CHECK(diagnostic.message.find("width must be positive") != std::string::npos);
    CHECK(engine.catalog().empty());
    valid = true;
    engine.add(module);
    CHECK(validations == 2);
    engine.seal();
    CHECK(engine.bind_value<Integer>({"config", "width"}).read() == 1);
    CHECK(validations == 2);
}

void validation_reentry() {
    Engine engine;
    Module sealing("sealing", [&] { engine.seal(); });
    sealing.add_value<Integer>("value", [] { return Integer{1}; });
    expect_error(ErrorCode::registration_closed, {"sealing", ""}, [&] { engine.add(sealing); });
    CHECK(engine.catalog().empty());

    Engine other;
    Module duplicate("m", [&] { other.add(Module("m")); });
    expect_error(ErrorCode::duplicate_module, {"m", ""}, [&] { other.add(duplicate); });
    other.seal();
}

void unresolved_dependencies() {
    Module consumer("consumer");
    MethodOptions options;
    options.reads = {{"source", "input"}};
    options.writes = {{"source", "output"}};
    consumer.add_method<void>("run", {}, [] {}, options);
    Engine engine;
    engine.add(consumer);
    CHECK(engine.check().size() == 2);
    const auto diagnostic = expect_error(ErrorCode::missing_symbol, {"consumer", "run"}, [&] {
        engine.seal();
    });
    CHECK(diagnostic.message.find("source") != std::string::npos);
    Module source("source");
    source.add_value<Integer>("input", [] { return Integer{2}; });
    source.add_value<Integer>("output", [] { return Integer{4}; });
    engine.add(source);
    CHECK(engine.check().empty());
    engine.seal();
    engine.bind_method<void>({"consumer", "run"})();
}

void dependency_kind() {
    Module module("m");
    module.add_method<void>("target", {}, [] {});
    MethodOptions options;
    options.reads = {{"m", "target"}};
    options.writes = {{"m", "target"}};
    module.add_method<void>("run", {}, [] {}, options);
    Engine engine;
    engine.add(module);
    CHECK(engine.check().size() == 2);
    expect_error(ErrorCode::wrong_kind, {"m", "run"}, [&] { engine.seal(); });
}

void lifecycle() {
    Engine engine;
    Module module("m");
    module.add_value<Integer>("value", [] { return Integer{1}; });
    module.add_method<void>("call", {}, [] {});
    engine.add(module);
    expect_error(ErrorCode::registration_open, {"m", "value"}, [&] {
        engine.bind_value<Integer>({"m", "value"});
    });
    expect_error(ErrorCode::registration_open, {"m", "call"}, [&] {
        engine.call({"m", "call"}, {});
    });
    engine.seal();
    engine.seal();
    int validations = 0;
    expect_error(ErrorCode::registration_closed, {"other", ""}, [&] {
        engine.add(Module("other", [&] { ++validations; }));
    });
    CHECK(validations == 0);
}

void binding_checks() {
    Engine engine;
    Module module("m");
    module.add_value<Integer>("value", [] { return Integer{2}; });
    module.add_method<Integer, Integer>("twice", {"x"}, [](Integer x) { return x * 2; });
    engine.add(module);
    engine.seal();
    expect_error(ErrorCode::missing_symbol, {"missing", "value"}, [&] {
        engine.read({"missing", "value"});
    });
    expect_error(ErrorCode::wrong_kind, {"m", "twice"}, [&] { engine.read({"m", "twice"}); });
    expect_error(ErrorCode::wrong_kind, {"m", "value"}, [&] { engine.call({"m", "value"}, {}); });
    expect_error(ErrorCode::type_mismatch, {"m", "value"}, [&] {
        engine.bind_value<double>({"m", "value"});
    });
    expect_error(ErrorCode::type_mismatch, {"m", "twice"}, [&] {
        engine.bind_method<double, Integer>({"m", "twice"});
    });
    expect_error(ErrorCode::type_mismatch, {"m", "twice"}, [&] {
        engine.bind_method<Integer, double>({"m", "twice"});
    });
    expect_error(ErrorCode::argument_count, {"m", "twice"}, [&] {
        engine.bind_method<Integer>({"m", "twice"});
    });
}

void invocation_checks() {
    int calls = 0;
    Module module("m");
    module.add_method<Integer, Integer>("twice", {"x"}, [&](Integer x) {
        ++calls;
        return x * 2;
    });
    Engine engine;
    engine.add(module);
    engine.seal();
    expect_error(ErrorCode::argument_count, {"m", "twice"}, [&] { engine.call({"m", "twice"}, {}); });
    expect_error(ErrorCode::argument_count, {"m", "twice"}, [&] {
        engine.call({"m", "twice"}, {Integer{1}, Integer{2}});
    });
    expect_error(ErrorCode::type_mismatch, {"m", "twice"}, [&] {
        engine.call({"m", "twice"}, {2.0});
    });
    expect_error(ErrorCode::type_mismatch, {"m", "twice"}, [&] {
        engine.call({"m", "twice"}, {std::any{}});
    });
    CHECK(calls == 0);
    CHECK(std::any_cast<Integer>(engine.call({"m", "twice"}, {Integer{3}})) == 6);
    CHECK(calls == 1);
}

void callback_failures() {
    int state = 0;
    Module module("m");
    module.add_value<Integer>("unavailable", []() -> Integer { throw std::runtime_error("offline"); });
    module.add_method<void>("fail", {}, [&] { ++state; throw std::runtime_error("after write"); });
    module.add_method<void>("unknown", {}, [] { throw 7; });
    Engine engine;
    engine.add(module);
    engine.seal();
    const auto diagnostic = expect_error(ErrorCode::execution_failed, {"m", "unavailable"}, [&] {
        engine.read({"m", "unavailable"});
    });
    CHECK(diagnostic.message.find("offline") != std::string::npos);
    expect_error(ErrorCode::execution_failed, {"m", "fail"}, [&] { engine.call({"m", "fail"}, {}); });
    CHECK(state == 1);
    expect_error(ErrorCode::execution_failed, {"m", "unknown"}, [&] { engine.call({"m", "unknown"}, {}); });
}

void binding_lifetime() {
    std::weak_ptr<State> lifetime;
    {
        const auto bindings = [&] {
            Engine engine;
            auto state = std::make_shared<State>();
            lifetime = state;
            engine.add(model_module("m", state));
            engine.seal();
            return std::make_pair(engine.bind_value<Integer>({"m", "b"}),
                                  engine.bind_method<void>({"m", "advance"}));
        }();
        CHECK(!lifetime.expired());
        bindings.second();
        CHECK(bindings.first.read() == 4);
    }
    CHECK(lifetime.expired());
}

struct ThrowingCopy {
    bool non_standard;

    explicit ThrowingCopy(bool unknown) : non_standard(unknown) {}
    ThrowingCopy(const ThrowingCopy& other) : non_standard(other.non_standard) {
        if (non_standard) {
            throw 7;
        }
        throw std::runtime_error("copy failed");
    }
};

// 首次移动进入传输容器成功，取出结果时的移动失败。
struct ThrowingExtraction {
    bool non_standard;
    int moves = 0;

    explicit ThrowingExtraction(bool unknown) : non_standard(unknown) {}
    ThrowingExtraction(const ThrowingExtraction&) = default;
    ThrowingExtraction(ThrowingExtraction&& other)
        : non_standard(other.non_standard), moves(other.moves + 1) {
        if (moves == 2) {
            if (non_standard) {
                throw 7;
            }
            throw std::runtime_error("second move failed");
        }
    }
};

void binding_argument_failures() {
    int calls = 0;
    Module module("m");
    module.add_method<void, ThrowingCopy>("consume", {"value"},
                                         [&](const ThrowingCopy&) { ++calls; });
    Engine engine;
    engine.add(module);
    engine.seal();
    const auto consume = engine.bind_method<void, ThrowingCopy>({"m", "consume"});
    for (bool unknown : {false, true}) {
        const ThrowingCopy input(unknown);
        const auto diagnostic = expect_error(ErrorCode::execution_failed, {"m", "consume"}, [&] {
            consume(input);
        });
        if (!unknown) {
            CHECK(diagnostic.message.find("copy failed") != std::string::npos);
        }
        CHECK(calls == 0);
    }
}

void binding_value_failures() {
    bool unknown = false;
    int reads = 0;
    Module module("m");
    module.add_value<ThrowingExtraction>("value", [&] {
        ++reads;
        return ThrowingExtraction(unknown);
    });
    Engine engine;
    engine.add(module);
    engine.seal();
    const auto value = engine.bind_value<ThrowingExtraction>({"m", "value"});
    for (bool non_standard : {false, true}) {
        unknown = non_standard;
        const auto diagnostic = expect_error(ErrorCode::execution_failed, {"m", "value"}, [&] {
            value.read();
        });
        if (!unknown) {
            CHECK(diagnostic.message.find("second move failed") != std::string::npos);
        }
    }
    CHECK(reads == 2);
}

void binding_result_failures() {
    int calls = 0;
    Module module("m");
    module.add_method<ThrowingExtraction, bool>("produce", {"unknown"}, [&](bool unknown) {
        ++calls;
        return ThrowingExtraction(unknown);
    });
    Engine engine;
    engine.add(module);
    engine.seal();
    const auto produce = engine.bind_method<ThrowingExtraction, bool>({"m", "produce"});
    for (bool unknown : {false, true}) {
        const auto diagnostic = expect_error(ErrorCode::execution_failed, {"m", "produce"}, [&] {
            produce(unknown);
        });
        if (!unknown) {
            CHECK(diagnostic.message.find("second move failed") != std::string::npos);
        }
    }
    CHECK(calls == 2);  // 结果取出失败不回滚已执行的方法。
}

void stateful_callbacks() {
    Module module("m");
    module.add_method<Integer>("next", {}, [value = Integer{0}]() mutable { return ++value; });
    Engine engine;
    engine.add(module);
    engine.seal();
    const auto next = engine.bind_method<Integer>({"m", "next"});
    CHECK(next() == 1);
    CHECK(next() == 2);
    CHECK(std::any_cast<Integer>(engine.call({"m", "next"}, {})) == 3);
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"non_spatial_model", non_spatial_model},
        {"custom_values", custom_values},
        {"transport_values", transport_values},
        {"catalog_is_passive", catalog_is_passive},
        {"scoped_references", scoped_references},
        {"invalid_declarations", invalid_declarations},
        {"duplicate_registration", duplicate_registration},
        {"configuration_validation", configuration_validation},
        {"validation_reentry", validation_reentry},
        {"unresolved_dependencies", unresolved_dependencies},
        {"dependency_kind", dependency_kind},
        {"lifecycle", lifecycle},
        {"binding_checks", binding_checks},
        {"invocation_checks", invocation_checks},
        {"callback_failures", callback_failures},
        {"binding_argument_failures", binding_argument_failures},
        {"binding_value_failures", binding_value_failures},
        {"binding_result_failures", binding_result_failures},
        {"binding_lifetime", binding_lifetime},
        {"stateful_callbacks", stateful_callbacks},
    };
    if (argc != 2 || tests.find(argv[1]) == tests.end()) {
        std::cerr << "Specify a known test case\n";
        return 2;
    }
    try {
        tests.at(argv[1])();
        std::cout << argv[1] << ": passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << argv[1] << ": " << error.what() << '\n';
        return 1;
    }
}
