#include <ascend/assembly.hpp>

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <iterator>
#include <limits>
#include <locale>
#include <map>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using namespace ascend;
using Integer = std::int64_t;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

template <class F>
Diagnostic failure(ErrorCode code, Reference target, F&& function) {
    try {
        function();
    } catch (const EngineError& error) {
        const auto& diagnostic = error.diagnostic();
        if (diagnostic.code != code || !(diagnostic.target == target)) {
            throw std::runtime_error(
                "unexpected diagnostic " + diagnostic.target.module + '/' + diagnostic.target.symbol + ": " +
                render_diagnostic(diagnostic) + " (expected " + std::to_string(static_cast<int>(code)) + " at " +
                target.module + '/' + target.symbol + ", received " +
                std::to_string(static_cast<int>(diagnostic.code)) + ")");
        }
        CHECK(!render_diagnostic(diagnostic).empty());
        return diagnostic;
    }
    throw std::runtime_error("expected EngineError");
}

MethodOptions contract(std::string name) {
    MethodOptions options;
    options.contract = std::move(name);
    return options;
}

constexpr const char* kAccumulator = "example.accumulator";
constexpr const char* kStimulus = "example.stimulus";

// 工厂接收实例名与构造配置；内部组合由作者维护，外部只通过公开边界装配。
Module accumulator(const std::string& instance, const Config& config) {
    const Config* field = config.find("initial");
    if (!field) throw std::runtime_error("initial is required");
    const Integer initial = field->integer();
    if (initial < -1000000 || initial > 1000000) throw std::runtime_error("initial is out of range");

    Module model(instance);
    model.require_value<Integer>("input", "example.scalar.v1");
    auto value = std::make_shared<Integer>(initial);
    Module state("state");
    state.add_value<Integer>("value", [value] { return *value; }, "Accumulated output", "example.scalar.v1");
    state.add_method<void, Integer>("write", {"next"}, [value](Integer next) { *value = next; },
                                    contract("example.write.v1"));
    Module update("update");
    const auto input = update.require_value<Integer>("input", "example.scalar.v1");
    const auto old = update.require_value<Integer>("old", "example.scalar.v1");
    const auto write = update.require_method<void, Integer>("write", "example.write.v1");
    update.add_method<void>("advance", {}, [input, old, write](const Context& context) {
        write(context, old.read(context) + 2 * input.read(context));
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

Module stimulus(const std::string& instance, const Config& config) {
    Integer initial = 0;
    if (const Config* field = config.find("value")) initial = field->integer();
    Module result(instance);
    auto value = std::make_shared<Integer>(initial);
    result.add_value<Integer>("value", [value] { return *value; }, "Testbench-driven input", "example.scalar.v1");
    result.add_method<void, Integer>("drive", {"next"}, [value](Integer next) { *value = next; },
                                     contract("example.scalar-drive.v1"));
    return result;
}

ModuleFactoryDirectory default_factories() {
    ModuleFactoryDirectory factories;
    factories.add_definition(kAccumulator, accumulator);
    factories.add_definition(kStimulus, stimulus);
    return factories;
}

// 两个作用域使用同名局部实例，转接作用域需求并导出接口。
AssemblyDefinition assembly_definition(Integer baseline_initial = 0, Integer alternate_initial = 0) {
    AssemblyDefinition definition;
    definition.add_scope("baseline");
    definition.add_scope("alternate");
    definition.add_instance(kAccumulator, "model",
                            Config::object({{"initial", Config::integer(baseline_initial)}}), "baseline");
    definition.add_instance(kAccumulator, "model",
                            Config::object({{"initial", Config::integer(alternate_initial)}}), "alternate");
    definition.forward_inherited("input", {"model", "input"}, "baseline");
    definition.forward_inherited("input", {"model", "input"}, "alternate");
    definition.export_symbol("advance", {"model", "advance"}, "baseline");
    definition.export_symbol("output", {"model", "output"}, "baseline");
    definition.export_symbol("advance", {"model", "advance"}, "alternate");
    definition.export_symbol("output", {"model", "output"}, "alternate");
    definition.add_instance(kStimulus, "input_baseline", Config::object({{"value", Config::integer(0)}}));
    definition.add_instance(kStimulus, "input_alternate", Config::object({{"value", Config::integer(0)}}));
    definition.connect({"baseline", "input"}, {"input_baseline", "value"});
    definition.connect({"alternate", "input"}, {"input_alternate", "value"});
    return definition;
}

using Outputs = std::vector<std::pair<Integer, Integer>>;

bool equals(const Outputs& outputs, std::initializer_list<std::pair<Integer, Integer>> expected) {
    return outputs == Outputs(expected);
}

// 每次运行使用独立临时目录，避免固定文件名覆盖已有文件或并发运行互相干扰。
class TempDirectory {
public:
    explicit TempDirectory(const std::string& tag) {
        std::random_device random;
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        const auto base = std::filesystem::temp_directory_path();
        for (int attempt = 0; attempt < 100; ++attempt) {
            const auto candidate = base / (tag + "-" + std::to_string(stamp) + "-" +
                                           std::to_string(random()) + "-" + std::to_string(attempt));
            std::error_code error;
            if (std::filesystem::create_directory(candidate, error)) {
                path_ = candidate;
                return;
            }
        }
        throw std::runtime_error("cannot create a temporary directory");
    }

    ~TempDirectory() {
        std::error_code error;
        std::filesystem::remove_all(path_, error);
    }

    TempDirectory(const TempDirectory&) = delete;
    TempDirectory& operator=(const TempDirectory&) = delete;

    const std::filesystem::path& path() const { return path_; }

private:
    std::filesystem::path path_;
};

struct CommaNumpunct : std::numpunct<char> {
    char do_decimal_point() const override { return ','; }
};

// 优先使用系统逗号小数区域；没有时用自定义 facet 构造，保证该分支始终执行。
std::locale comma_locale() {
    for (const char* name : {"de_DE.UTF-8", "de_DE.utf8", "de_DE", "de-DE", "German_Germany.1252",
                             "fr_FR.UTF-8", "fr_FR.utf8", "fr_FR", "fr-FR", "French_France.1252"}) {
        try {
            const std::locale candidate(name);
            if (std::use_facet<std::numpunct<char>>(candidate).decimal_point() == ',') return candidate;
        } catch (const std::exception&) {
        }
    }
    return std::locale(std::locale::classic(), new CommaNumpunct);
}

// testbench：先驱动输入，再显式推进，最后采样公开输出。
Outputs drive(Engine& engine) {
    engine.seal();
    const auto baseline_step = engine.bind_method<void>({"baseline", "advance"});
    const auto alternate_step = engine.bind_method<void>({"alternate", "advance"});
    const auto drive_baseline = engine.bind_method<void, Integer>({"input_baseline", "drive"});
    const auto drive_alternate = engine.bind_method<void, Integer>({"input_alternate", "drive"});
    const auto baseline_output = engine.bind_value<Integer>({"baseline", "output"});
    const auto alternate_output = engine.bind_value<Integer>({"alternate", "output"});
    constexpr std::array<std::pair<Integer, Integer>, 3> stimuli = {{{2, 3}, {1, 2}, {1, 1}}};
    Outputs outputs;
    for (const auto& stimulus : stimuli) {
        drive_baseline(stimulus.first);
        drive_alternate(stimulus.second);
        baseline_step();
        alternate_step();
        outputs.emplace_back(baseline_output.read(), alternate_output.read());
    }
    return outputs;
}

void factories() {
    ModuleFactoryDirectory directory;
    directory.add_definition("b", stimulus);
    directory.add_definition("a", accumulator);
    CHECK(directory.contains("a"));
    CHECK(!directory.contains("c"));
    CHECK(directory.definitions() == std::vector<std::string>({"a", "b"}));
    CHECK(!directory.contains(""));

    // 每次构造返回独立实例，名称来自调用方。
    Engine engine;
    engine.add(directory.create("b", "first", Config::object({{"value", Config::integer(1)}})));
    engine.add(directory.create("b", "second", Config::object({{"value", Config::integer(2)}})));
    engine.seal();
    CHECK(engine.bind_value<Integer>({"first", "value"}).read() == 1);
    CHECK(engine.bind_value<Integer>({"second", "value"}).read() == 2);
}

void definition() {
    ModuleFactoryDirectory directory = default_factories();
    AssemblyDefinition definition = assembly_definition();
    Engine engine = definition.instantiate(directory);
    CHECK(engine.check().empty());
    CHECK(equals(drive(engine), {{4, 6}, {6, 10}, {8, 12}}));
}

void definition_access() {
    AssemblyDefinition definition = assembly_definition(2, 0);
    CHECK((definition.scopes() == std::vector<std::string>{"", "alternate", "baseline"}));
    const auto root_instances = definition.instances();
    CHECK(root_instances.size() == 2);
    CHECK(root_instances[0].name == "input_baseline");
    CHECK(root_instances[1].name == "input_alternate");
    CHECK(definition.instances("baseline").size() == 1);
    CHECK(definition.instances("baseline")[0].definition == kAccumulator);
    CHECK(definition.instances("baseline")[0].name == "model");
    CHECK(definition.instances("baseline")[0].config.find("initial")->integer() == 2);
    failure(ErrorCode::invalid_assembly, {"absent", {}}, [&] { definition.instances("absent"); });
    failure(ErrorCode::invalid_assembly, {"baseline/typo", {}}, [&] {
        definition.set_instance_config("typo", Config::object({{"initial", Config::integer(1)}}), "baseline");
    });

    // 实例快照是只读副本；替换配置不改变实例集合与连接。
    auto copy = definition.instances("baseline");
    copy[0].config = Config{};
    CHECK(definition.instances("baseline")[0].config.find("initial")->integer() == 2);
    definition.set_instance_config("model", Config::object({{"initial", Config::integer(7)}}), "baseline");
    CHECK(definition.instances().size() == 2);

    // 同一份定义驱动保存与实例化：替换后的配置随记录往返并在重建实例后生效。
    const auto loaded = AssemblyDefinition::parse(definition.to_json());
    CHECK(loaded.scopes() == definition.scopes());
    CHECK(loaded.instances("baseline")[0].config.find("initial")->integer() == 7);
    Engine engine = loaded.instantiate(default_factories());
    CHECK(engine.check().empty());
    engine.seal();
    const auto drive_input = engine.bind_method<void, Integer>({"input_baseline", "drive"});
    const auto step = engine.bind_method<void>({"baseline", "advance"});
    const auto output = engine.bind_value<Integer>({"baseline", "output"});
    CHECK(output.read() == 7);
    drive_input(2);
    step();
    CHECK(output.read() == 11);
}

void roundtrip() {
    ModuleFactoryDirectory directory = default_factories();
    AssemblyDefinition definition = assembly_definition();
    const std::string text = definition.to_json();
    AssemblyDefinition restored = AssemblyDefinition::parse(text, "memory.record");
    CHECK(restored.source() == "memory.record");
    CHECK(restored.to_json() == text);
    Engine engine = restored.instantiate(directory);
    CHECK(engine.check().empty());
    CHECK(equals(drive(engine), {{4, 6}, {6, 10}, {8, 12}}));

    // 工厂内部组合保持封装：记录只依赖公开边界。
    CHECK(text.find("\"state\"") == std::string::npos);
    CHECK(text.find("\"update\"") == std::string::npos);
    CHECK(text.find("example.write.v1") == std::string::npos);
    CHECK(text.find("example.accumulator") != std::string::npos);
    CHECK(text.find("\"model\"") != std::string::npos);
}

void config_change() {
    ModuleFactoryDirectory directory = default_factories();
    Engine engine = assembly_definition(2).instantiate(directory);
    CHECK(equals(drive(engine), {{6, 6}, {8, 10}, {10, 12}}));
}

void wiring() {
    ModuleFactoryDirectory directory = default_factories();
    const auto build = [&](bool reverse, const std::string& provider) {
        AssemblyDefinition definition;
        definition.add_instance(kAccumulator, "dut", Config::object({{"initial", Config::integer(0)}}));
        const Config value_two = Config::object({{"value", Config::integer(2)}});
        const Config value_three = Config::object({{"value", Config::integer(3)}});
        if (reverse) {
            definition.add_instance(kStimulus, "input_three", value_three);
            definition.add_instance(kStimulus, "input_two", value_two);
        } else {
            definition.add_instance(kStimulus, "input_two", value_two);
            definition.add_instance(kStimulus, "input_three", value_three);
        }
        definition.connect({"dut", "input"}, {provider, "value"});
        return definition.instantiate(directory);
    };
    // 显式连接决定提供方；记录顺序不参与选择。
    Engine three = build(false, "input_three");
    three.seal();
    three.bind_method<void>({"dut", "advance"})();
    CHECK(three.bind_value<Integer>({"dut", "output"}).read() == 6);
    Engine reversed = build(true, "input_three");
    reversed.seal();
    reversed.bind_method<void>({"dut", "advance"})();
    CHECK(reversed.bind_value<Integer>({"dut", "output"}).read() == 6);
    Engine other = build(true, "input_two");
    other.seal();
    other.bind_method<void>({"dut", "advance"})();
    CHECK(other.bind_value<Integer>({"dut", "output"}).read() == 4);
}

void editing_instance() {
    ModuleFactoryDirectory directory = default_factories();
    AssemblyDefinition definition;
    definition.add_instance(kAccumulator, "dut", Config::object({{"initial", Config::integer(0)}}));
    definition.add_instance(kStimulus, "input_two", Config::object({{"value", Config::integer(2)}}));
    definition.add_instance(kStimulus, "input_three", Config::object({{"value", Config::integer(3)}}));
    definition.connect({"dut", "input"}, {"input_two", "value"});

    // 断开后重新接到另一个提供方：选择随连接改变；重复断开返回 false。
    CHECK(definition.disconnect({"dut", "input"}));
    CHECK(!definition.disconnect({"dut", "input"}));
    definition.connect({"dut", "input"}, {"input_three", "value"});
    Engine engine = definition.instantiate(directory);
    engine.seal();
    engine.bind_method<void>({"dut", "advance"})();
    CHECK(engine.bind_value<Integer>({"dut", "output"}).read() == 6);

    // 移除实例：实例消失、其余实例不变；移除不存在的实例报告 invalid_assembly。
    definition.remove_instance("input_two");
    CHECK(definition.instances().size() == 2);
    const std::string text = definition.to_json();
    CHECK(text.find("input_two") == std::string::npos);
    CHECK(text.find("input_three") != std::string::npos);
    Engine after = AssemblyDefinition::parse(text).instantiate(directory);
    after.seal();
    after.bind_method<void>({"dut", "advance"})();
    CHECK(after.bind_value<Integer>({"dut", "output"}).read() == 6);
    failure(ErrorCode::invalid_assembly, {"input_two", {}}, [&] { definition.remove_instance("input_two"); });

    // 移除仍被连接引用的实例：同一作用域内引用它的连接一并移除，需求变为未连接。
    AssemblyDefinition target;
    target.add_instance(kAccumulator, "dut", Config::object({{"initial", Config::integer(0)}}));
    target.add_instance(kStimulus, "input_two", Config::object({{"value", Config::integer(2)}}));
    target.connect({"dut", "input"}, {"input_two", "value"});
    target.remove_instance("input_two");
    CHECK(target.instances().size() == 1);
    Engine unconnected = target.instantiate(directory);
    CHECK(!unconnected.check().empty());

    // 非根作用域：移除实例同时移除引用它的转接与导出。
    AssemblyDefinition composed;
    composed.add_scope("group");
    composed.add_instance(kAccumulator, "dut", Config::object({{"initial", Config::integer(0)}}), "group");
    composed.add_instance(kStimulus, "input", Config::object({{"value", Config::integer(2)}}), "group");
    composed.connect({"dut", "input"}, {"input", "value"}, "group");
    composed.forward_inherited("input", {"dut", "input"}, "group");
    composed.export_symbol("output", {"dut", "output"}, "group");
    composed.remove_instance("dut", "group");
    CHECK(composed.instances("group").size() == 1);
    const std::string composed_text = composed.to_json();
    CHECK(composed_text.find("dut") == std::string::npos);  // 实例、连接、转接与导出均不再引用
    CHECK((AssemblyDefinition::parse(composed_text).scopes() == std::vector<std::string>{"", "group"}));
}

void factory_merge() {
    ModuleFactoryDirectory first;
    first.add_definition("shared", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.add_value<Integer>("value", [] { return Integer{1}; }, {}, "example.scalar.v1");
        return module;
    });
    ModuleFactoryDirectory second = default_factories();
    second.add_definition("shared", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.add_value<Integer>("value", [] { return Integer{2}; }, {}, "example.scalar.v1");
        return module;
    });
    first.merge_from(second);
    CHECK(first.contains(kAccumulator));
    CHECK(first.contains(kStimulus));
    CHECK(first.contains("shared"));
    CHECK((first.definitions() == std::vector<std::string>{kAccumulator, kStimulus, "shared"}));
    // 已存在的定义保持不变（先到者优先）；重复合并不报冲突。
    Engine engine;
    engine.add(first.create("shared", "probe", {}));
    engine.seal();
    CHECK(engine.bind_value<Integer>({"probe", "value"}).read() == 1);
    first.merge_from(second);
    CHECK(first.contains(kAccumulator));
}

void independence() {
    ModuleFactoryDirectory directory = default_factories();
    AssemblyDefinition definition = assembly_definition();
    Engine first = definition.instantiate(directory);
    Engine second = definition.instantiate(directory);
    first.seal();
    second.seal();
    const auto drive_first = first.bind_method<void, Integer>({"input_baseline", "drive"});
    const auto step_first = first.bind_method<void>({"baseline", "advance"});
    drive_first(2);
    step_first();
    CHECK(first.bind_value<Integer>({"baseline", "output"}).read() == 4);
    CHECK(second.bind_value<Integer>({"baseline", "output"}).read() == 0);
    CHECK(second.bind_value<Integer>({"alternate", "output"}).read() == 0);
}

void config_values() {
    ModuleFactoryDirectory directory = default_factories();
    directory.add_definition("example.echo", [](const std::string& instance, const Config& config) {
        Module module(instance);
        module.add_value<Integer>("integer", [config] {
            const Config* field = config.find("integer");
            if (!field) throw std::runtime_error("integer is required");
            return field->integer();
        });
        module.add_value<std::string>("label", [config] {
            const Config* field = config.find("label");
            if (!field) throw std::runtime_error("label is required");
            return field->string();
        });
        module.add_value<double>("ratio", [config] {
            const Config* field = config.find("ratio");
            if (!field) throw std::runtime_error("ratio is required");
            return field->number();
        });
        module.add_value<bool>("flag", [config] {
            const Config* field = config.find("flag");
            if (!field) throw std::runtime_error("flag is required");
            return field->boolean();
        });
        return module;
    });
    const std::string text =
        R"({"format":"ascend.assembly","version":1,"instances":[)"
        R"({"definition":"example.echo","name":"echo","config":{"integer":9007199254740993}}]})";
    AssemblyDefinition definition = AssemblyDefinition::parse(text, "memory.record");
    Engine engine = definition.instantiate(directory);
    engine.seal();
    CHECK(engine.bind_value<Integer>({"echo", "integer"}).read() == Integer{9007199254740993});
    CHECK(definition.to_json().find("9007199254740993") != std::string::npos);

    // 有符号 64 位边界在解析、保存与工厂交接中保持精确。
    AssemblyDefinition extremes = AssemblyDefinition::parse(
        R"({"format":"ascend.assembly","version":1,"instances":[)"
        R"({"definition":"example.echo","name":"low","config":{"integer":-9223372036854775808}},)"
        R"({"definition":"example.echo","name":"high","config":{"integer":9223372036854775807}}]})",
        "memory.record");
    Engine bounds = extremes.instantiate(directory);
    bounds.seal();
    CHECK(bounds.bind_value<Integer>({"low", "integer"}).read() == std::numeric_limits<Integer>::min());
    CHECK(bounds.bind_value<Integer>({"high", "integer"}).read() == std::numeric_limits<Integer>::max());

    // 非整数数据按 JSON 可表达形式往返；转义与数字文本保持可解释。
    AssemblyDefinition extras = AssemblyDefinition::parse(
        R"({"format":"ascend.assembly","version":1,"instances":[)"
        R"({"definition":"example.echo","name":"echo","config":)"
        R"({"integer":1,"label":"caf\u00e9","ratio":0.5,"flag":true,"list":[1,2]}}]})",
        "memory.record");
    Engine extra_engine = extras.instantiate(directory);
    extra_engine.seal();
    CHECK(extra_engine.bind_value<std::string>({"echo", "label"}).read() == std::string("caf\xC3\xA9"));
    CHECK(extra_engine.bind_value<double>({"echo", "ratio"}).read() == 0.5);
    CHECK(extra_engine.bind_value<bool>({"echo", "flag"}).read());
    const std::string text_again = extras.to_json();
    CHECK(text_again.find("caf\xC3\xA9") != std::string::npos);
    CHECK(text_again.find("\"ratio\": 0.5") != std::string::npos);
    CHECK(text_again.find("\"flag\": true") != std::string::npos);
    CHECK(text_again.find("\"list\"") != std::string::npos);

    // 进程区域使用逗号小数时，记录中的 '.' 必须仍可解析与输出。
    const std::locale previous = std::locale::global(comma_locale());
    try {
        AssemblyDefinition localized = AssemblyDefinition::parse(
            R"({"format":"ascend.assembly","version":1,"instances":[)"
            R"({"definition":"example.echo","name":"echo","config":)"
            R"({"integer":1,"label":"x","ratio":0.5,"flag":true}}]})",
            "memory.record");
        Engine localized_engine = localized.instantiate(directory);
        localized_engine.seal();
        CHECK(localized_engine.bind_value<double>({"echo", "ratio"}).read() == 0.5);
        CHECK(localized.to_json().find("\"ratio\": 0.5") != std::string::npos);
    } catch (...) {
        std::locale::global(previous);
        throw;
    }
    std::locale::global(previous);

    // 对象成员名不重复；访问器不做隐式转换。
    bool duplicate = false;
    try {
        Config::object({{"a", Config::integer(1)}, {"a", Config::integer(2)}});
    } catch (const std::invalid_argument&) {
        duplicate = true;
    }
    CHECK(duplicate);
    bool converted = false;
    try {
        Config::string("0").integer();
    } catch (const std::invalid_argument&) {
        converted = true;
    }
    CHECK(converted);
}

void record_errors() {
    const auto parse = [](const std::string& text) { return AssemblyDefinition::parse(text, "memory.record"); };

    const Diagnostic syntax = failure(ErrorCode::invalid_json, {}, [&] { parse("{\"instances\": ["); });
    CHECK(syntax.source == "memory.record");
    CHECK(render_diagnostic(syntax).find("line") != std::string::npos);

    const Diagnostic version = failure(ErrorCode::unsupported_format_version, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":2})");
    });
    CHECK(version.path == "/version");
    CHECK(version.source == "memory.record");

    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] { parse(R"({"version":1})"); }).path == "/format");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"other.record","version":1})");
    }).path == "/format");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":"1"})");
    }).path == "/version");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"definitions":[]})");
    }).path == "/definitions");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"instances":{}})");
    }).path == "/instances");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"instances":[{"definition":"d"}]})");
    }).path == "/instances/0/name");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"forwards":[)"
              R"({"requirement":"in","target":{"module":"m","requirement":"in"}}]})");
    }).path == "/forwards");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"exports":[)"
              R"({"name":"x","target":{"module":"m","symbol":"s"}}]})");
    }).path == "/exports");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"scopes":[{"name":"a/b"}]})");
    }).path == "/scopes/0/name");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"scopes":[{"name":"a"},{"name":"a"}]})");
    }).path == "/scopes/1/name");
    CHECK(failure(ErrorCode::invalid_json, {}, [&] {
        parse(R"({"format":"ascend.assembly","format":"ascend.assembly","version":1})");
    }).path == "/format");

    const Diagnostic overflow = failure(ErrorCode::invalid_json, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1,"instances":[)"
              R"({"definition":"d","name":"n","config":{"initial":9223372036854775808}}]})");
    });
    CHECK(overflow.path == "/instances/0/config/initial");

    // 顶层不是对象、空输入与多余内容。
    CHECK(failure(ErrorCode::invalid_assembly, {}, [&] { parse("[]"); }).path.empty());
    CHECK(failure(ErrorCode::invalid_json, {}, [&] { parse(""); }).path.empty());
    CHECK(failure(ErrorCode::invalid_json, {}, [&] {
        parse(R"({"format":"ascend.assembly","version":1} extra)");
    }).path.empty());

    // 省略可选数组与配置仍是有效记录。
    parse(R"({"format":"ascend.assembly","version":1})");
}

