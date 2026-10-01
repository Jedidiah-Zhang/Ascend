#include <ascend/experiment.hpp>
#include <ascend/example/experiment_model.hpp>
#include <ascend/i18n.hpp>

#include <algorithm>
#include <any>
#include <array>
#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace {
using namespace ascend;
using Integer = std::int64_t;
using Tuple = std::array<Integer, 3>;

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
            throw std::runtime_error("unexpected diagnostic at " + diagnostic.target.module + '/' +
                                     diagnostic.target.symbol + ": " + render_diagnostic(diagnostic));
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

// 三变量确定性模型，供手工核对：
//   x' = x + a;  y' = x;  z' = y + z;  观测为 (x, y, z)
// plant 是容器（无自身状态），state 持有变量，update 计算下一步。
struct Values {
    Integer x = 0;
    Integer y = 0;
    Integer z = 0;
};

constexpr const char* kPlant = "test.plant";
constexpr const char* kStimulus = "test.stimulus";
constexpr const char* kPlantState = "test.plant.state.v1";
constexpr const char* kStimulusState = "test.stimulus.state.v1";

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

Module plant_with_state(const std::string& instance, const Config& config, std::string state_contract,
                        int declaration) {
    Values initial;
    if (!config.is_null()) initial = decode(config);
    auto values = std::make_shared<Values>(initial);

    Module model(instance);
    model.declare_stateless();
    model.require_value<Integer>("input", "test.scalar.v1");

    Module state("state");
    state.add_value<Integer>("x", [values] { return values->x; }, "x", "test.scalar.v1");
    state.add_value<Integer>("y", [values] { return values->y; }, "y", "test.scalar.v1");
    state.add_value<Integer>("z", [values] { return values->z; }, "z", "test.scalar.v1");
    state.add_method<void, Integer, Integer, Integer>(
        "write", {"x", "y", "z"},
        [values](Integer x, Integer y, Integer z) {
            values->x = x;
            values->y = y;
            values->z = z;
        },
        contract("test.write.v1"));
    if (declaration == 0) {
        state.add_state(std::move(state_contract), [values] { return encode(*values); },
                        [values](const Config& state_value) { *values = decode(state_value); });
    } else if (declaration == 1) {
        state.declare_stateless();
    }

    Module update("update");
    update.declare_stateless();
    const auto input = update.require_value<Integer>("input", "test.scalar.v1");
    const auto old_x = update.require_value<Integer>("x", "test.scalar.v1");
    const auto old_y = update.require_value<Integer>("y", "test.scalar.v1");
    const auto old_z = update.require_value<Integer>("z", "test.scalar.v1");
    const auto write = update.require_method<void, Integer, Integer, Integer>("write", "test.write.v1");
    update.add_method<void>("advance", {}, [input, old_x, old_y, old_z, write](const Context& context) {
        const Integer x = old_x.read(context);
        const Integer y = old_y.read(context);
        const Integer z = old_z.read(context);
        write(context, x + input.read(context), x, y + z);
    }, contract("test.advance.v1"));

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

Module plant(const std::string& instance, const Config& config) {
    return plant_with_state(instance, config, kPlantState, 0);
}

Module stimulus(const std::string& instance, const Config& config) {
    Integer initial = 0;
    if (!config.is_null()) {
        if (config.kind() != Config::Kind::integer) {
            throw std::invalid_argument("stimulus config must be an integer");
        }
        initial = config.integer();
    }
    Module module(instance);
    auto value = std::make_shared<Integer>(initial);
    module.add_value<Integer>("value", [value] { return *value; }, "Input value", "test.scalar.v1");
    module.add_method<void, Integer>("drive", {"next"}, [value](Integer next) { *value = next; },
                                     contract("test.scalar-drive.v1"));
    module.add_state(kStimulusState, [value] { return Config::integer(*value); },
                     [value](const Config& state) {
                         if (state.kind() != Config::Kind::integer) {
                             throw std::invalid_argument("input state must be an integer");
                         }
                         *value = state.integer();
                     });
    return module;
}

ModuleFactoryDirectory factories() {
    ModuleFactoryDirectory result;
    result.add_definition(kPlant, plant);
    result.add_definition(kStimulus, stimulus);
    return result;
}

AssemblyDefinition definition(Integer x = 0, Integer y = 0, Integer z = 0, Integer a = 0) {
    AssemblyDefinition result;
    result.add_instance(kPlant, "plant", encode({x, y, z}));
    result.add_instance(kStimulus, "input", Config::integer(a));
    result.connect({"plant", "input"}, {"input", "value"});
    return result;
}

ExperimentSpec spec() {
    return ExperimentSpec{{"plant", "advance"},
                          {{"a", {"input", "drive"}}},
                          {{"x", {"plant", "x"}}, {"y", {"plant", "y"}}, {"z", {"plant", "z"}}}};
}

Engine build_engine(Integer x = 0, Integer y = 0, Integer z = 0, Integer a = 0) {
    Engine engine;
    engine.add(plant("plant", encode({x, y, z})));
    engine.add(stimulus("input", Config::integer(a)));
    engine.connect({"plant", "input"}, {"input", "value"});
    if (!engine.check().empty()) throw std::runtime_error("unexpected diagnostics");
    engine.seal();
    return engine;
}

const ModuleState& module_state(const StateSnapshot& snapshot, const std::string& path) {
    for (const auto& module : snapshot.modules) {
        if (module.path == path) return module;
    }
    throw std::runtime_error("module state not found: " + path);
}

ModuleState& module_state(StateSnapshot& snapshot, const std::string& path) {
    for (auto& module : snapshot.modules) {
        if (module.path == path) return module;
    }
    throw std::runtime_error("module state not found: " + path);
}

Integer observed(const Sample& sample, const char* name) {
    return std::any_cast<Integer>(sample.observations.at(name));
}

Tuple observed(const Sample& sample) { return {observed(sample, "x"), observed(sample, "y"), observed(sample, "z")}; }

Integer truth(const StateSnapshot& snapshot, const char* field) {
    return module_state(snapshot, "plant/state").state.find(field)->integer();
}

void advance(ExperimentRun& run, Integer input) {
    run.drive("a", {std::any(input)});
    run.step();
}

// 手工核对序列：a = 1 恒定。(0,0,0) 起步，干预在逻辑帧 2 施加 x := 10。
constexpr std::array<Tuple, 6> kControl = {Tuple{0, 0, 0}, Tuple{1, 0, 0}, Tuple{2, 1, 0},
                                          Tuple{3, 2, 1}, Tuple{4, 3, 3}, Tuple{5, 4, 6}};
constexpr std::array<Tuple, 6> kTreated = {Tuple{0, 0, 0}, Tuple{1, 0, 0}, Tuple{10, 1, 0},
                                          Tuple{11, 10, 1}, Tuple{12, 11, 11}, Tuple{13, 12, 22}};

void state_declarations() {
    Module invalid("m");
    failure(ErrorCode::invalid_declaration, {"m", {}}, [&] {
        invalid.add_state("", [] { return Config{}; }, [](const Config&) {});
    });
    failure(ErrorCode::invalid_declaration, {"m", {}}, [&] {
        invalid.add_state("test.state.v1", {}, [](const Config&) {});
    });
    failure(ErrorCode::invalid_declaration, {"m", {}}, [&] {
        invalid.add_state("test.state.v1", [] { return Config{}; }, {});
    });
    Module both("m");
    both.declare_stateless();
    failure(ErrorCode::invalid_declaration, {"m", {}}, [&] {
        both.add_state("test.state.v1", [] { return Config{}; }, [](const Config&) {});
    });
    failure(ErrorCode::invalid_declaration, {"m", {}}, [&] { both.declare_stateless(); });

    // 未封闭不能交接状态。
    Engine open;
    Module declared("a");
    declared.add_state("test.state.v1", [] { return Config::integer(1); }, [](const Config&) {});
    open.add(std::move(declared));
    failure(ErrorCode::registration_open, {}, [&] { open.capture_state(); });
    failure(ErrorCode::registration_open, {}, [&] { open.restore_state(StateSnapshot{}); });

    // 任一模块未声明状态能力时，完整捕获失败并指出模块。
    Engine incomplete;
    Module fine("a");
    fine.add_state("test.state.v1", [] { return Config::integer(1); }, [](const Config&) {});
    incomplete.add(std::move(fine));
    incomplete.add(Module("b"));
    CHECK(incomplete.check().empty());
    incomplete.seal();
    failure(ErrorCode::state_incomplete, {"b", {}}, [&] { incomplete.capture_state(); });

    // 明确无状态后可以捕获，条目按模块路径排序并区分有状态与无状态。
    Engine complete;
    Module stateful("a");
    stateful.add_state("test.state.v1",
                       [value = Integer{3}] { return Config::integer(value); }, [](const Config&) {});
    complete.add(std::move(stateful));
    Module plain("b");
    plain.declare_stateless();
    complete.add(std::move(plain));
    complete.seal();
    const auto snapshot = complete.capture_state();
    CHECK(snapshot.modules.size() == 2);
    CHECK(snapshot.modules[0].path == "a");
    CHECK(!snapshot.modules[0].stateless);
    CHECK(snapshot.modules[0].contract == "test.state.v1");
    CHECK(snapshot.modules[0].state == Config::integer(3));
    CHECK(snapshot.modules[1].path == "b");
    CHECK(snapshot.modules[1].stateless);
    CHECK(snapshot.modules[1].state.is_null());

    // 捕获回调失败按 invalid_state 报告并保留作者原文。
    Engine throwing;
    Module failing("a");
    failing.add_state("test.state.v1",
                      []() -> Config { throw std::runtime_error("capture failed"); },
                      [](const Config&) {});
    throwing.add(std::move(failing));
    throwing.seal();
    const auto diagnostic = failure(ErrorCode::invalid_state, {"a", {}},
                                    [&] { throwing.capture_state(); });
    CHECK(render_diagnostic(diagnostic).find("capture failed") != std::string::npos);
}

void capture_restore() {
    Engine first = build_engine(0, 0, 0, 0);
    const auto step = first.bind_method<void>({"plant", "advance"});
    const auto drive = first.bind_method<void, Integer>({"input", "drive"});
    for (int index = 0; index < 3; ++index) {
        drive(1);
        step();
    }
    CHECK(std::any_cast<Integer>(first.read({"plant", "x"})) == 3);
    const auto snapshot = first.capture_state();

    Engine second = build_engine(0, 0, 0, 0);
    second.restore_state(snapshot);
    CHECK(std::any_cast<Integer>(second.read({"plant", "x"})) == 3);
    CHECK(std::any_cast<Integer>(second.read({"plant", "y"})) == 2);
    CHECK(std::any_cast<Integer>(second.read({"plant", "z"})) == 1);

    // 相同后续输入下，恢复实例与源实例逐逻辑帧一致。
    const auto step_second = second.bind_method<void>({"plant", "advance"});
    const auto drive_second = second.bind_method<void, Integer>({"input", "drive"});
    for (int index = 0; index < 2; ++index) {
        drive(1);
        step();
        drive_second(1);
        step_second();
        for (const char* name : {"x", "y", "z"}) {
            CHECK(std::any_cast<Integer>(first.read({"plant", name})) ==
                  std::any_cast<Integer>(second.read({"plant", name})));
        }
    }
    CHECK(std::any_cast<Integer>(second.read({"plant", "x"})) == 5);
}

void deep_copy() {
    ExperimentRun run(definition(), factories(), spec());
    advance(run, 1);
    const auto checkpoint = run.checkpoint();
    const auto copy = checkpoint;

    // 运行继续变化，已有检查点保持不变。
    advance(run, 1);
    CHECK(run.frame() == 2);
    CHECK(truth(checkpoint.truth, "x") == 1);
    CHECK(truth(copy.truth, "x") == 1);
    CHECK(module_state(checkpoint.truth, "plant/state").state ==
          module_state(copy.truth, "plant/state").state);

    // 修改副本并恢复，不影响源检查点与源运行。
    auto modified = checkpoint;
    module_state(modified.truth, "plant/state").state =
        encode({50, 60, 70});
    ExperimentRun restored(definition(), factories(), spec());
    restored.restore(modified);
    CHECK(observed(restored.sample()) == Tuple{50, 60, 70});
    CHECK(truth(checkpoint.truth, "x") == 1);
    CHECK(truth(copy.truth, "x") == 1);
    CHECK(observed(run.sample()) == Tuple{2, 1, 0});
}

void branch_isolation() {
    const auto environment = definition();
    const auto directory = factories();
    ExperimentRun original(environment, directory, spec(), "original");
    advance(original, 1);
    advance(original, 1);
    const auto origin = original.checkpoint();
    CHECK(truth(origin.truth, "x") == 2);

    ExperimentRun control(environment, directory, spec(), "control");
    control.restore(origin);
    ExperimentRun treated(environment, directory, spec(), "treated");
    treated.restore(apply_interventions(
        origin, {{"plant/state", "x", Config::integer(10)}}));

    for (int frame = 3; frame <= 5; ++frame) {
        advance(control, 1);
        advance(treated, 1);
        CHECK(observed(control.sample()) == kControl[frame]);
        CHECK(observed(treated.sample()) == kTreated[frame]);
    }

    // 原始运行继续推进，结果与对照分支一致，不受两个分支影响。
    for (int frame = 3; frame <= 5; ++frame) advance(original, 1);
    CHECK(observed(original.sample()) == kControl[5]);

    // 同一检查点恢复两份无干预实例，逐步轨迹一致。
    ExperimentRun mirror(environment, directory, spec());
    mirror.restore(origin);
    for (int frame = 3; frame <= 5; ++frame) {
        advance(mirror, 1);
        CHECK(observed(mirror.sample()) == kControl[frame]);
    }

    // 交换两个分支的推进先后，各自结果不变；分支之间状态互不影响。
    ExperimentRun control_swapped(environment, directory, spec());
    control_swapped.restore(origin);
    ExperimentRun treated_swapped(environment, directory, spec());
    treated_swapped.restore(apply_interventions(
        origin, {{"plant/state", "x", Config::integer(10)}}));
    for (int frame = 3; frame <= 5; ++frame) {
        advance(treated_swapped, 1);
        advance(control_swapped, 1);
        CHECK(observed(treated_swapped.sample()) == kTreated[frame]);
        CHECK(observed(control_swapped.sample()) == kControl[frame]);
    }
    CHECK(observed(control.sample()) == kControl[5]);
    CHECK(observed(treated.sample()) == kTreated[5]);
}

void sampling_stability() {
    const auto environment = definition();
    const auto directory = factories();
    ExperimentRun source(environment, directory, spec());
    advance(source, 1);
    advance(source, 1);
    const auto origin = source.checkpoint();

    // 高频采样并重复读取真值与观测；低频运行只在共同采样点采样。
    ExperimentRun intensive(environment, directory, spec());
    intensive.restore(origin);
    ExperimentRun sparse(environment, directory, spec());
    sparse.restore(origin);
    for (int frame = 3; frame <= 5; ++frame) {
        advance(intensive, 1);
        for (int repeat = 0; repeat < 3; ++repeat) {
            CHECK(observed(intensive.sample()) == kControl[frame]);
        }
        advance(sparse, 1);
    }

    // 共同采样点上完整真值与观测一致，读取次数与采样频率不改变轨迹。
    const auto intensive_final = intensive.sample();
    const auto sparse_final = sparse.sample();
    CHECK(observed(intensive_final) == kControl[5]);
    CHECK(observed(sparse_final) == kControl[5]);
    for (const auto& module : intensive_final.truth.modules) {
        CHECK(module_state(sparse_final.truth, module.path).state == module.state);
    }
}

void lifetime() {
    const auto environment = definition();
    const auto directory = factories();
    Checkpoint shared;
    {
        ExperimentRun source(environment, directory, spec(), "source");
        advance(source, 1);
        advance(source, 1);
        shared = source.checkpoint();
    }  // 源运行销毁后，检查点仍可独立恢复。

    ExperimentRun surviving(environment, directory, spec(), "surviving");
    surviving.restore(shared);
    {
        ExperimentRun branch(environment, directory, spec(), "branch");
        branch.restore(shared);
        advance(branch, 1);
        CHECK(observed(branch.sample()) == kControl[3]);
    }  // 中间分支销毁不影响其他对象。

    advance(surviving, 1);
    CHECK(observed(surviving.sample()) == kControl[3]);
    const Sample kept = surviving.sample();
    for (int frame = 4; frame <= 5; ++frame) advance(surviving, 1);
    CHECK(observed(surviving.sample()) == kControl[5]);
    CHECK(observed(kept) == kControl[3]);

    // 运行销毁后，已取得的采样结果仍可读。
    Sample survived;
    {
        ExperimentRun transient(environment, directory, spec(), "transient");
        transient.restore(shared);
        advance(transient, 1);
        survived = transient.sample();
    }
    CHECK(observed(survived) == kControl[3]);
    CHECK(truth(survived.truth, "x") == 3);
}

void intervention_propagation() {
    const auto environment = definition();
    const auto directory = factories();
    ExperimentRun original(environment, directory, spec());
    advance(original, 1);
    advance(original, 1);
    const auto origin = original.checkpoint();
    CHECK(observed(original.sample()) == kControl[2]);

    ExperimentRun control(environment, directory, spec());
    control.restore(origin);
    ExperimentRun treated(environment, directory, spec());
    treated.restore(apply_interventions(
        origin, {{"plant/state", "x", Config::integer(10)}}));

    // 直接结果在预测起点可见：x 已改变，y、z 尚未传播。
    const auto start = treated.sample();
    CHECK(start.frame == 2);
    CHECK(observed(start) == kTreated[2]);
    CHECK(observed(control.sample()) == kControl[2]);

    // 下游传播逐逻辑帧出现：y 在下一步变化，z 再下一步变化。
    advance(control, 1);
    advance(treated, 1);
    CHECK(observed(control.sample()) == kControl[3]);
    CHECK(observed(treated.sample()) == kTreated[3]);
    advance(control, 1);
    advance(treated, 1);
    CHECK(observed(control.sample()) == kControl[4]);
    CHECK(observed(treated.sample()) == kTreated[4]);
    advance(control, 1);
    advance(treated, 1);
    CHECK(observed(control.sample()) == kControl[5]);
    CHECK(observed(treated.sample()) == kTreated[5]);
}

void intervention_errors() {
    ExperimentRun run(definition(1, 2, 3, 0), factories(), spec());
    const auto origin = run.checkpoint();
    const auto rejected = [&](std::string module, std::string field, Config value) {
        return failure(ErrorCode::invalid_intervention, {module, {}}, [&] {
            apply_interventions(origin, {{std::move(module), std::move(field), std::move(value)}});
        });
    };
    rejected("missing", "x", Config::integer(1));
    rejected("plant", "x", Config::integer(1));          // 容器模块无状态
    rejected("plant/state", "w", Config::integer(1));    // 字段不存在
    rejected("plant/state", "x/", Config::integer(1));   // 路径段为空
    rejected("plant/state", "x//y", Config::integer(1));
    rejected("plant/state", "x/y", Config::integer(1));  // 中间值不是对象

    // 空字段替换整个状态对象；同一路径多次修改按顺序后者覆盖。
    const auto whole = apply_interventions(
        origin, {{"plant/state", "", encode({7, 8, 9})}});
    ExperimentRun restored(definition(), factories(), spec());
    restored.restore(whole);
    CHECK(observed(restored.sample()) == Tuple{7, 8, 9});
    const auto twice = apply_interventions(
        origin, {{"plant/state", "x", Config::integer(5)}, {"plant/state", "x", Config::integer(7)}});
    restored.restore(twice);
    CHECK(observed(restored.sample()) == Tuple{7, 2, 3});

    // 原检查点与源运行不受干预影响。
    CHECK(truth(origin.truth, "x") == 1);
    CHECK(observed(run.sample()) == Tuple{1, 2, 3});
}

Module drone(const std::string& instance) {
    auto position = std::make_shared<std::pair<Integer, Integer>>(1, 2);
    auto tick = std::make_shared<Integer>(0);
    Module module(instance);
    module.add_value<Integer>("x", [position] { return position->first; });
    module.add_value<Integer>("tick", [tick] { return *tick; });
    module.add_method<void>("advance", {}, [position, tick] {
        ++position->first;
        ++*tick;
    });
    module.add_state(
        "test.drone.state.v1",
        [position, tick] {
            return Config::object(
                {{"position", Config::object({{"x", Config::integer(position->first)},
                                              {"y", Config::integer(position->second)}})},
                 {"tick", Config::integer(*tick)}});
        },
        [position, tick](const Config& state) {
            if (state.kind() != Config::Kind::object) throw std::invalid_argument("state must be an object");
            const Config* nested = state.find("position");
            if (!nested || nested->kind() != Config::Kind::object) {
                throw std::invalid_argument("position must be an object");
            }
            const Config* x = nested->find("x");
            const Config* y = nested->find("y");
            const Config* count = state.find("tick");
            if (!x || !y || !count || x->kind() != Config::Kind::integer ||
                y->kind() != Config::Kind::integer || count->kind() != Config::Kind::integer) {
                throw std::invalid_argument("pose state fields must be integers");
            }
            position->first = x->integer();
            position->second = y->integer();
            *tick = count->integer();
        });
    return module;
}

void nested_fields() {
    Engine engine;
    engine.add(drone("drone"));
    engine.seal();
    engine.call({"drone", "advance"}, {});
    const Checkpoint checkpoint{1, engine.capture_state()};

    const auto changed = apply_interventions(
        checkpoint, {{"drone", "position/x", Config::integer(7)}, {"drone", "tick", Config::integer(9)}});
    Engine restored;
    restored.add(drone("drone"));
    restored.seal();
    restored.restore_state(changed.truth);
    CHECK(std::any_cast<Integer>(restored.read({"drone", "x"})) == 7);
    CHECK(std::any_cast<Integer>(restored.read({"drone", "tick"})) == 9);

    // 模块恢复回调拒绝非法状态结构。
    auto broken = checkpoint;
    module_state(broken.truth, "drone").state = Config::integer(5);
    failure(ErrorCode::invalid_state, {"drone", {}}, [&] { restored.restore_state(broken.truth); });
}

Engine variant_engine(const std::string& state_contract, int declaration) {
    Engine engine;
    engine.add(plant_with_state("plant", encode({1, 2, 3}), state_contract, declaration));
    engine.add(stimulus("input", Config::integer(1)));
    engine.connect({"plant", "input"}, {"input", "value"});
    engine.seal();
    return engine;
}

void restore_mismatches() {
    ExperimentRun source(definition(1, 2, 3, 0), factories(), spec());
    const auto snapshot = source.checkpoint().truth;

    // 模块数量不同。
    Engine single;
    Module lone("lone");
    lone.declare_stateless();
    single.add(std::move(lone));
    single.seal();
    failure(ErrorCode::state_mismatch, {}, [&] { single.restore_state(snapshot); });

    // 模块集合不一致（同数量），诊断指向装配中缺失的模块。
    Engine renamed;
    Module alpha("alpha");
    alpha.declare_stateless();
    Module beta("beta");
    beta.declare_stateless();
    Module gamma("gamma");
    gamma.declare_stateless();
    Module omega("omega");
    omega.declare_stateless();
    renamed.add(std::move(alpha));
    renamed.add(std::move(beta));
    renamed.add(std::move(gamma));
    renamed.add(std::move(omega));
    renamed.seal();
    failure(ErrorCode::state_mismatch, {"alpha", {}}, [&] { renamed.restore_state(snapshot); });

    // 快照条目乱序仍然按集合恢复；重复路径与空路径被拒绝。
    StateSnapshot reversed;
    reversed.modules.assign(snapshot.modules.rbegin(), snapshot.modules.rend());
    Engine ordered = build_engine(0, 0, 0, 0);
    ordered.restore_state(reversed);
    CHECK(std::any_cast<Integer>(ordered.read({"plant", "x"})) == 1);
    StateSnapshot duplicated = snapshot;
    duplicated.modules.push_back(snapshot.modules.front());
    failure(ErrorCode::state_mismatch, {"input", {}},
            [&] { ordered.restore_state(duplicated); });
    StateSnapshot empty_path = snapshot;
    empty_path.modules.back().path.clear();
    failure(ErrorCode::state_mismatch, {}, [&] { ordered.restore_state(empty_path); });

    // 状态契约不匹配。
    Engine versioned = variant_engine("test.plant.state.v2", 0);
    failure(ErrorCode::state_mismatch, {"plant/state", {}},
            [&] { versioned.restore_state(snapshot); });

    // 无状态声明不一致。
    Engine stateless_state = variant_engine(kPlantState, 1);
    failure(ErrorCode::state_mismatch, {"plant/state", {}},
            [&] { stateless_state.restore_state(snapshot); });

    // 无状态条目必须携带空值。
    StateSnapshot stateless_value = snapshot;
    for (auto& module : stateless_value.modules) {
        if (module.stateless) {
            module.state = Config::integer(1);
            break;
        }
    }
    failure(ErrorCode::state_mismatch, {"plant", {}},
            [&] { ordered.restore_state(stateless_value); });

    // 目标模块未声明状态能力。
    Engine undeclared_state = variant_engine(kPlantState, 2);
    failure(ErrorCode::state_mismatch, {"plant/state", {}},
            [&] { undeclared_state.restore_state(snapshot); });

    // 模块恢复回调拒绝状态结构，作者原文保留在诊断中。
    auto broken = snapshot;
    module_state(broken, "plant/state").state = Config::integer(5);
    Engine target = build_engine(0, 0, 0, 0);
    const auto diagnostic = failure(ErrorCode::invalid_state, {"plant/state", {}},
                                    [&] { target.restore_state(broken); });
    CHECK(render_diagnostic(diagnostic).find("state must be an object") != std::string::npos);

    // 恢复中途失败不回滚已恢复的模块：input 已按快照写回，plant/state 才失败。
    Engine partial = build_engine(0, 0, 0, 7);
    failure(ErrorCode::invalid_state, {"plant/state", {}},
            [&] { partial.restore_state(broken); });
    CHECK(std::any_cast<Integer>(partial.read({"input", "value"})) == 0);

    // 恢复回调抛出的 EngineError 保留为 cause 链。
    Engine guarded;
    Module keeper("keeper");
    keeper.add_state(
        "test.keeper.state.v1", [] { return Config::integer(0); },
        [](const Config&) {
            throw EngineError({ErrorCode::invalid_config, {"keeper", {}}, "author rejection"});
        });
    guarded.add(std::move(keeper));
    guarded.seal();
    StateSnapshot keeper_state;
    keeper_state.modules.push_back({"keeper", "test.keeper.state.v1", false, Config::integer(0)});
    const auto guarded_failure = failure(ErrorCode::invalid_state, {"keeper", {}},
                                         [&] { guarded.restore_state(keeper_state); });
    CHECK(guarded_failure.cause != nullptr);
    CHECK(guarded_failure.cause->code == ErrorCode::invalid_config);
    CHECK(guarded_failure.cause->text.literal() == "author rejection");
}

void spec_errors() {
    const auto environment = definition();
    const auto directory = factories();
    const auto bad_spec = [&](ExperimentSpec candidate) {
        ExperimentRun run(environment, directory, std::move(candidate));
        (void)run;
    };
    ExperimentSpec empty_advance = spec();
    empty_advance.advance = {};
    failure(ErrorCode::invalid_declaration, {}, [&] { bad_spec(empty_advance); });
    ExperimentSpec missing_advance = spec();
    missing_advance.advance.symbol = "missing";
    failure(ErrorCode::missing_symbol, {"plant", "missing"}, [&] { bad_spec(missing_advance); });
    ExperimentSpec value_advance = spec();
    value_advance.advance = {"plant", "x"};
    failure(ErrorCode::wrong_kind, {"plant", "x"}, [&] { bad_spec(value_advance); });
    ExperimentSpec duplicate_input = spec();
    duplicate_input.inputs.push_back({"a", {"plant", "advance"}});
    failure(ErrorCode::invalid_declaration, {}, [&] { bad_spec(duplicate_input); });
    ExperimentSpec empty_input = spec();
    empty_input.inputs.push_back({"", {"plant", "advance"}});
    failure(ErrorCode::invalid_declaration, {}, [&] { bad_spec(empty_input); });
    ExperimentSpec empty_observation = spec();
    empty_observation.observations.push_back({"", {"plant", "x"}});
    failure(ErrorCode::invalid_declaration, {}, [&] { bad_spec(empty_observation); });

    ExperimentRun run(environment, directory, spec());
    failure(ErrorCode::invalid_declaration, {}, [&] { run.drive("missing"); });
    failure(ErrorCode::argument_count, {"input", "drive"}, [&] { run.drive("a"); });
    failure(ErrorCode::type_mismatch, {"input", "drive"},
            [&] { run.drive("a", {std::any(std::string("1"))}); });
    ExperimentSpec bad_observation = spec();
    bad_observation.observations.push_back({"o", {"plant", "missing"}});
    ExperimentRun observed_run(environment, directory, bad_observation);
    failure(ErrorCode::missing_symbol, {"plant", "missing"}, [&] { observed_run.sample(); });
    ExperimentSpec method_observation = spec();
    method_observation.observations.push_back({"o", {"plant", "advance"}});
    ExperimentRun method_run(environment, directory, method_observation);
    // 导出引用在诊断中解析到实际提供方位置。
    failure(ErrorCode::wrong_kind, {"plant/update", "advance"}, [&] { method_run.sample(); });

    // 规格校验先于装配实例化：非法规格不执行工厂代码。
    int factory_calls = 0;
    ModuleFactoryDirectory counting;
    counting.add_definition(kPlant, [&](const std::string& instance, const Config& config) {
        ++factory_calls;
        return plant(instance, config);
    });
    counting.add_definition(kStimulus, stimulus);
    failure(ErrorCode::invalid_declaration, {}, [&] {
        ExperimentRun run(environment, counting, empty_advance);
        (void)run;
    });
    CHECK(factory_calls == 0);
}

// 推进入口先写入再抛错：用于验证失败不回滚且逻辑帧不前进。
Module fragile(const std::string& instance, const Config& config) {
    Integer initial = 0;
    if (!config.is_null()) initial = config.integer();
    auto value = std::make_shared<Integer>(initial);
    Module module(instance);
    module.add_value<Integer>("x", [value] { return *value; }, "x", "test.scalar.v1");
    module.add_state("test.fragile.state.v1", [value] { return Config::integer(*value); },
                     [value](const Config& state) {
                         if (state.kind() != Config::Kind::integer) {
                             throw std::invalid_argument("state must be an integer");
                         }
                         *value = state.integer();
                     });
    module.add_method<void>("advance", {}, [value] {
        ++*value;
        if (*value >= 100) throw std::runtime_error("limit reached");
    }, contract("test.fragile.advance.v1"));
    return module;
}

void step_failure() {
    AssemblyDefinition environment;
    environment.add_instance("test.fragile", "counter", Config::integer(0));
    ModuleFactoryDirectory directory;
    directory.add_definition("test.fragile", fragile);
    const ExperimentSpec settings{{"counter", "advance"}, {}, {{"x", {"counter", "x"}}}};
    ExperimentRun run(environment, directory, settings);
    run.step();
    run.step();
    CHECK(observed(run.sample(), "x") == 2);
    const auto origin = run.checkpoint();
    const auto armed = apply_interventions(origin, {{"counter", "", Config::integer(99)}});
    run.restore(armed);

    // 失败时逻辑帧不前进，先写入的值保留，不从检查点自动回滚；失败进入轨迹记录。
    RunTrace trace{"failing", origin, {{"counter", "", Config::integer(99)}}, {}, {}, {}};
    const auto diagnostic = failure(ErrorCode::execution_failed, {"counter", "advance"}, [&] { run.step(); });
    trace.failures.push_back({run.frame(), diagnostic});
    CHECK(run.frame() == 2);
    CHECK(observed(run.sample(), "x") == 100);
    // 失败后仍可采样继续，成功步骤只进入采样、不追加失败记录。
    trace.samples.push_back(run.sample());
    CHECK(trace.failures.size() == 1);
    CHECK(trace.failures[0].frame == 2);
    CHECK(trace.samples.size() == 1);
    CHECK(trace.samples[0].frame == 2);

    // 恢复失败时逻辑帧同样保持不变；检查点逻辑帧必须非负。
    auto rejected = origin;
    module_state(rejected.truth, "counter").state = Config::object({});
    failure(ErrorCode::invalid_state, {"counter", {}}, [&] { run.restore(rejected); });
    CHECK(run.frame() == 2);
    auto negative = origin;
    negative.frame = -1;
    failure(ErrorCode::invalid_declaration, {}, [&] { run.restore(negative); });
    CHECK(run.frame() == 2);
    CHECK(observed(run.sample(), "x") == 100);

    // 从原检查点恢复，改走一条不再触发失败的分支。
    run.restore(origin);
    CHECK(run.frame() == 2);
    CHECK(observed(run.sample(), "x") == 2);
    run.restore(apply_interventions(origin, {{"counter", "", Config::integer(10)}}));
    run.step();
    CHECK(run.frame() == 3);
    CHECK(observed(run.sample(), "x") == 11);

    // 失败运行不影响其他实例。
    ExperimentRun other(environment, directory, settings);
    other.restore(origin);
    other.step();
    CHECK(observed(other.sample(), "x") == 3);
}

void trace_record() {
    const auto environment = definition();
    const auto directory = factories();
    ExperimentRun original(environment, directory, spec(), "original");
    advance(original, 1);
    advance(original, 1);

    RunTrace control_trace{"control", original.checkpoint(), {}, {}, {}, {}};
    RunTrace treated_trace{"treated", original.checkpoint(),
                           {{"plant/state", "x", Config::integer(10)}}, {}, {}, {}};
    ExperimentRun control(environment, directory, spec(), control_trace.label);
    control.restore(control_trace.origin);
    ExperimentRun treated(environment, directory, spec(), treated_trace.label);
    treated.restore(apply_interventions(treated_trace.origin, treated_trace.interventions));

    for (int frame = 3; frame <= 5; ++frame) {
        advance(control, 1);
        advance(treated, 1);
        control_trace.samples.push_back(control.sample());
        treated_trace.samples.push_back(treated.sample());
    }
    CHECK(control_trace.origin.frame == 2);
    CHECK(treated_trace.origin.frame == 2);
    CHECK(control_trace.samples.size() == 3);
    CHECK(control_trace.samples[0].frame == 3);
    CHECK(control_trace.samples[2].frame == 5);
    CHECK(observed(control_trace.samples[2]) == kControl[5]);
    CHECK(observed(treated_trace.samples[2]) == kTreated[5]);

    // 记录中的起点快照保持共同起点，干预只进入被干预分支的记录。
    CHECK(truth(control_trace.origin.truth, "x") == 2);
    CHECK(truth(treated_trace.origin.truth, "x") == 2);
    CHECK(treated_trace.interventions.size() == 1);
    CHECK(control_trace.interventions.empty());
}

void nested_assembly() {
    AssemblyDefinition environment;
    environment.add_scope("outer");
    environment.add_scope("inner", "outer");
    environment.add_instance("test.fragile", "counter", Config::integer(0), "outer/inner");
    for (const auto* symbol : {"advance", "x"}) {
        environment.export_symbol(symbol, {"counter", symbol}, "outer/inner");
        environment.export_symbol(symbol, {"inner", symbol}, "outer");
    }
    const auto loaded = AssemblyDefinition::parse(environment.to_json());
    ModuleFactoryDirectory directory;
    directory.add_definition("test.fragile", fragile);
    const ExperimentSpec settings{{"outer", "advance"}, {}, {{"x", {"outer", "x"}}}};
    ExperimentRun source(loaded, directory, settings);
    source.step();
    const auto checkpoint = source.checkpoint();
    CHECK(module_state(checkpoint.truth, "outer").stateless);
    CHECK(module_state(checkpoint.truth, "outer/inner").stateless);
    CHECK(module_state(checkpoint.truth, "outer/inner/counter").state == Config::integer(1));
    ExperimentRun control(loaded, directory, settings);
    ExperimentRun treated(loaded, directory, settings);
    control.restore(checkpoint);
    treated.restore(apply_interventions(checkpoint, {{"outer/inner/counter", "", Config::integer(10)}}));
    control.step();
    treated.step();
    CHECK(observed(control.sample(), "x") == 2);
    CHECK(observed(treated.sample(), "x") == 11);
    CHECK(observed(source.sample(), "x") == 1);

    // 只声明装配器生成的容器；不得掩盖作者模块缺少声明。
    directory.add_definition("unknown", [](const std::string& name, const Config&) { return Module(name); });
    environment.add_instance("unknown", "missing", {}, "outer/inner");
    ExperimentRun incomplete(environment, directory, settings);
    failure(ErrorCode::state_incomplete, {"outer/inner/missing", {}}, [&] { incomplete.checkpoint(); });
}

void reentrant_operations() {
    std::function<void()> hook = [] {};
    auto values = std::make_shared<std::pair<Integer, Integer>>(0, 0);
    ModuleFactoryDirectory directory;
    directory.add_definition("guarded", [&](const std::string& name, const Config&) {
        Module module(name);
        module.add_method<void>("advance", {}, [&, values] {
            ++values->first;
            hook(); // x 已更新而 y 尚未更新，不能产生合法检查点。
            ++values->second;
        });
        module.add_method<void>("drive", {}, [&] { hook(); });
        module.add_value<Integer>("x", [&, values] { hook(); return values->first; });
        module.add_state("guarded.v1", [&, values] {
            hook();
            return Config::array({Config::integer(values->first), Config::integer(values->second)});
        }, [&, values](const Config& state) {
            values->first = state.elements().at(0).integer();
            hook();
            values->second = state.elements().at(1).integer();
        });
        return module;
    });
    AssemblyDefinition environment;
    environment.add_instance("guarded", "counter");
    ExperimentRun run(environment, directory,
                      {{"counter", "advance"}, {{"input", {"counter", "drive"}}}, {{"x", {"counter", "x"}}}},
                      "guarded");
    const auto origin = run.checkpoint();
    const std::vector<std::pair<std::string, std::function<void()>>> operations = {
        {"drive", [&] { run.drive("input"); }}, {"step", [&] { run.step(); }},
        {"observe", [&] { run.observe(); }}, {"sample", [&] { run.sample(); }},
        {"checkpoint", [&] { run.checkpoint(); }}, {"restore", [&] { run.restore(origin); }}
    };
    for (const auto& outer : operations) {
        int attempts = 0;
        hook = [&] {
            for (const auto& inner : operations) {
                failure(ErrorCode::execution_failed, {"guarded", inner.first}, inner.second);
                ++attempts;
            }
        };
        outer.second();
        CHECK(attempts >= 6);

        // 每类作者回调抛错后都释放保护，允许宿主恢复并继续。
        hook = [] { throw std::runtime_error("callback failed"); };
        bool rejected = false;
        try { outer.second(); } catch (const EngineError&) { rejected = true; }
        CHECK(rejected);
        hook = [] {};
        run.restore(origin);
        run.step();
        CHECK(run.frame() == 1);
        CHECK(values->first == 1 && values->second == 1);
    }
}

void frame_limit() {
    AssemblyDefinition environment;
    environment.add_instance("test.fragile", "counter", Config::integer(0));
    ModuleFactoryDirectory directory;
    directory.add_definition("test.fragile", fragile);
    ExperimentRun run(environment, directory, {{"counter", "advance"}, {}, {{"x", {"counter", "x"}}}}, "limit");
    auto checkpoint = run.checkpoint();
    checkpoint.frame = std::numeric_limits<Integer>::max() - 1;
    run.restore(checkpoint);
    run.step();
    CHECK(run.frame() == std::numeric_limits<Integer>::max());
    const auto before = run.checkpoint();
    failure(ErrorCode::execution_failed, {"limit", "step"}, [&] { run.step(); });
    CHECK(run.frame() == before.frame);
    CHECK(run.checkpoint().truth.modules.front().state == before.truth.modules.front().state);
    CHECK(observed(run.sample(), "x") == 1); // 拒绝发生在模型回调之前。
    checkpoint.frame = 0;
    run.restore(checkpoint);
    run.step();
    CHECK(run.frame() == 1);
}

void observation_memory() {
    int captures = 0;
    ModuleFactoryDirectory directory;
    directory.add_definition("memory", [&](const std::string& name, const Config& config) {
        auto visible = std::make_shared<Integer>(0);
        auto memory = std::make_shared<Integer>(config.integer());
        Module module(name);
        module.add_value<Integer>("visible", [visible] { return *visible; });
        module.add_method<void>("advance", {}, [visible, memory] {
            *visible += *memory;
            *memory += 2;
        });
        module.add_state("memory.v1", [&, visible, memory] {
            ++captures;
            return Config::object({{"visible", Config::integer(*visible)}, {"memory", Config::integer(*memory)}});
        }, [visible, memory](const Config& state) {
            const auto next_visible = require_integer(state, "visible");
            const auto next_memory = require_integer(state, "memory");
            *visible = next_visible;
            *memory = next_memory;
        });
        return module;
    });
    AssemblyDefinition environment;
    environment.add_instance("memory", "model", Config::integer(3));
    const ExperimentSpec settings{{"model", "advance"}, {}, {{"output", {"model", "visible"}}}};
    // 主体仅接收 Observation 数据，不接收研究运行对象或 Sample。
    const auto subject = [](const Observation& observation) {
        CHECK(observation.values.size() == 1);
        CHECK(observation.values.count("memory") == 0);
        return std::any_cast<Integer>(observation.values.at("output"));
    };
    Checkpoint origin;
    {
        ExperimentRun source(environment, directory, settings);
        source.step(); // visible=3, 未公开记忆=5
        CHECK(subject(source.observe()) == 3);
        CHECK(captures == 0);
        origin = source.checkpoint();
        CHECK(module_state(origin.truth, "model").state.find("memory")->integer() == 5);
        source.step();
        CHECK(subject(source.observe()) == 8);
    }
    ExperimentRun restored(environment, directory, settings);
    restored.restore(origin);
    const auto before = captures;
    const Observation kept = restored.observe();
    CHECK(kept.frame == 1);
    CHECK(subject(kept) == 3);
    restored.step();
    CHECK(subject(restored.observe()) == 8); // 未恢复记忆时会错误得到 6。
    restored.step();
    CHECK(subject(restored.observe()) == 15);
    CHECK(captures == before);
    CHECK(subject(kept) == 3);
    auto incomplete = origin;
    module_state(incomplete.truth, "model").state = Config::object({{"visible", Config::integer(3)}});
    failure(ErrorCode::invalid_state, {"model", {}}, [&] { restored.restore(incomplete); });
    CHECK(subject(restored.observe()) == 15);
}

void trace_replay() {
    // 返回后原运行、原装配与原工厂目录均已销毁；记录独立持有运行条件。
    const ExperimentRecord record = [] {
        ExperimentRecord result{definition(), spec(), {{kPlant, "plant.integer.v1"}, {kStimulus, "stimulus.integer.v1"}}, {}, {}};
        const auto directory = factories();
        ExperimentRun source(result.assembly, directory, result.spec);
        advance(source, 1);
        advance(source, 1);
        const auto origin = source.checkpoint();
        result.branches.push_back({"control", origin, {}, {}, {}, {}});
        result.branches.push_back({"treated", origin, {{"plant/state", "x", Config::integer(10)}}, {}, {}, {}});
        result.inputs = {{2, "a", {Integer{2}}}, {3, "a", {Integer{-1}}}, {4, "a", {Integer{3}}}};
        for (auto& trace : result.branches) {
            ExperimentRun run(result.assembly, directory, result.spec, trace.label);
            run.restore(apply_interventions(trace.origin, trace.interventions));
            trace.samples.push_back(run.sample());
            for (const auto& input : result.inputs) {
                CHECK(run.frame() == input.frame);
                run.drive(input.name, input.arguments);
                run.step();
                trace.samples.push_back(run.sample());
            }
        }
        return result;
    }();
    CHECK(record.implementations.at(kPlant) == "plant.integer.v1");
    CHECK(record.implementations.at(kStimulus) == "stimulus.integer.v1");
    const auto directory = factories(); // 宿主选择与上述标识一致的实现。
    const auto loaded = AssemblyDefinition::parse(record.assembly.to_json());
    for (const auto& trace : record.branches) {
        ExperimentRun replay(loaded, directory, record.spec, trace.label);
        replay.restore(apply_interventions(trace.origin, trace.interventions));
        std::size_t index = 0;
        const auto verify = [&] {
            const auto actual = replay.sample();
            const auto& expected = trace.samples.at(index++);
            CHECK(actual.frame == expected.frame);
            CHECK(observed(actual) == observed(expected));
            for (const auto& module : expected.truth.modules) CHECK(module_state(actual.truth, module.path).state == module.state);
        };
        verify();
        for (const auto& input : record.inputs) {
            CHECK(replay.frame() == input.frame);
            replay.drive(input.name, input.arguments);
            replay.step();
            verify();
        }
        CHECK(index == trace.samples.size());
        CHECK(observed(replay.sample()) == (trace.label == "control" ? Tuple{6, 3, 7} : Tuple{14, 11, 23}));
    }
}

void run_directory() {
    const auto directory = factories();
    ExperimentRun run(definition(), directory, spec(), "view");
    CHECK((run.scopes() == std::vector<std::string>{"", "input", "plant", "plant/state", "plant/update"}));
    CHECK(run.catalog().size() == 6);          // plant 的 4 项导出与 input 的 2 项。
    CHECK(run.catalog("plant").size() == 5);   // state.x/y/z/write 与 update.advance。
    CHECK(run.requirements().size() == 1);
    CHECK(run.requirements("plant").size() == 5);  // update 的 input、x、y、z、write。
    CHECK(run.connections().size() == 1);
    CHECK(run.connections("plant").size() == 5);  // x、y、z、write 与 input 转接。
    const auto catalog = run.catalog();
    const auto advance_entry = std::find_if(catalog.begin(), catalog.end(), [](const Declaration& item) {
        return item.reference == Reference{"plant", "advance"};
    });
    CHECK(advance_entry != catalog.end());
    CHECK(advance_entry->kind == SymbolKind::method);
    CHECK(advance_entry->result_type == typeid(void));
    CHECK(run.spec().inputs.size() == 1);
    CHECK(run.spec().inputs.front().first == "a");
    CHECK((run.spec().observations.front().second == Reference{"plant", "x"}));
    failure(ErrorCode::missing_module, {"absent", {}}, [&] { run.catalog("absent"); });
    // 只读目录查询不改变逻辑帧与后续结果。
    CHECK(run.frame() == 0);
    CHECK(observed(run.sample()) == kControl[0]);
    advance(run, 1);
    CHECK(observed(run.sample()) == kControl[1]);
}

Tuple tuple(const example::Values& values) { return {values.x, values.y, values.z}; }

void example_model() {
    // 可复用示例组件的装配、规格与参考序列手工核对一致。
    const auto directory = example::factories();
    ExperimentRun source(example::environment(), directory, example::specification());
    for (int frame = 1; frame <= 2; ++frame) {
        advance(source, 1);
        CHECK(observed(source.sample()) == tuple(example::reference_control[frame]));
    }
    const auto origin = source.checkpoint();
    ExperimentRun treated(example::environment(), directory, example::specification());
    treated.restore(apply_interventions(
        origin, {{example::reference_intervention_module, example::reference_intervention_field,
                  Config::integer(example::reference_intervention_value)}}));
    CHECK(observed(treated.sample()) == tuple(example::reference_treated[2]));
    for (Integer frame = 2; frame < 5; ++frame) {
        advance(treated, 1);
        CHECK(observed(treated.sample()) == tuple(example::reference_treated[frame + 1]));
    }

    // 共享语言资源按登记路径加载，说明解析为中文并能回退默认模板。
    TextCatalog catalog;
    for (const auto& resource : example::i18n_resources(ASCEND_EXAMPLE_MODEL_DIR)) catalog.load(resource);
    const auto catalog_items = treated.catalog("plant");
    const auto declared = std::find_if(catalog_items.begin(), catalog_items.end(), [](const Declaration& item) {
        return item.reference == Reference{"plant/state", "x"};
    });
    CHECK(declared != catalog_items.end());
    CHECK(catalog.resolve("zh-CN", declared->description) == "状态变量 x；下一步取 x + a");
    CHECK(catalog.resolve("en", declared->description) == "State variable x; next value is x + a");

    // int64 极值输入：x + a 与 y + z 溢出分别报错，逻辑帧不前进且不产生有符号溢出。
    const auto maximum = std::numeric_limits<Integer>::max();
    ExperimentRun overflow_x(example::environment({maximum, 0, 0}, 1), directory, example::specification());
    const auto diagnostic =
        failure(ErrorCode::execution_failed, {"plant/update", "advance"}, [&] { overflow_x.step(); });
    CHECK(render_diagnostic(diagnostic).find("x + a") != std::string::npos);
    CHECK(overflow_x.frame() == 0);
    ExperimentRun overflow_z(example::environment({0, maximum, 1}, 1), directory, example::specification());
    failure(ErrorCode::execution_failed, {"plant/update", "advance"}, [&] { overflow_z.step(); });
    CHECK(overflow_z.frame() == 0);
    CHECK(observed(overflow_z.sample()) == Tuple{0, maximum, 1});
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"state_declarations", state_declarations},
        {"capture_restore", capture_restore},
        {"deep_copy", deep_copy},
        {"branch_isolation", branch_isolation},
        {"sampling_stability", sampling_stability},
        {"lifetime", lifetime},
        {"intervention_propagation", intervention_propagation},
        {"intervention_errors", intervention_errors},
        {"nested_fields", nested_fields},
        {"restore_mismatches", restore_mismatches},
        {"spec_errors", spec_errors},
        {"step_failure", step_failure},
        {"trace_record", trace_record},
        {"nested_assembly", nested_assembly},
        {"reentrant_operations", reentrant_operations},
        {"frame_limit", frame_limit},
        {"observation_memory", observation_memory},
        {"trace_replay", trace_replay},
        {"run_directory", run_directory},
        {"example_model", example_model},
    };
    if (argc != 2 || tests.count(argv[1]) == 0) return 2;
    try {
        tests.at(argv[1])();
        std::cout << argv[1] << ": passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << argv[1] << ": " << error.what() << '\n';
        return 1;
    }
}
