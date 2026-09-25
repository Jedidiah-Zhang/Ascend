#include <ascend/engine.hpp>

#include <cstdint>
#include <functional>
#include <iostream>
#include <map>
#include <memory>
#include <optional>
#include <stdexcept>
#include <string>

namespace {
using namespace ascend;
using Integer = std::int64_t;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

template <class F>
Diagnostic error(ErrorCode code, Reference target, F&& function) {
    try {
        function();
    } catch (const EngineError& failure) {
        CHECK(failure.diagnostic().code == code);
        CHECK(failure.diagnostic().target == target);
        CHECK(!failure.diagnostic().message.empty());
        return failure.diagnostic();
    }
    throw std::runtime_error("expected EngineError");
}

MethodOptions contract(std::string name) {
    MethodOptions result;
    result.contract = std::move(name);
    return result;
}

Module source(std::string name, Integer value) {
    Module result(std::move(name));
    result.add_value<Integer>("value", [value] { return value; }, {}, "example.scalar.v1");
    return result;
}

Module counter(std::string name, Integer initial = 0) {
    Module result(std::move(name));
    result.require_value<Integer>("input", "example.scalar.v1");
    auto state = std::make_shared<Integer>(initial);
    Module storage("state");
    storage.add_value<Integer>("value", [state] { return *state; }, {}, "example.scalar.v1");
    storage.add_method<void, Integer>("set", {"value"}, [state](Integer value) {
        *state = value;
    }, contract("example.write.v1"));
    Module update("update");
    const auto input = update.require_value<Integer>("input", "example.scalar.v1");
    const auto old = update.require_value<Integer>("old", "example.scalar.v1");
    const auto write = update.require_method<void, Integer>("write", "example.write.v1");
    update.add_method<void>("advance", {}, [input, old, write](const Context& context) {
        write(context, old.read(context) + 2 * input.read(context));
    }, contract("example.advance.v1"));
    result.add(std::move(update));
    result.add(std::move(storage));
    result.forward("input", {"update", "input"});
    result.connect({"update", "old"}, {"state", "value"});
    result.connect({"update", "write"}, {"state", "set"});
    result.export_symbol("advance", {"update", "advance"});
    result.export_symbol("output", {"state", "value"});
    return result;
}

Module wrapped(std::string name, Integer initial = 0) {
    Module result(std::move(name));
    result.require_value<Integer>("input", "example.scalar.v1");
    result.add(counter("counter", initial));
    result.forward("input", {"counter", "input"});
    result.export_symbol("advance", {"counter", "advance"});
    result.export_symbol("output", {"counter", "output"});
    return result;
}

Module stimulus(std::string name, Integer initial) {
    Module result(std::move(name));
    auto current = std::make_shared<Integer>(initial);
    result.add_value<Integer>("value", [current] { return *current; },
                              "Externally driven input", "example.scalar.v1");
    result.add_method<void, Integer>("drive", {"value"}, [current](Integer value) {
        *current = value;
    }, contract("example.scalar-drive.v1"));
    return result;
}

void model() {
    Engine engine;
    engine.add(wrapped("baseline"));
    engine.add(wrapped("alternate"));
    engine.add(source("two", 2));
    engine.add(source("three", 3));
    engine.connect({"baseline", "input"}, {"two", "value"});
    engine.connect({"alternate", "input"}, {"three", "value"});
    CHECK(engine.check().empty());
    engine.seal();
    auto base = engine.bind_method<void>({"baseline", "advance"});
    auto other = engine.bind_method<void>({"alternate", "advance"});
    base();
    other();
    base();
    other();
    CHECK(engine.bind_value<Integer>({"baseline", "output"}).read() == 8);
    CHECK(engine.bind_value<Integer>({"alternate", "output"}).read() == 12);
    base();
    CHECK(engine.bind_value<Integer>({"baseline", "output"}).read() == 12);
    CHECK(engine.bind_value<Integer>({"alternate", "output"}).read() == 12);
}

void testbench_stimulus() {
    Engine engine;
    engine.add(wrapped("dut", 1));
    engine.add(stimulus("stimulus", 0));
    engine.connect({"dut", "input"}, {"stimulus", "value"});
    CHECK(engine.check().empty());
    engine.seal();

    const auto drive = engine.bind_method<void, Integer>({"stimulus", "drive"});
    const auto tick = engine.bind_method<void>({"dut", "advance"});
    const auto output = engine.bind_value<Integer>({"dut", "output"});

    CHECK(output.read() == 1);  // 初值由 DUT 工厂提供。
    drive(2);                   // 改变外部输入，不会暗中推进 DUT。
    CHECK(output.read() == 1);
    tick();                     // 一次显式调用就是一次 testbench 驱动步。
    CHECK(output.read() == 5);

    drive(1);
    CHECK(output.read() == 5);
    tick();
    CHECK(output.read() == 7);

    drive(0);
    tick();
    CHECK(output.read() == 7);
}

void discovery() {
    int calls = 0;
    Module consumer("consumer");
    consumer.require_value<Integer>("input", "example.scalar.v1", "Required scalar");
    Module provider("provider");
    provider.add_value<Integer>("value", [&] { ++calls; return Integer{4}; },
                                "Visible scalar", "example.scalar.v1");
    provider.add_value<Integer>("different", [] { return Integer{4}; }, {}, "other.v1");
    Engine engine;
    engine.add(consumer);
    engine.add(provider);
    engine.add(counter("nested"));
    auto needs = engine.requirements();
    CHECK(needs.size() == 2);
    CHECK(needs[0].reference == Reference{"consumer", "input"});
    CHECK(needs[0].description == "Required scalar");
    needs[0].contract = "external edit";
    CHECK(engine.requirements()[0].contract == "example.scalar.v1");
    const auto candidates = engine.candidates({"consumer", "input"});
    CHECK(candidates.size() == 2);  // provider.value 及 nested.output。
    CHECK(candidates[0].reference == Reference{"nested", "output"});
    CHECK(candidates[1].reference == Reference{"provider", "value"});
    CHECK(engine.catalog("nested").size() == 3);
    CHECK(engine.requirements("nested").size() == 3);
    engine.connect({"consumer", "input"}, {"provider", "value"});
    engine.connect({"nested", "input"}, {"provider", "value"});
    CHECK(engine.connections().size() == 2);
    CHECK(engine.connections("nested").size() == 3);
    CHECK(engine.check().empty());
    engine.seal();
    CHECK(calls == 0);
}

void missing() {
    Engine engine;
    Module consumer("consumer");
    auto input = consumer.require_value<Integer>("input", "example.scalar.v1");
    consumer.add_value<Integer>("output", [input](const Context& context) { return input.read(context); });
    engine.add(consumer);
    CHECK(engine.check().size() == 1);
    error(ErrorCode::unconnected_requirement, {"consumer", "input"}, [&] { engine.seal(); });
    error(ErrorCode::registration_open, {"consumer", "output"}, [&] { engine.read({"consumer", "output"}); });
    engine.connect({"consumer", "input"}, {"missing", "value"});
    error(ErrorCode::missing_symbol, {"consumer", "input"}, [&] { engine.seal(); });
    engine.add(source("missing", 9));
    engine.seal();
    CHECK(engine.bind_value<Integer>({"consumer", "output"}).read() == 9);

    Engine typo;
    typo.add(source("p", 1));
    typo.add(Module("c"));
    typo.connect({"c", "typo"}, {"p", "value"});
    error(ErrorCode::missing_requirement, {"c", "typo"}, [&] { typo.seal(); });

    Engine multiple;
    Module unconnected("u");
    unconnected.require_value<Integer>("first", "example.scalar.v1");
    unconnected.require_value<Integer>("second", "example.scalar.v1");
    multiple.add(unconnected);
    CHECK(multiple.check().size() == 2);
}

void mismatches() {
    Module consumer("c");
    consumer.require_method<Integer, Integer>("method", "example.method.v1");
    Module provider("p");
    provider.add_value<Integer>("value", [] { return Integer{1}; }, {}, "example.method.v1");
    provider.add_method<Integer, Integer>("contract", {"x"}, [](Integer x) { return x; }, contract("other.v1"));
    provider.add_method<double, Integer>("result", {"x"}, [](Integer) { return 1.0; }, contract("example.method.v1"));
    provider.add_method<Integer>("arity", {}, [] { return Integer{1}; }, contract("example.method.v1"));
    provider.add_method<Integer, double>("argument", {"x"}, [](double) { return Integer{1}; }, contract("example.method.v1"));
    provider.add_method<Integer, Integer>("ok", {"x"}, [](Integer x) { return x; }, contract("example.method.v1"));
    Engine engine;
    engine.add(consumer);
    engine.add(provider);
    const std::map<std::string, ErrorCode> cases = {
        {"value", ErrorCode::wrong_kind}, {"contract", ErrorCode::contract_mismatch},
        {"result", ErrorCode::type_mismatch}, {"arity", ErrorCode::argument_count},
        {"argument", ErrorCode::type_mismatch},
    };
    for (const auto& item : cases) {
        engine.connect({"c", "method"}, {"p", item.first});
        error(item.second, {"c", "method"}, [&] { engine.seal(); });
        engine.disconnect({"c", "method"});
    }
    engine.connect({"c", "method"}, {"p", "ok"});
    engine.seal();
}

void names() {
    Module group("group");
    group.add(source("p", 1));
    error(ErrorCode::duplicate_module, {"p", ""}, [&] { group.add(source("p", 2)); });
    group.require_value<Integer>("port", "example.scalar.v1");
    error(ErrorCode::duplicate_requirement, {"group", "port"}, [&] {
        group.require_value<Integer>("port", "example.scalar.v1");
    });
    error(ErrorCode::invalid_declaration, {"group", "empty"}, [&] {
        group.require_value<Integer>("empty", "");
    });
    group.export_symbol("port", {"p", "value"});  // 需求与提供项分别命名。
    error(ErrorCode::duplicate_symbol, {"group", "port"}, [&] {
        group.add_value<Integer>("port", [] { return Integer{2}; });
    });
    group.forward("port", {"c", "input"});
    error(ErrorCode::duplicate_connection, {"c", "input"}, [&] {
        group.connect({"c", "input"}, {"p", "value"});
    });
}

void boundaries() {
    error(ErrorCode::invalid_declaration, {"a/b", ""}, [] { Module invalid("a/b"); });
    Engine engine;
    engine.add(counter("group"));
    engine.add(source("source", 2));
    error(ErrorCode::invalid_declaration, {"group/update", "input"}, [&] {
        engine.connect({"group/update", "input"}, {"source", "value"});
    });
    engine.connect({"group", "input"}, {"source", "value"});
    engine.seal();
    error(ErrorCode::missing_symbol, {"group/state", "value"}, [&] {
        engine.read({"group/state", "value"});
    });
    CHECK(engine.catalog("group").size() == 3);  // 浏览不授予内部调用入口。
    error(ErrorCode::missing_module, {"absent", ""}, [&] { engine.catalog("absent"); });
}

void exports() {
    Module group("group");
    group.export_symbol("output", {"p", "absent"});
    group.add(source("p", 1));
    Engine engine;
    engine.add(group);
    error(ErrorCode::missing_symbol, {"group", "output"}, [&] { engine.seal(); });

    Module missing("missing");
    missing.export_symbol("output", {"later", "value"});
    Engine repair;
    repair.add(missing);
    error(ErrorCode::missing_symbol, {"missing", "output"}, [&] { repair.seal(); });
    repair.add(source("later", 7), "missing");
    repair.seal();
    CHECK(repair.bind_value<Integer>({"missing", "output"}).read() == 7);
}

void forwarding() {
    Module outer("outer");
    outer.add(counter("inner"));
    // 同名提供方不会从父级自动导入。
    outer.add(source("input", 2));
    Engine engine;
    engine.add(outer);
    error(ErrorCode::unconnected_requirement, {"outer/inner", "input"}, [&] { engine.seal(); });
    engine.connect({"inner", "input"}, {"input", "value"}, "outer");
    engine.seal();

    Module broken("broken");
    broken.add(counter("inner"));
    broken.forward("not_declared", {"inner", "input"});
    Engine invalid;
    invalid.add(broken);
    error(ErrorCode::missing_requirement, {"broken/inner", "input"}, [&] { invalid.seal(); });

    Module mismatch("mismatch");
    mismatch.require_value<double>("input", "example.scalar.v1");
    mismatch.add(counter("inner"));
    mismatch.forward("input", {"inner", "input"});
    Engine incompatible;
    incompatible.add(mismatch);
    const auto diagnostics = incompatible.check();
    CHECK(diagnostics.size() == 2);
    CHECK(diagnostics[0].code == ErrorCode::unconnected_requirement);
    CHECK(diagnostics[1].code == ErrorCode::type_mismatch);
    CHECK(diagnostics[1].target == Reference{"mismatch/inner", "input"});
}

void order() {
    for (bool reverse : {false, true}) {
        Engine engine;
        engine.connect({"c", "input"}, {"s", "value"});
        if (reverse) { engine.add(source("s", 3)); engine.add(counter("c")); }
        else { engine.add(counter("c")); engine.add(source("s", 3)); }
        engine.seal();
        engine.bind_method<void>({"c", "advance"})();
        CHECK(engine.bind_value<Integer>({"c", "output"}).read() == 6);
    }
}

Module peer(std::string name, const std::shared_ptr<Integer>& calls) {
    Module module(std::move(name));
    const auto next = module.require_method<Integer, Integer>("next", "example.recurse.v1");
    module.add_method<Integer, Integer>("run", {"depth"}, [calls, next](const Context& context, Integer depth) {
        ++*calls;
        return depth == 0 ? Integer{0} : 1 + next(context, depth - 1);
    }, contract("example.recurse.v1"));
    return module;
}

void cycles() {
    std::weak_ptr<Integer> lifetime;
    {
        const auto run = [&] {
            auto calls = std::make_shared<Integer>(0);
            lifetime = calls;
            Engine engine;
            engine.add(peer("a", calls));
            engine.add(peer("b", calls));
            engine.connect({"a", "next"}, {"b", "run"});
            engine.connect({"b", "next"}, {"a", "run"});
            engine.seal();
            return engine.bind_method<Integer, Integer>({"a", "run"});
        }();
        CHECK(run(5) == 5);
        CHECK(*lifetime.lock() == 6);
    }
    CHECK(lifetime.expired());
}

void lifetime() {
    std::optional<Context> saved;
    Module consumer("c");
    auto input = consumer.require_value<Integer>("input", "example.scalar.v1");
    consumer.add_value<Integer>("value", [&saved, input](const Context& context) {
        saved = context;
        return input.read(context);
    });
    {
        const auto binding = [&] {
            Engine engine;
            engine.add(consumer);
            engine.add(source("s", 8));
            engine.connect({"c", "input"}, {"s", "value"});
            engine.seal();
            return engine.bind_value<Integer>({"c", "value"});
        }();
        CHECK(binding.read() == 8);
        CHECK(input.read(*saved) == 8);
    }
    error(ErrorCode::execution_failed, {"c", "input"}, [&] { input.read(*saved); });

    // 同一个无状态模块构建对象用于两套装配时，需求按当前上下文解析。
    Engine first;
    Engine second;
    first.add(consumer);
    second.add(consumer);
    first.add(source("s", 2));
    second.add(source("s", 3));
    first.connect({"c", "input"}, {"s", "value"});
    second.connect({"c", "input"}, {"s", "value"});
    first.seal();
    second.seal();
    CHECK(first.bind_value<Integer>({"c", "value"}).read() == 2);
    CHECK(second.bind_value<Integer>({"c", "value"}).read() == 3);
    CHECK(first.bind_value<Integer>({"c", "value"}).read() == 2);
}

void closed() {
    Engine engine;
    engine.add(counter("c"));
    engine.add(source("s", 2));
    engine.connect({"c", "input"}, {"s", "value"});
    engine.seal();
    engine.seal();
    error(ErrorCode::registration_closed, {"c", "input"}, [&] { engine.disconnect({"c", "input"}); });
    error(ErrorCode::registration_closed, {"c", "input"}, [&] { engine.connect({"c", "input"}, {"s", "value"}); });
    error(ErrorCode::registration_closed, {"later", ""}, [&] { engine.add(Module("later")); });
}

void context_identity() {
    Module foreign("foreign");
    auto alien = foreign.require_value<Integer>("input", "example.scalar.v1");
    Module module("c");
    module.require_value<Integer>("input", "example.scalar.v1");
    module.add_value<Integer>("bad", [alien](const Context& context) { return alien.read(context); });
    Engine engine;
    engine.add(module);
    engine.add(source("s", 1));
    engine.connect({"c", "input"}, {"s", "value"});
    engine.seal();
    error(ErrorCode::execution_failed, {"c", "bad"}, [&] { engine.read({"c", "bad"}); });
}

void failures() {
    Module provider("p");
    provider.add_method<void>("fail", {}, [] { throw std::runtime_error("domain failure"); }, contract("example.fail.v1"));
    Module consumer("c");
    const auto invoke = consumer.require_method<void>("invoke", "example.fail.v1");
    consumer.add_method<void>("run", {}, [invoke](const Context& context) { invoke(context); });
    Engine engine;
    engine.add(provider);
    engine.add(consumer);
    engine.connect({"c", "invoke"}, {"p", "fail"});
    engine.seal();
    const auto diagnostic = error(ErrorCode::execution_failed, {"c", "run"}, [&] { engine.call({"c", "run"}, {}); });
    CHECK(diagnostic.message.find("domain failure") != std::string::npos);
    CHECK(diagnostic.message.find("p/fail") != std::string::npos);

    int validations = 0;
    Module group("group");
    group.add(Module("invalid", [&] { ++validations; throw std::runtime_error("invalid configuration"); }));
    Engine validation;
    error(ErrorCode::validation_failed, {"group/invalid", ""}, [&] { validation.add(group); });
    CHECK(validations == 1);
    CHECK(validation.catalog().empty());
}
}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"model", model}, {"discovery", discovery}, {"missing", missing},
        {"stimulus", testbench_stimulus},
        {"mismatches", mismatches}, {"names", names}, {"boundaries", boundaries},
        {"exports", exports}, {"forwarding", forwarding}, {"order", order},
        {"cycles", cycles}, {"lifetime", lifetime}, {"closed", closed},
        {"context_identity", context_identity}, {"failures", failures},
    };
    if (argc != 2 || tests.count(argv[1]) == 0) return 2;
    try {
        tests.at(argv[1])();
        std::cout << argv[1] << ": passed\n";
        return 0;
    } catch (const std::exception& failure) {
        std::cerr << argv[1] << ": " << failure.what() << '\n';
        return 1;
    }
}