void builder_errors() {
    const auto rejects = [](auto&& function) {
        try {
            function();
        } catch (const EngineError& error) {
            return error.diagnostic().code == ErrorCode::invalid_assembly;
        }
        return false;
    };
    AssemblyDefinition definition;
    definition.add_scope("group");
    CHECK(rejects([&] { definition.add_scope("group"); }));
    CHECK(rejects([&] { definition.add_scope("a/b"); }));
    CHECK(rejects([&] { definition.add_scope(""); }));
    CHECK(rejects([&] { definition.add_instance("example.stimulus", "dut", Config{}, "absent"); }));
    CHECK(rejects([&] { definition.forward_inherited("input", {"dut", "input"}); }));
    CHECK(rejects([&] { definition.export_symbol("output", {"dut", "value"}); }));
    definition.add_instance("example.stimulus", "dut", Config{}, "group");
    CHECK(AssemblyDefinition::parse(definition.to_json(), "memory.record").to_json() == definition.to_json());
}

void factory_errors() {
    ModuleFactoryDirectory directory = default_factories();
    failure(ErrorCode::duplicate_definition, {kAccumulator, ""}, [&] {
        directory.add_definition(kAccumulator, stimulus);
    });
    failure(ErrorCode::invalid_declaration, {"", ""}, [&] { directory.add_definition("", stimulus); });
    failure(ErrorCode::unknown_definition, {"dut", ""}, [&] {
        directory.create("absent", "dut", Config{});
    });

    const auto instantiate = [&](const std::string& text) {
        return AssemblyDefinition::parse(text, "memory.record").instantiate(directory);
    };
    const Diagnostic unknown = failure(ErrorCode::unknown_definition, {"dut", ""}, [&] {
        instantiate(R"({"format":"ascend.assembly","version":1,"instances":[)"
                    R"({"definition":"absent","name":"dut"}]})");
    });
    CHECK(unknown.path == "/instances/0/definition");
    CHECK(unknown.source == "memory.record");

    const Diagnostic nested = failure(ErrorCode::unknown_definition, {"group/dut", ""}, [&] {
        instantiate(R"({"format":"ascend.assembly","version":1,"scopes":[{"name":"group","instances":[)"
                    R"({"definition":"absent","name":"dut"}]}]})");
    });
    CHECK(nested.path == "/scopes/0/instances/0/definition");

    const Diagnostic missing = failure(ErrorCode::invalid_config, {"dut", ""}, [&] {
        instantiate(R"({"format":"ascend.assembly","version":1,"instances":[)"
                    R"({"definition":"example.accumulator","name":"dut"}]})");
    });
    CHECK(missing.path == "/instances/0/config");
    CHECK(render_diagnostic(missing).find("initial") != std::string::npos);

    const Diagnostic range = failure(ErrorCode::invalid_config, {"group/dut", ""}, [&] {
        instantiate(R"({"format":"ascend.assembly","version":1,"scopes":[{"name":"group","instances":[)"
                    R"({"definition":"example.accumulator","name":"dut","config":{"initial":1000001}}]}]})");
    });
    CHECK(range.path == "/scopes/0/instances/0/config");
    CHECK(render_diagnostic(range).find("out of range") != std::string::npos);

    const Diagnostic kind = failure(ErrorCode::invalid_config, {"dut", ""}, [&] {
        instantiate(R"({"format":"ascend.assembly","version":1,"instances":[)"
                    R"({"definition":"example.accumulator","name":"dut","config":{"initial":"0"}}]})");
    });
    CHECK(render_diagnostic(kind).find("integer") != std::string::npos);

    directory.add_definition("example.renamed", [](const std::string&, const Config&) { return Module("fixed"); });
    const Diagnostic renamed = failure(ErrorCode::invalid_config, {"dut", ""}, [&] {
        instantiate(R"({"format":"ascend.assembly","version":1,"instances":[)"
                    R"({"definition":"example.renamed","name":"dut"}]})");
    });
    CHECK(render_diagnostic(renamed).find("fixed") != std::string::npos);
}

void assembly_errors() {
    ModuleFactoryDirectory directory = default_factories();

    // 重复实例由引擎的重复模块诊断报告，加载层不另写规则。
    AssemblyDefinition duplicates;
    duplicates.add_scope("group");
    duplicates.add_instance(kStimulus, "dut", Config::object({{"value", Config::integer(0)}}), "group");
    duplicates.add_instance(kStimulus, "dut", Config::object({{"value", Config::integer(0)}}), "group");
    const Diagnostic duplicate = failure(ErrorCode::duplicate_module, {"group/dut", ""}, [&] {
        duplicates.instantiate(directory);
    });
    CHECK(duplicate.path == "/scopes/0/instances/1");

    // 缺失连接在封闭时报告，实例化本身成功。
    AssemblyDefinition disconnected;
    disconnected.add_scope("baseline");
    disconnected.add_instance(kAccumulator, "model",
                              Config::object({{"initial", Config::integer(0)}}), "baseline");
    disconnected.forward_inherited("input", {"model", "input"}, "baseline");
    Engine pending = disconnected.instantiate(directory);
    CHECK(pending.check().size() == 1);
    CHECK(pending.check()[0].code == ErrorCode::unconnected_requirement);
    CHECK(pending.check()[0].target == Reference{"baseline", "input"});
    failure(ErrorCode::unconnected_requirement, {"baseline", "input"}, [&] { pending.seal(); });

    // 未知提供方、契约、种类与签名不匹配复用引擎诊断。
    const auto scope_with = [&](const std::string& definition_id, const std::string& instance) {
        AssemblyDefinition definition;
        definition.add_scope("baseline");
        definition.add_instance(kAccumulator, "model",
                                Config::object({{"initial", Config::integer(0)}}), "baseline");
        definition.forward_inherited("input", {"model", "input"}, "baseline");
        definition.add_instance(definition_id, instance, Config{});
        definition.connect({"baseline", "input"}, {instance, "value"});
        return definition;
    };
    AssemblyDefinition absent;
    absent.add_scope("baseline");
    absent.add_instance(kAccumulator, "model",
                        Config::object({{"initial", Config::integer(0)}}), "baseline");
    absent.forward_inherited("input", {"model", "input"}, "baseline");
    absent.connect({"baseline", "input"}, {"later", "value"});
    Engine absent_engine = absent.instantiate(directory);
    failure(ErrorCode::missing_symbol, {"baseline", "input"}, [&] { absent_engine.seal(); });

    directory.add_definition("example.alien", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.add_value<Integer>("value", [] { return Integer{0}; }, {}, "alien.v1");
        return module;
    });
    Engine alien = scope_with("example.alien", "alien").instantiate(directory);
    failure(ErrorCode::contract_mismatch, {"baseline", "input"}, [&] { alien.seal(); });

    directory.add_definition("example.text", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.add_value<std::string>("value", [] { return std::string("x"); }, {}, "example.scalar.v1");
        return module;
    });
    Engine text = scope_with("example.text", "text").instantiate(directory);
    failure(ErrorCode::type_mismatch, {"baseline", "input"}, [&] { text.seal(); });

    directory.add_definition("example.consumer", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.require_method<Integer, Integer>("fn", "example.fn.v1");
        return module;
    });
    directory.add_definition("example.numeric", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.add_value<Integer>("value", [] { return Integer{1}; }, {}, "example.fn.v1");
        return module;
    });
    AssemblyDefinition kinds;
    kinds.add_instance("example.consumer", "consumer");
    kinds.add_instance("example.numeric", "provider");
    kinds.connect({"consumer", "fn"}, {"provider", "value"});
    Engine kind = kinds.instantiate(directory);
    failure(ErrorCode::wrong_kind, {"consumer", "fn"}, [&] { kind.seal(); });

    directory.add_definition("example.wide", [](const std::string& instance, const Config&) {
        Module module(instance);
        module.add_method<double, Integer>("fn", {"x"}, [](Integer) { return 1.0; },
                                           contract("example.fn.v1"));
        return module;
    });
    AssemblyDefinition signed_arity;
    signed_arity.add_instance("example.consumer", "consumer");
    signed_arity.add_instance("example.wide", "provider");
    signed_arity.connect({"consumer", "fn"}, {"provider", "fn"});
    Engine signature = signed_arity.instantiate(directory);
    failure(ErrorCode::type_mismatch, {"consumer", "fn"}, [&] { signature.seal(); });

    // 跨作用域接线是无效声明，由引擎目标定位报告。
    AssemblyDefinition crossing;
    crossing.add_scope("baseline");
    crossing.add_instance(kStimulus, "input", Config::object({{"value", Config::integer(0)}}));
    crossing.connect({"baseline/model", "input"}, {"input", "value"});
    const Diagnostic cross = failure(ErrorCode::invalid_declaration, {"baseline/model", "input"}, [&] {
        crossing.instantiate(directory);
    });
    CHECK(cross.path == "/connections/0");

    AssemblyDefinition nested;
    nested.add_scope("group");
    nested.add_instance(kStimulus, "dut", Config::object({{"value", Config::integer(0)}}), "group");
    nested.connect({"model/x", "value"}, {"dut", "value"}, "group");
    const Diagnostic inner = failure(ErrorCode::invalid_declaration, {"group/model/x", "value"}, [&] {
        nested.instantiate(directory);
    });
    CHECK(inner.path == "/scopes/0/connections/0");
}

void factory_target() {
    ModuleFactoryDirectory directory;
    directory.add_definition("broken", [](const std::string& name, const Config& config) -> Module {
        const auto duplicate = [](Module& module) {
            module.add_value<Integer>("v", [] { return Integer{0}; });
            module.add_value<Integer>("v", [] { return Integer{1}; });
        };
        Module root(name);
        if (config.string() == "root") {
            duplicate(root);
        } else if (config.string() == "deep") {
            Module branch("branch");
            Module leaf("leaf");
            duplicate(leaf);  // 尚未加入 branch，只有局部位置可知。
            branch.add(std::move(leaf));
            root.add(std::move(branch));
        } else {
            Module child(config.string());  // 可与根实例同名。
            duplicate(child);
            root.add(std::move(child));
        }
        return root;
    });

    for (const std::string mode : {"root", "child", "dut", "deep"}) {
        const std::string local = mode == "root" ? "dut" : mode == "deep" ? "leaf" : mode;
        const auto direct = failure(ErrorCode::duplicate_symbol, {"dut", ""}, [&] {
            directory.create("broken", "dut", Config::string(mode));
        });
        CHECK(direct.cause != nullptr);
        CHECK(direct.cause->target == Reference{local, "v"});
        CHECK(direct.cause->code == ErrorCode::duplicate_symbol);
        CHECK(render_text(direct.cause->text) == "Duplicate public symbol");
        CHECK(direct.source.empty());

        for (bool nested : {false, true}) {
            AssemblyDefinition definition;
            if (nested) definition.add_scope("group");
            definition.add_instance("broken", "dut", Config::string(mode), nested ? "group" : "");
            const auto error = failure(ErrorCode::duplicate_symbol, {nested ? "group/dut" : "dut", ""}, [&] {
                AssemblyDefinition::parse(definition.to_json(), "factory.json").instantiate(directory);
            });
            CHECK(error.source == "factory.json");
            CHECK(error.path == (nested ? "/scopes/0/instances/0" : "/instances/0"));
            CHECK(error.cause != nullptr);
            CHECK(error.cause->target == Reference{local, "v"});
            CHECK(render_text(error.cause->text) == render_text(direct.cause->text));
            CHECK(error.cause->text.key() == direct.cause->text.key());
            CHECK(error.cause->source.empty());
            CHECK(error.cause->path.empty());
            const std::string rendered = EngineError(error).what();
            CHECK(rendered.find("factory.json") != std::string::npos);
            CHECK(rendered.find("Duplicate public symbol") != std::string::npos);
            CHECK(rendered.find(local + "/v") != std::string::npos);
        }
    }

    // 完成构造的树在检查阶段仍使用完整内部路径，不降级为工厂实例定位。
    directory.add_definition("late", [](const std::string& name, const Config&) {
        Module root(name);
        Module child("child");
        child.require_value<Integer>("input", "scalar");
        root.add(std::move(child));
        return root;
    });
    AssemblyDefinition definition;
    definition.add_scope("group");
    definition.add_instance("late", "dut", Config{}, "group");
    auto engine = AssemblyDefinition::parse(definition.to_json(), "late.json").instantiate(directory);
    const auto diagnostics = engine.check();
    CHECK(diagnostics.size() == 1);
    CHECK(diagnostics[0].target == Reference{"group/dut/child", "input"});
    CHECK(diagnostics[0].source == "late.json");
    CHECK(diagnostics[0].path == "/scopes/0/instances/0");
    CHECK(!diagnostics[0].cause);
}

void factory_causes() {
    ModuleFactoryDirectory directory;
    directory.add_definition("inner", [](const std::string&, const Config&) -> Module {
        throw EngineError({ErrorCode::invalid_config, {"leaf", "value"},
                           "Invalid module parameter", "module.cfg", "/tuning"});
    });
    directory.add_definition("outer", [&directory](const std::string&, const Config&) {
        return directory.create("inner", "branch", Config{});
    });
    const Diagnostic saved = [&] {
        AssemblyDefinition definition;
        definition.add_scope("group");
        definition.add_instance("outer", "dut", Config{}, "group");
        return failure(ErrorCode::invalid_config, {"group/dut", ""}, [&] {
            AssemblyDefinition::parse(definition.to_json(), "outer.json").instantiate(directory);
        });
    }();
    CHECK(saved.source == "outer.json");
    CHECK(saved.path == "/scopes/0/instances/0");
    CHECK(saved.cause != nullptr);
    CHECK(saved.cause->target == Reference{"branch", ""});
    CHECK(saved.cause->cause != nullptr);
    const auto& original = *saved.cause->cause;
    CHECK(original.target == Reference{"leaf", "value"});
    CHECK(original.code == ErrorCode::invalid_config);
    CHECK(original.source == "module.cfg");
    CHECK(original.path == "/tuning");
    CHECK(render_text(original.text) == "Invalid module parameter");
    CHECK(!original.cause);
    const std::string rendered = EngineError(saved).what();
    CHECK(rendered.find("group/dut") != std::string::npos);
    CHECK(rendered.find("branch") != std::string::npos);
    CHECK(rendered.find("module.cfg /tuning leaf/value") != std::string::npos);

    // 目录查找和返回名校验是工厂边界自身的错误，没有虚构的内部原因。
    CHECK(!failure(ErrorCode::unknown_definition, {"dut", ""}, [&] {
        directory.create("absent", "dut", Config{});
    }).cause);
    directory.add_definition("renamed", [](const std::string&, const Config&) { return Module("other"); });
    CHECK(!failure(ErrorCode::invalid_config, {"dut", ""}, [&] {
        directory.create("renamed", "dut", Config{});
    }).cause);
}

void rollback() {
    ModuleFactoryDirectory directory = default_factories();
    std::vector<std::weak_ptr<Integer>> created;
    directory.add_definition("example.tracked", [&created](const std::string& instance, const Config& config) {
        auto state = std::make_shared<Integer>(config.integer());
        created.push_back(state);
        Module module(instance);
        module.add_value<Integer>("value", [state] { return *state; }, {}, "example.scalar.v1");
        return module;
    });

    AssemblyDefinition good;
    good.add_instance("example.tracked", "keeper", Config::integer(7));
    Engine keeper = good.instantiate(directory);
    keeper.seal();
    const auto keeper_value = keeper.bind_value<Integer>({"keeper", "value"});
    CHECK(keeper_value.read() == 7);
    CHECK(created.size() == 1);

    // 后续定义未知：临时构造及其状态释放，已加载实例不受影响。
    AssemblyDefinition failing;
    failing.add_instance("example.tracked", "temporary", Config::integer(1));
    failing.add_instance("example.empty", "absent");
    failure(ErrorCode::unknown_definition, {"absent", ""}, [&] { failing.instantiate(directory); });
    CHECK(created.size() == 2);
    CHECK(created[1].expired());
    CHECK(keeper_value.read() == 7);

    // 装配阶段失败同样不发布部分实例。
    AssemblyDefinition duplicated;
    duplicated.add_instance("example.tracked", "dut", Config::integer(2));
    duplicated.add_instance("example.tracked", "dut", Config::integer(3));
    failure(ErrorCode::duplicate_module, {"dut", ""}, [&] { duplicated.instantiate(directory); });
    CHECK(created.size() == 4);
    CHECK(created[2].expired());
    CHECK(created[3].expired());
    CHECK(keeper_value.read() == 7);

    // 工厂异常转换为配置交接失败。
    directory.add_definition("example.broken", [](const std::string&, const Config&) -> Module {
        throw std::runtime_error("factory failure");
    });
    AssemblyDefinition broken;
    broken.add_instance("example.broken", "broken");
    const Diagnostic diagnostic = failure(ErrorCode::invalid_config, {"broken", ""}, [&] {
        broken.instantiate(directory);
    });
    CHECK(diagnostic.path == "/instances/0/config");
    CHECK(render_diagnostic(diagnostic).find("factory failure") != std::string::npos);
}

void file() {
    ModuleFactoryDirectory directory = default_factories();
    TempDirectory temp("ascend-assembly-record-test");
    const std::string path = (temp.path() / "record.json").string();
    AssemblyDefinition definition = assembly_definition();
    definition.save(path);
    AssemblyDefinition loaded = AssemblyDefinition::load(path);
    CHECK(loaded.source() == path);
    CHECK(loaded.to_json() == definition.to_json());
    Engine engine = loaded.instantiate(directory);
    CHECK(equals(drive(engine), {{4, 6}, {6, 10}, {8, 12}}));

    // 文件不存在与读取中途失败都保持 I/O 故障分类，不送入 JSON 解析。
    const std::string absent = (temp.path() / "absent.json").string();
    const Diagnostic missing = failure(ErrorCode::io_failure, {absent, ""}, [&] {
        AssemblyDefinition::load(absent);
    });
    CHECK(missing.source == absent);
    const std::string directory_path = temp.path().string();
    const Diagnostic unreadable = failure(ErrorCode::io_failure, {directory_path, ""}, [&] {
        AssemblyDefinition::load(directory_path);
    });
    CHECK(unreadable.source == directory_path);

    // 写入失败：目标父目录不存在。
    const std::string unwritable = (temp.path() / "missing" / "record.json").string();
    failure(ErrorCode::io_failure, {unwritable, ""}, [&] { definition.save(unwritable); });
}

void diagnostic_locations() {
    ModuleFactoryDirectory directory;
    directory.add_definition("consumer", [](const std::string& name, const Config&) {
        Module module(name);
        module.require_value<Integer>("input", "scalar");
        return module;
    });
    directory.add_definition("provider", [](const std::string& name, const Config&) {
        Module module(name);
        module.add_value<Integer>("value", [] { return Integer{1}; }, {}, "other");
        return module;
    });
    // 定义销毁和引擎移动后，延迟检查仍保有文件来源。
    Engine engine = [&] {
        auto definition = AssemblyDefinition::parse(
            R"({"format":"ascend.assembly","version":1,"instances":[)"
            R"({"definition":"consumer","name":"dut"}],"connections":[)"
            R"({"requirement":{"module":"dut","symbol":"input"},)"
            R"("provider":{"module":"missing","symbol":"value"}}]})", "broken.json");
        return definition.instantiate(directory);
    }();
    Engine moved = std::move(engine);
    const auto diagnostics = moved.check();
    CHECK(diagnostics.size() == 1);
    CHECK(diagnostics[0].source == "broken.json");
    CHECK(diagnostics[0].path == "/connections/0");
    const auto sealed = failure(ErrorCode::missing_symbol, {"dut", "input"}, [&] { moved.seal(); });
    CHECK(sealed.source == diagnostics[0].source);
    CHECK(sealed.path == diagnostics[0].path);

    // 宿主修正接线后，不沿用已经删除的文件接线位置。
    moved.disconnect({"dut", "input"});
    CHECK(moved.check()[0].path == "/instances/0");
    moved.add(directory.create("provider", "source", Config{}));
    moved.connect({"dut", "input"}, {"source", "value"});
    CHECK(moved.check()[0].code == ErrorCode::contract_mismatch);
    CHECK(moved.check()[0].path != "/connections/0");

    AssemblyDefinition nested;
    nested.add_scope("outer");
    nested.add_scope("inner", "outer");
    nested.add_instance("consumer", "dut", Config{}, "outer/inner");
    nested.add_instance("provider", "source", Config{}, "outer/inner");
    nested.connect({"dut", "input"}, {"source", "value"}, "outer/inner");
    // 同名需求和导出分别定位，不能仅用 Reference 映射两种错误。
    nested.export_symbol("input", {"dut", "missing"}, "outer/inner");
    auto loaded = AssemblyDefinition::parse(nested.to_json(), "nested.json");
    Engine inner = loaded.instantiate(directory);
    CHECK(inner.check().size() == 2);
    for (const auto& diagnostic : inner.check()) {
        CHECK(diagnostic.source == "nested.json");
        if (diagnostic.code == ErrorCode::contract_mismatch) {
            CHECK(diagnostic.path == "/scopes/0/scopes/0/connections/0");
        } else {
            CHECK(diagnostic.code == ErrorCode::missing_symbol);
            CHECK(diagnostic.path == "/scopes/0/scopes/0/exports/0");
        }
    }

    AssemblyDefinition forwarded;
    forwarded.add_scope("group");
    forwarded.add_instance("consumer", "dut", Config{}, "group");
    forwarded.forward_inherited("input", {"dut", "input"}, "group");
    forwarded.export_symbol("input", {"dut", "missing"}, "group");
    auto forwarded_engine = AssemblyDefinition::parse(forwarded.to_json(), "forward.json").instantiate(directory);
    CHECK(forwarded_engine.check().size() == 2);
    for (const auto& diagnostic : forwarded_engine.check()) {
        CHECK(diagnostic.target == Reference{"group", "input"});
        CHECK(diagnostic.source == "forward.json");
        CHECK(diagnostic.path == (diagnostic.code == ErrorCode::unconnected_requirement
                                      ? "/scopes/0/forwards/0" : "/scopes/0/exports/0"));
    }
}

void forward_transaction() {
    Module parent("parent");
    Module child("child");
    child.require_value<Integer>("input", "scalar");
    parent.add(std::move(child));
    parent.forward_inherited("first", {"child", "input"});
    failure(ErrorCode::duplicate_connection, {"child", "input"}, [&] {
        parent.forward_inherited("second", {"child", "input"});
    });
    Engine engine;
    engine.add(std::move(parent));
    CHECK(engine.requirements().size() == 1);
    CHECK(engine.requirements()[0].reference == Reference{"parent", "first"});
    Module provider("provider");
    provider.add_value<Integer>("value", [] { return Integer{1}; }, {}, "scalar");
    engine.add(std::move(provider));
    engine.connect({"parent", "first"}, {"provider", "value"});
    CHECK(engine.check().empty());
    engine.seal();

    Module declared("declared");
    declared.require_value<Integer>("existing", "scalar");
    Module consumer("consumer");
    consumer.require_value<Integer>("input", "scalar");
    declared.add(std::move(consumer));
    declared.forward("existing", {"consumer", "input"});
    failure(ErrorCode::duplicate_connection, {"consumer", "input"}, [&] {
        declared.forward_inherited("existing", {"consumer", "input"});
    });
    Engine preserved;
    preserved.add(std::move(declared));
    CHECK(preserved.requirements().size() == 1);
    CHECK(preserved.requirements()[0].reference == Reference{"declared", "existing"});
}

void json_depth() {
    // JSON 容器最多 128 层；根、instances 数组及实例对象占三层。
    const auto text = [](std::size_t arrays) {
        return std::string(R"({"format":"ascend.assembly","version":1,"instances":[)"
                           R"({"definition":"echo","name":"e","config":)") +
               std::string(arrays, '[') + "0" + std::string(arrays, ']') + "}]}";
    };
    const auto boundary = AssemblyDefinition::parse(text(125), "depth.json");
    CHECK(AssemblyDefinition::parse(boundary.to_json()).to_json() == boundary.to_json());
    const auto deep = failure(ErrorCode::invalid_json, {}, [&] {
        AssemblyDefinition::parse(text(126), "depth.json");
    });
    CHECK(deep.source == "depth.json");
    CHECK(deep.path.find("/instances/0/config") == 0);

    Config config = Config::integer(0);
    for (int i = 0; i < 126; ++i) {
        std::vector<Config> elements;
        elements.push_back(std::move(config));
        config = Config::array(std::move(elements));
    }
    AssemblyDefinition built;
    built.add_instance("echo", "e", std::move(config));
    failure(ErrorCode::invalid_json, {}, [&] { built.to_json(); });

    AssemblyDefinition scopes;
    std::string scope;
    for (int i = 0; i < 70; ++i) {
        scopes.add_scope("g", scope);
        scope += scope.empty() ? "g" : "/g";
    }
    failure(ErrorCode::invalid_json, {}, [&] { scopes.to_json(); });
}

void utf8_serialization() {
    TempDirectory temp("ascend-utf8-test");
    const std::string path = (temp.path() / "record.json").string();
    const std::string original = AssemblyDefinition().to_json() + '\n';
    AssemblyDefinition().save(path);
    const std::vector<std::string> invalid = {
        std::string("\xFF", 1), std::string("\xC0\xAF", 2), std::string("\xE2\x82", 2),
        std::string("\xED\xA0\x80", 3), std::string("\xF4\x90\x80\x80", 4),
    };
    for (const auto& bytes : invalid) {
        failure(ErrorCode::invalid_json, {}, [&] {
            AssemblyDefinition::parse(
                std::string(R"({"format":"ascend.assembly","version":1,"instances":[)"
                            R"({"definition":"echo","name":"e","config":")") + bytes + "\"}]}");
        });
        for (bool key : {false, true}) {
            AssemblyDefinition definition;
            definition.add_instance("echo", "e", key ? Config::object({{bytes, Config{}}}) : Config::string(bytes));
            const auto diagnostic = failure(ErrorCode::invalid_json, {}, [&] { definition.save(path); });
            CHECK(diagnostic.source == path);
            CHECK(diagnostic.path.find("/instances/0/config") == 0);
            std::ifstream stream(path, std::ios::binary);
            const std::string actual((std::istreambuf_iterator<char>(stream)), std::istreambuf_iterator<char>());
            CHECK(actual == original);  // 序列化失败不能截断已有记录。
        }
    }
    AssemblyDefinition valid;
    valid.add_instance("echo", "e", Config::string(std::string("\0\xC3\xA9\xF0\x9F\x98\x80", 7)));
    valid.save(path);
    CHECK(AssemblyDefinition::load(path).to_json() == valid.to_json());
}

void pointer_paths() {
    const auto error = failure(ErrorCode::invalid_json, {}, [&] {
        AssemblyDefinition::parse(
            R"({"format":"ascend.assembly","version":1,"instances":[)"
            R"({"definition":"echo","name":"e","config":{"a/b~c":9223372036854775808}}]})", "pointer.json");
    });
    CHECK(error.path == "/instances/0/config/a~1b~0c");
    CHECK(failure(ErrorCode::invalid_assembly, {}, [] {
        AssemblyDefinition::parse(R"({"format":"ascend.assembly","version":1,"a/b~c":0})");
    }).path == "/a~1b~0c");
}
}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"factories", factories},       {"definition", definition},     {"definition_access", definition_access},
        {"roundtrip", roundtrip},
        {"config_change", config_change}, {"wiring", wiring},           {"independence", independence},
        {"editing_instance", editing_instance}, {"factory_merge", factory_merge},
        {"config_values", config_values}, {"record_errors", record_errors},
        {"factory_errors", factory_errors}, {"assembly_errors", assembly_errors},
        {"factory_target", factory_target}, {"factory_causes", factory_causes},
        {"builder_errors", builder_errors}, {"rollback", rollback}, {"file", file},
        {"diagnostic_locations", diagnostic_locations}, {"forward_transaction", forward_transaction},
        {"json_depth", json_depth}, {"utf8_serialization", utf8_serialization}, {"pointer_paths", pointer_paths},
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
