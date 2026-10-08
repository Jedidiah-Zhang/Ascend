#include <ascend/example/experiment_model.hpp>
#include <ascend/module_package.hpp>
#include <ascend/engine.hpp>
#include <ascend/i18n.hpp>
#include <ascend/session/session.hpp>
#include <ascend/session/project.hpp>

#include <any>
#include <algorithm>
#include <cstdint>
#include <filesystem>
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
using namespace ascend::session;
using Integer = std::int64_t;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

// ---------------------------------------------------------------------------
// 会话模板

std::vector<I18nResource> core_resources() {
    return {{engine_text_domain, WORKBENCH_ENGINE_I18N},
            {session_text_domain, WORKBENCH_SESSION_I18N}};
}

ModelTemplate example_template() {
    ModelTemplate model;
    model.name = "确定性三变量";
    model.assembly = example::environment();
    model.spec = example::specification();
    model.factories = example::factories(WORKBENCH_EXAMPLE_ROOT);
    model.implementations = {{example::plant_definition, example::plant_implementation},
                             {example::stimulus_definition, example::stimulus_implementation}};
    model.resources = core_resources();
    const auto module_resources = example::i18n_resources(WORKBENCH_EXAMPLE_ROOT);
    model.resources.insert(model.resources.end(), module_resources.begin(), module_resources.end());
    model.locale = "zh-CN";
    model.modules = std::make_shared<ModuleLibrary>();
    example::register_module_library(*model.modules);
    return model;
}

std::unique_ptr<Session> make_session(ModelTemplate model) {
    auto adapters = std::make_shared<AdapterRegistry>();
    return std::make_unique<Session>(std::move(model), std::move(adapters));
}

std::unique_ptr<Session> example_session() { return make_session(example_template()); }

OperationResult set_input(Session& session, const std::string& name, Integer value) {
    return session.set_input(name, std::any(value));
}

OperationResult advance(Session& session, Integer input, std::int64_t steps = 1) {
    const auto driven = set_input(session, "a", input);
    if (!driven.ok) return driven;
    return session.run(steps);
}

// ---------------------------------------------------------------------------
// 测试用模型：输入、推进与采样可分别失败

constexpr const char* kFragile = "test.fragile";

Module fragile(const std::string& instance, const Config& config) {
    Integer initial = 0;
    if (!config.is_null()) initial = config.integer();
    auto input = std::make_shared<Integer>(0);
    auto value = std::make_shared<Integer>(initial);
    MethodOptions drive_options;
    drive_options.contract = "test.scalar-drive.v1";
    MethodOptions advance_options;
    advance_options.contract = "test.advance.v1";

    Module module(instance);
    module.add_value<Integer>("out", [value] { return *value; }, "Fragile output", "test.scalar.v1");
    module.add_method<void, Integer>("drive", {"next"},
                                     [input](Integer next) {
                                         if (next == 100) throw std::runtime_error("input rejected");
                                         *input = next;
                                     },
                                     drive_options);
    module.add_method<void>("advance", {}, [input, value] {
        if (*input == 50) throw std::runtime_error("advance rejected");
        ++*value;
    }, advance_options);
    module.add_state("test.fragile.state.v1", [value] {
        if (*value == 1) throw std::runtime_error("capture rejected");
        return Config::integer(*value);
    }, [value](const Config& state) { *value = state.integer(); });
    return module;
}

ModelTemplate fragile_template() {
    ModelTemplate model;
    model.name = "脆弱模型";
    model.assembly.add_instance(kFragile, "m", Config::integer(0));
    model.spec = ExperimentSpec{{"m", "advance"},
                                {{"a", {"m", "drive"}}},
                                {{"out", {"m", "out"}}}};
    model.factories.add_definition(kFragile, fragile);
    model.implementations = {{kFragile, "test.fragile.v1"}};
    model.resources = core_resources();
    model.locale = "zh-CN";
    return model;
}

// 观测读取进程级计数：记录与重放使用不同实例时结果不同，用于核对重放同源性检查。
std::int64_t& construction_counter() {
    static std::int64_t counter = 0;
    return counter;
}

constexpr const char* kRacing = "test.racing";

Module racing(const std::string& instance, const Config&) {
    auto value = std::make_shared<Integer>(0);
    ++construction_counter();
    Module module(instance);
    module.add_value<Integer>("out", [value] { return *value + construction_counter(); });
    module.add_method<void>("advance", {}, [value] { ++*value; });
    module.add_state("test.racing.state.v1", [value] { return Config::integer(*value); },
                     [value](const Config& state) { *value = state.integer(); });
    return module;
}

ModelTemplate racing_template() {
    ModelTemplate model;
    model.name = "非确定模型";
    model.assembly.add_instance(kRacing, "m", Config{});
    model.spec = ExperimentSpec{{"m", "advance"}, {}, {{"out", {"m", "out"}}}};
    model.factories.add_definition(kRacing, racing);
    model.implementations = {{kRacing, "test.racing.v1"}};
    model.resources = core_resources();
    model.locale = "zh-CN";
    return model;
}

// 状态捕获可按测试开关抛错：验证检查点与分支首次采样的异常转换。
constexpr const char* kPoison = "test.poison";

bool& poison_capture() {
    static bool poison = false;
    return poison;
}

Module poison(const std::string& instance, const Config&) {
    auto value = std::make_shared<Integer>(0);
    Module module(instance);
    module.add_value<Integer>("out", [value] { return *value; });
    module.add_method<void>("advance", {}, [value] { ++*value; });
    module.add_state("test.poison.state.v1",
                     [value] {
                         if (poison_capture()) throw std::runtime_error("capture rejected");
                         return Config::integer(*value);
                     },
                     [value](const Config& state) { *value = state.integer(); });
    return module;
}

ModelTemplate poison_template() {
    ModelTemplate model;
    model.name = "捕获可失败模型";
    model.assembly.add_instance(kPoison, "m", Config{});
    model.spec = ExperimentSpec{{"m", "advance"}, {}, {{"out", {"m", "out"}}}};
    model.factories.add_definition(kPoison, poison);
    model.implementations = {{kPoison, "test.poison.v1"}};
    model.resources = core_resources();
    model.locale = "zh-CN";
    return model;
}

// 双输入模型：同一驱动前逻辑帧上的两项输入按记录顺序驱动后只推进一次。
constexpr const char* kDual = "test.dual";

Module dual(const std::string& instance, const Config&) {
    auto input_a = std::make_shared<Integer>(0);
    auto input_b = std::make_shared<Integer>(0);
    auto value = std::make_shared<Integer>(0);
    Module module(instance);
    module.add_value<Integer>("out", [value] { return *value; });
    module.add_method<void, Integer>("drive_a", {"value"}, [input_a](Integer next) { *input_a = next; });
    module.add_method<void, Integer>("drive_b", {"value"}, [input_b](Integer next) {
        if (next == 100) throw std::runtime_error("b rejected");
        *input_b = next;
    });
    module.add_method<void>("advance", {}, [input_a, input_b, value] {
        *value += *input_a * 10 + *input_b;
    });
    module.add_state("test.dual.state.v1", [value] { return Config::integer(*value); },
                     [value](const Config& state) { *value = state.integer(); });
    return module;
}

ModelTemplate dual_template() {
    ModelTemplate model;
    model.name = "双输入模型";
    model.assembly.add_instance(kDual, "m", Config{});
    model.spec = ExperimentSpec{{"m", "advance"},
                                {{"a", {"m", "drive_a"}}, {"b", {"m", "drive_b"}}},
                                {{"out", {"m", "out"}}}};
    model.factories.add_definition(kDual, dual);
    model.implementations = {{kDual, "test.dual.v1"}};
    model.resources = core_resources();
    model.locale = "zh-CN";
    return model;
}

// ---------------------------------------------------------------------------
// 视图辅助

void check_values(const TrackTraceView& trace, std::size_t index, Integer x, Integer y, Integer z) {
    const auto& sample = trace.samples.at(index);
    CHECK(sample.observations.size() == 3);
    CHECK(sample.observations[0].display == std::to_string(x));
    CHECK(sample.observations[1].display == std::to_string(y));
    CHECK(sample.observations[2].display == std::to_string(z));
}

std::vector<Integer> values_except_prefix(const TrackTraceView& trace) {
    std::vector<Integer> result;
    for (const auto& sample : trace.samples) {
        for (const auto& cell : sample.observations) result.push_back(std::stoll(cell.display));
    }
    return result;
}

// ---------------------------------------------------------------------------
// 用例

void adapter_int64() {
    AdapterRegistry registry;
    const ValueAdapter* adapter = registry.find(typeid(Integer));
    CHECK(adapter != nullptr);
    CHECK(adapter->name() == "int64");
    CHECK(adapter->editable());
    CHECK(registry.find(typeid(double)) == nullptr);

    TextRef error;
    CHECK(!adapter->parse("", error).has_value());
    CHECK(error.key().domain == session_text_domain);
    CHECK(!error.key().key.empty());
    CHECK(!adapter->parse(" 1", error).has_value());
    CHECK(!adapter->parse("1 ", error).has_value());
    CHECK(!adapter->parse("1.0", error).has_value());
    CHECK(!adapter->parse("abc", error).has_value());
    CHECK(!adapter->parse("+-1", error).has_value());
    CHECK(!adapter->parse("9223372036854775808", error).has_value());
    CHECK(!adapter->parse("-9223372036854775809", error).has_value());
    CHECK(std::any_cast<Integer>(*adapter->parse("+42", error)) == 42);
    CHECK(std::any_cast<Integer>(*adapter->parse("-0", error)) == 0);
    CHECK(std::any_cast<Integer>(*adapter->parse("-9223372036854775808", error)) ==
          std::numeric_limits<Integer>::min());
    CHECK(std::any_cast<Integer>(*adapter->parse("9223372036854775807", error)) ==
          std::numeric_limits<Integer>::max());

    const auto maximum = std::numeric_limits<Integer>::max();
    CHECK(adapter->format(std::any(maximum)) == "9223372036854775807");
    const auto exact = adapter->number(std::any(Integer{42}));
    CHECK(exact.has_value() && exact->exact && exact->value == 42.0);
    const auto approximate = adapter->number(std::any(maximum));
    CHECK(approximate.has_value() && !approximate->exact);
    // 精确整数视图：只用于精确计算，显示仍走 format。
    const auto integer_view = adapter->integer(std::any(Integer{5}));
    CHECK(integer_view.has_value() && *integer_view == 5);
    CHECK(!adapter->integer(std::any(1.0)).has_value());

    CHECK(adapter->equal(std::any(Integer{1}), std::any(Integer{1})));
    CHECK(!adapter->equal(std::any(Integer{1}), std::any(Integer{2})));
    CHECK(!adapter->equal(std::any(Integer{1}), std::any(1.0)));

    // 适配器错误按语言资源渲染；缺失资源时使用英文默认模板。
    TextCatalog texts;
    texts.load({session_text_domain, WORKBENCH_SESSION_I18N});
    CHECK(!adapter->parse("x", error).has_value());
    CHECK(render_text(error, &texts, "zh-CN") == "整数文本只能包含可选的符号与十进制数字");
    CHECK(render_text(error) == "Integer text may only contain an optional sign and decimal digits");
    CHECK(!adapter->parse("9223372036854775808", error).has_value());
    CHECK(render_text(error, &texts, "zh-CN") == "整数超出 64 位有符号范围");
}

void localized_texts() {
    // 会话检查项与轨迹标签按语言资源渲染；未加载资源时回退英文默认模板。
    auto adapters = std::make_shared<AdapterRegistry>();
    auto plain = example_template();
    plain.resources.clear();
    plain.locale = "en";
    auto session = std::make_unique<Session>(std::move(plain), adapters);
    CHECK(session->load().ok);
    const auto report = session->check();
    CHECK(report.passed);
    CHECK(report.items.front().area == "Factory and assembly");
    CHECK(report.items.back().area == "Check scope");
    CHECK(session->trace(0).label == "Run");
    CHECK(session->comparison().rows.empty());

    // 加载会话语言资源后为中文。
    auto localized = example_session();
    CHECK(localized->load().ok);
    const auto zh = localized->check();
    CHECK(zh.passed);
    CHECK(zh.items.front().area == "工厂与装配");
    CHECK(zh.items.back().note.find("不构成实现正确性证明") != std::string::npos);
    CHECK(localized->trace(0).label == "运行");
    CHECK(localized->create_checkpoint().ok);
    CHECK(localized->create_branches(
              {{"", {}}, {"", {{example::reference_intervention_module, example::reference_intervention_field,
                                Config::integer(example::reference_intervention_value)}}}})
              .ok);
    CHECK(localized->trace(1).label == "对照");
    CHECK(localized->trace(2).label == "干预");
}

void load_and_check() {
    auto session = example_session();
    const auto loaded = session->load();
    CHECK(loaded.ok);
    CHECK(loaded.status.phase == Phase::runnable);
    CHECK(loaded.status.run_id.has_value() && *loaded.status.run_id == 1);
    CHECK(loaded.status.draft_revision == 1);
    CHECK(loaded.status.run_revision == 1);
    CHECK(!loaded.status.dirty);
    CHECK(!loaded.status.has_checkpoint);
    CHECK(loaded.status.tracks.size() == 1);
    CHECK(loaded.status.tracks[0].frame == 0);
    CHECK(loaded.status.tracks[0].samples == 1);
    CHECK(!session->status().last_failure.has_value());
    CHECK(session->resource_diagnostics().empty());

    // 采样单元携带精确整数视图（游标差值等精确计算使用）。
    const auto first_sample = session->trace(0).samples.front();
    CHECK(first_sample.observations.size() == 3);
    CHECK(first_sample.observations[0].integer.has_value() && *first_sample.observations[0].integer == 0);

    // 规格视图：推进入口、输入驱动目标与观测。
    const auto spec = session->spec();
    CHECK(spec.advance.module == "plant" && spec.advance.symbol == "advance");
    CHECK(spec.inputs.size() == 1);
    CHECK(spec.inputs[0].first == "a");
    CHECK(spec.inputs[0].second.module == "input" && spec.inputs[0].second.symbol == "drive");
    CHECK(spec.observations.size() == 3);
    CHECK(spec.observations[0].first == "x");
    CHECK(spec.observations[0].second.module == "plant" && spec.observations[0].second.symbol == "x");

    const auto report = session->check();
    CHECK(report.passed);
    bool has_assembly_item = false;
    bool has_spec_item = false;
    for (const auto& item : report.items) {
        has_assembly_item = has_assembly_item || item.area == "工厂与装配";
        has_spec_item = has_spec_item || item.area == "实验规格";
    }
    CHECK(has_assembly_item && has_spec_item);

    const auto catalog = session->catalog();
    CHECK(catalog.revision == 1);
    CHECK(catalog.modules.size() == 5);
    const ModuleNodeView* root = nullptr;
    for (const auto& module : catalog.modules) {
        if (module.path.empty()) root = &module;
    }
    CHECK(root != nullptr);
    const DeclarationView* advance = nullptr;
    for (const auto& declaration : root->declarations) {
        if (declaration.reference == Reference{"plant", "advance"}) advance = &declaration;
    }
    CHECK(advance != nullptr);
    CHECK(advance->kind == SymbolKind::method);
    CHECK(advance->description == "按 x' = x + a、y' = x、z' = y + z 推进一步；右侧读取步开始值。");

    const auto instances = session->instances();
    CHECK(instances.size() == 2);
    CHECK(instances[0].name == "plant");
    CHECK(instances[0].definition == example::plant_definition);
    CHECK(instances[0].config.find("x") != nullptr);
    CHECK(instances[1].name == "input");

    const auto& names = session->input_names();
    CHECK((names == std::vector<std::string>{"a"}));
    const auto& observations = session->observation_names();
    CHECK((observations == std::vector<std::string>{"x", "y", "z"}));
}

void config_revision() {
    auto session = example_session();
    CHECK(session->load().ok);
    const auto revision = session->status().draft_revision;

    // 相同配置不产生新修订。
    const auto unchanged = session->set_instance_config(
        "", "plant", Config::object({{"x", Config::integer(0)}, {"y", Config::integer(0)},
                                     {"z", Config::integer(0)}}));
    CHECK(unchanged.ok);
    CHECK(session->status().draft_revision == revision);

    CHECK(advance(*session, 1).ok);
    check_values(session->trace(0), 1, 1, 0, 0);

    const auto changed = session->set_instance_config(
        "", "plant", Config::object({{"x", Config::integer(5)}, {"y", Config::integer(0)},
                                     {"z", Config::integer(0)}}));
    CHECK(changed.ok);
    CHECK(session->status().draft_revision == revision + 1);
    CHECK(session->status().dirty);
    CHECK(session->status().run_revision == revision);
    // 目录仍对应旧修订。
    CHECK(session->catalog().revision == revision);

    // 旧运行继续按旧配置推进，结果仍关联原运行身份。
    CHECK(session->run(1).ok);
    check_values(session->trace(0), 2, 2, 1, 0);
    CHECK(*session->status().run_id == 1);

    // 应用重建后新配置才作用于新运行；轨迹与检查点重置。
    const auto applied = session->apply();
    CHECK(applied.ok);
    CHECK(*applied.status.run_id == 2);
    CHECK(applied.status.draft_revision == revision + 1);
    CHECK(applied.status.run_revision == revision + 1);
    CHECK(!applied.status.dirty);
    CHECK(applied.status.tracks[0].samples == 1);
    CHECK(applied.status.tracks[0].frame == 0);
    CHECK(applied.status.checkpoint_frame == -1);
    CHECK(session->catalog().revision == revision + 1);

    CHECK(session->run(1).ok);
    check_values(session->trace(0), 1, 6, 5, 0);

    // 未登记实例给出诊断且不改变修订。
    const auto missing = session->set_instance_config("", "absent", Config{});
    CHECK(!missing.ok);
    CHECK(session->status().draft_revision == revision + 1);
    CHECK(!missing.diagnostics.empty());
}

void step_run_stop() {
    auto stepping = example_session();
    auto running = example_session();
    CHECK(stepping->load().ok);
    CHECK(running->load().ok);
    for (int index = 0; index < 5; ++index) {
        CHECK(advance(*stepping, 1).ok);
    }
    CHECK(set_input(*running, "a", 1).ok);
    CHECK(running->run(5).ok);
    CHECK(values_except_prefix(stepping->trace(0)) == values_except_prefix(running->trace(0)));

    // 停止请求在逻辑帧边界生效，实际完成逻辑帧如实报告。
    int checks = 0;
    const auto stopped = running->run(10, [&] { return checks++ >= 2; });
    CHECK(stopped.ok);
    CHECK(stopped.stopped);
    CHECK(stopped.completed_steps == 2);
    CHECK(stopped.status.phase == Phase::stopped);
    CHECK(stopped.status.tracks[0].frame == 7);
    CHECK(!stopped.status.last_failure.has_value());
    const auto stopped_trace = running->trace(0);
    CHECK(stopped_trace.events.back().kind == StepEvent::Kind::stopped);

    // 继续运行至与单步会话相同的逻辑帧，结果一致。
    CHECK(running->run(3).ok);
    for (int index = 0; index < 5; ++index) {
        CHECK(advance(*stepping, 1).ok);
    }
    CHECK(stepping->status().tracks[0].frame == 10);
    CHECK(running->status().tracks[0].frame == 10);
    CHECK(values_except_prefix(stepping->trace(0)) == values_except_prefix(running->trace(0)));

    // 轻量轨迹访问：元数据计数与全量视图一致，尾部增量从指定下标开始。
    const auto info = running->series_info();
    CHECK(info.size() == 1);
    CHECK(info[0].samples == 11 && info[0].events == 11);
    CHECK(running->trace_delta(0, 0, 0).samples.size() == running->trace(0).samples.size());
    const auto delta = running->trace_delta(0, 9, 9);
    CHECK(delta.samples.size() == 2);
    CHECK(delta.samples.front().frame == 9);
    CHECK(delta.events.size() == 2);
    CHECK(delta.events.front().kind == StepEvent::Kind::completed);
}

void checkpoint_branches() {
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    check_values(session->trace(0), 0, 0, 0, 0);
    check_values(session->trace(0), 2, 2, 1, 0);

    const auto checkpoint = session->create_checkpoint();
    CHECK(checkpoint.ok);
    CHECK(checkpoint.status.has_checkpoint);
    CHECK(checkpoint.status.checkpoint_frame == 2);
    const auto fields = session->state_fields();
    bool found_x = false;
    for (const auto& field : fields) {
        if (field.module == "plant/state" && field.field == "x") {
            found_x = true;
            CHECK(field.integer);
            CHECK(field.value == 2);
            CHECK(field.display == "2");
        }
    }
    CHECK(found_x);

    const auto branches = session->create_branches(
        {{"对照", {}},
         {"干预", {{example::reference_intervention_module, example::reference_intervention_field,
                    Config::integer(example::reference_intervention_value)}}}});
    CHECK(branches.ok);
    CHECK(branches.status.branches == 2);
    CHECK(session->series_count() == 3);

    const auto exploration = session->trace(0);
    CHECK(exploration.label == "运行");
    CHECK(exploration.samples.size() == 3);
    check_values(exploration, 2, 2, 1, 0);

    const auto control = session->trace(1);
    CHECK(control.label == "对照");
    CHECK(control.origin == 2);
    CHECK(control.samples.size() == 1);
    check_values(control, 0, 2, 1, 0);

    const auto treated = session->trace(2);
    CHECK(treated.label == "干预");
    CHECK(treated.samples.size() == 1);
    check_values(treated, 0, 10, 1, 0);
    CHECK(treated.interventions.size() == 1);
    CHECK(treated.interventions[0].module == "plant/state");
    CHECK(treated.interventions[0].field == "x");
    CHECK(treated.interventions[0].previous == "2");
    CHECK(treated.interventions[0].replacement == "10");

    // 分支模式不再创建检查点。
    const auto rejected = session->create_checkpoint();
    CHECK(!rejected.ok);
    CHECK(rejected.status.phase == Phase::runnable);

    // 同步驱动到逻辑帧 5。
    CHECK(advance(*session, 1, 3).ok);
    const auto status = session->status();
    CHECK(status.tracks.size() == 2);
    CHECK(status.tracks[0].frame == 5);
    CHECK(status.tracks[1].frame == 5);
    CHECK(status.tracks[0].samples == 4);
    CHECK(status.tracks[1].samples == 4);

    const auto control_full = session->trace(1);
    const auto treated_full = session->trace(2);
    check_values(control_full, 1, 3, 2, 1);
    check_values(control_full, 3, 5, 4, 6);
    check_values(treated_full, 1, 11, 10, 1);
    check_values(treated_full, 3, 13, 12, 22);

    // 同一逻辑帧差值：干预 − 对照；干预直接变化在起点可见，y、z 逐步传播。
    const auto comparison = session->comparison();
    CHECK((comparison.variables == std::vector<std::string>{"x", "y", "z"}));
    CHECK(comparison.rows.size() == 4);
    CHECK(comparison.unpaired.empty());
    const std::map<std::int64_t, std::vector<Integer>> expected = {
        {2, {8, 0, 0}}, {3, {8, 8, 0}}, {4, {8, 8, 8}}, {5, {8, 8, 16}}};
    for (const auto& row : comparison.rows) {
        const auto& want = expected.at(row.frame);
        for (std::size_t index = 0; index < row.cells.size(); ++index) {
            CHECK(row.cells[index].comparable);
            CHECK(row.cells[index].integer.has_value());
            CHECK(*row.cells[index].integer == want[index]);
            CHECK(row.cells[index].difference == std::to_string(want[index]));
        }
    }

    // 检查点在分支运行后保持不变；真值详情可见 x 的直接变化。
    CHECK(session->status().checkpoint_frame == 2);
    const auto detail = session->sample_detail(2, 2);
    CHECK(detail.found);
    bool detail_x = false;
    for (const auto& module : detail.truth) {
        if (module.path != "plant/state") continue;
        for (const auto& field : module.fields) {
            if (field.first == "x") {
                detail_x = true;
                CHECK(field.second == "10");
            }
        }
    }
    CHECK(detail_x);

    // 从检查点重建分支：轨迹回到起点，共同输入记录清空。
    const auto reset = session->reset_branches();
    CHECK(reset.ok);
    CHECK(session->trace(1).samples.size() == 1);
    CHECK(session->trace(2).samples.size() == 1);
    CHECK(session->record().inputs == 0);
}

void branch_order() {
    // 交换分支的执行顺序与创建顺序，各自结果不变；源运行继续推进不改变检查点。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);
    CHECK(advance(*session, 1, 3).ok);  // 探索运行继续到逻辑帧 5。
    CHECK(session->status().tracks[0].frame == 5);

    const auto branches = session->create_branches(
        {{"干预", {{example::reference_intervention_module, example::reference_intervention_field,
                    Config::integer(example::reference_intervention_value)}}},
         {"对照", {}}});
    CHECK(branches.ok);
    CHECK(session->status().checkpoint_frame == 2);
    CHECK(session->trace(1).origin == 2);
    CHECK(session->trace(2).origin == 2);
    CHECK(session->trace(0).samples.size() == 6);

    CHECK(advance(*session, 1, 3).ok);
    check_values(session->trace(1), 3, 13, 12, 22);
    check_values(session->trace(2), 3, 5, 4, 6);
}

void intervention_errors() {
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);

    // 非初始逻辑帧上的目标：无状态容器、缺失字段、缺失模块分别被拒绝。
    const auto stateless = session->create_branches(
        {{"对照", {}}, {"干预", {{"plant", "x", Config::integer(1)}}}});
    CHECK(!stateless.ok);
    CHECK(session->series_count() == 1);
    CHECK(!stateless.diagnostics.empty());

    const auto missing_field = session->create_branches(
        {{"对照", {}}, {"干预", {{"plant/state", "walnut", Config::integer(1)}}}});
    CHECK(!missing_field.ok);

    const auto missing_module = session->create_branches(
        {{"对照", {}}, {"干预", {{"absent/state", "x", Config::integer(1)}}}});
    CHECK(!missing_module.ok);

    // 新值与现有值类型不一致。
    const auto kind = session->create_branches(
        {{"对照", {}}, {"干预", {{"plant/state", "x", Config::string("ten")}}}});
    CHECK(!kind.ok);
    CHECK(kind.diagnostics.front().message.find("plant/state#x") != std::string::npos);

    // 分支数量固定为两个。
    const auto count = session->create_branches(
        {{"对照", {}}, {"干预", {{"plant/state", "x", Config::integer(10)}}}, {"多余", {}}});
    CHECK(!count.ok);

    // 失败后会话仍保持源模式并可成功建立分支。
    CHECK(session->series_count() == 1);
    const auto ok = session->create_branches(
        {{"对照", {}}, {"干预", {{"plant/state", "x", Config::integer(10)}}}});
    CHECK(ok.ok);
}

void partial_branch_failure() {
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);
    const auto maximum = std::numeric_limits<Integer>::max();
    const auto branches = session->create_branches(
        {{"对照", {}}, {"干预", {{"plant/state", "x", Config::integer(maximum - 1)}}}});
    CHECK(branches.ok);

    // 第二步在干预分支溢出：命令停止，保留各自实际到达的逻辑帧。
    const auto failed = advance(*session, 1, 2);
    CHECK(!failed.ok);
    CHECK(failed.completed_steps == 1);
    CHECK(failed.status.phase == Phase::failed);
    CHECK(failed.status.tracks[0].frame == 4);
    CHECK(failed.status.tracks[1].frame == 3);
    CHECK(failed.status.tracks[1].advance_failures == 1);
    CHECK(failed.status.tracks[1].failed);
    CHECK(failed.diagnostics.size() == 1);

    // 失败状态禁止推进，只能在共同有效逻辑帧比较。
    const auto blocked = session->step();
    CHECK(!blocked.ok);
    CHECK(blocked.status.tracks[0].frame == 4);
    const auto comparison = session->comparison();
    CHECK(comparison.rows.size() == 2);
    CHECK((comparison.unpaired == std::vector<std::int64_t>{4}));
    // 推进失败进入记录的分支轨迹。
    CHECK(session->record().failures == 1);

    // 失败记录的重放：已记录采样逐逻辑帧核对；失败步骤的输入范围列入说明。
    const auto replay = session->replay();
    CHECK(replay.ok);
    CHECK(!replay.complete);
    CHECK(replay.verified_frames == 5);
    CHECK(replay.notes.size() == 1);
    CHECK(replay.notes.front().find("干预") != std::string::npos);
    CHECK(!replay.diagnostic.has_value());
    CHECK(!replay.first_mismatch.has_value());

    // 从检查点重建分支后恢复可运行。
    const auto reset = session->reset_branches();
    CHECK(reset.ok);
    CHECK(session->status().phase == Phase::runnable);
    CHECK(session->trace(1).samples.size() == 1);
    CHECK(session->trace(2).samples.size() == 1);
}

void failure_paths() {
    auto session = example_session();
    CHECK(session->load().ok);
    const auto maximum = std::numeric_limits<Integer>::max();
    CHECK(session->set_instance_config(
              "", "plant",
              Config::object({{"x", Config::integer(maximum)}, {"y", Config::integer(0)},
                              {"z", Config::integer(0)}}))
              .ok);
    CHECK(session->apply().ok);
    CHECK(set_input(*session, "a", 1).ok);
    const auto failed = session->step();
    CHECK(!failed.ok);
    CHECK(failed.status.phase == Phase::failed);
    CHECK(failed.status.tracks[0].frame == 0);
    CHECK(failed.status.tracks[0].advance_failures == 1);
    CHECK(failed.status.tracks[0].samples == 1);
    CHECK(failed.status.last_failure.has_value());
    CHECK(failed.diagnostics.front().message.find("x + a") != std::string::npos);
    CHECK(session->trace(0).events.back().kind == StepEvent::Kind::advance_failed);

    // 输入失败与采样失败分别记录，不误报逻辑帧。
    auto fragile = make_session(fragile_template());
    CHECK(fragile->load().ok);
    CHECK(set_input(*fragile, "a", 100).ok);
    const auto input_failed = fragile->step();
    CHECK(!input_failed.ok);
    CHECK(input_failed.status.tracks[0].input_failures == 1);
    CHECK(input_failed.status.tracks[0].frame == 0);
    CHECK(!input_failed.diagnostics.empty());
    CHECK(fragile->trace(0).events.back().kind == StepEvent::Kind::input_failed);

    auto sampler = make_session(fragile_template());
    CHECK(sampler->load().ok);
    CHECK(set_input(*sampler, "a", 1).ok);
    const auto sample_failed = sampler->step();
    CHECK(!sample_failed.ok);
    CHECK(sample_failed.status.tracks[0].sample_failures == 1);
    // 推进已完成：逻辑帧前进、采样失败只影响记录。
    CHECK(sample_failed.status.tracks[0].frame == 1);
    CHECK(sample_failed.status.tracks[0].samples == 1);
    CHECK(sampler->trace(0).events.back().kind == StepEvent::Kind::sample_failed);
}

void record_inputs() {
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(set_input(*session, "a", 1).ok);
    CHECK(session->run(2).ok);
    CHECK(set_input(*session, "a", 2).ok);
    CHECK(session->run(2).ok);

    const auto record = session->record();
    CHECK(record.branches == 1);
    CHECK(record.inputs == 4);
    CHECK(record.input_list.size() == 4);
    CHECK(record.input_list[0].frame == 0 && record.input_list[0].value == "1");
    CHECK(record.input_list[1].frame == 1 && record.input_list[1].value == "1");
    CHECK(record.input_list[2].frame == 2 && record.input_list[2].value == "2");
    CHECK(record.input_list[3].frame == 3 && record.input_list[3].value == "2");
    CHECK(session->status().recorded_inputs == 4);

    // 轨迹反映实际驱动的输入：x 依次为 0、1、2、4、6。
    const auto trace = session->trace(0);
    check_values(trace, 0, 0, 0, 0);
    check_values(trace, 1, 1, 0, 0);
    check_values(trace, 2, 2, 1, 0);
    check_values(trace, 3, 4, 2, 1);
    check_values(trace, 4, 6, 4, 3);
}

void replay() {
    // 场景 A 全流程重放成功。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);
    CHECK(session->create_branches(
              {{"对照", {}},
               {"干预", {{example::reference_intervention_module, example::reference_intervention_field,
                          Config::integer(example::reference_intervention_value)}}}})
              .ok);
    CHECK(advance(*session, 1, 3).ok);
    const auto before = session->status();
    const auto report = session->replay();
    CHECK(report.ok);
    CHECK(!report.first_mismatch.has_value());
    CHECK(report.verified_frames == 8);
    CHECK(report.notes.empty());
    CHECK(!report.diagnostic.has_value());
    // 重放不修改当前运行。
    CHECK(session->status().tracks[0].frame == before.tracks[0].frame);
    CHECK(session->status().tracks[0].samples == before.tracks[0].samples);

    // 重放同源性检查：实例化结果不同的模型在首个逻辑帧报告不一致。
    construction_counter() = 0;
    auto racing = make_session(racing_template());
    CHECK(racing->load().ok);
    CHECK(racing->run(2).ok);
    const auto mismatch = racing->replay();
    CHECK(!mismatch.ok);
    CHECK(mismatch.first_mismatch.has_value());
    CHECK(mismatch.first_mismatch->branch == "运行");
    CHECK(mismatch.first_mismatch->frame == 0);
    CHECK(!mismatch.first_mismatch->field.empty());
    CHECK(!racing->status().last_failure.has_value());

    // 实现标识缺失时检查失败，不建立运行。
    auto missing_ids = example_template();
    missing_ids.implementations.clear();
    auto invalid = make_session(std::move(missing_ids));
    const auto failed = invalid->load();
    CHECK(!failed.ok);
    CHECK(failed.status.phase == Phase::editing);
    CHECK(!failed.diagnostics.empty());
    CHECK(failed.diagnostics.front().message.find("实现标识") != std::string::npos);
    CHECK(!invalid->check().passed);
    CHECK(!invalid->apply().ok);
}

void state_rules() {
    auto session = example_session();
    // 空会话只允许打开；检查也被拒绝并说明原因。
    const auto empty_step = session->step();
    CHECK(!empty_step.ok);
    CHECK(empty_step.status.phase == Phase::empty);
    CHECK(!empty_step.diagnostics.empty());
    CHECK(!session->create_checkpoint().ok);
    const auto empty_check = session->check();
    CHECK(!empty_check.passed);
    CHECK(!empty_check.diagnostics.empty());
    CHECK(empty_check.items.front().area == "会话状态");
    const auto loaded = session->load();
    CHECK(loaded.ok);

    // 外部输入可选：未设置时按模块自身配置值（示例为 a = 1）推进。
    const auto autonomous = session->step();
    CHECK(autonomous.ok);
    CHECK(session->status().phase == Phase::runnable);
    CHECK(session->status().tracks[0].frame == 1);
    CHECK(set_input(*session, "a", 2).ok);
    CHECK(session->step().ok);
    CHECK(session->status().phase == Phase::runnable);

    // 检查不改变运行与轨迹。
    const auto frame = session->status().tracks[0].frame;
    CHECK(session->check().passed);
    CHECK(session->status().tracks[0].frame == frame);

    // 失败状态：禁止推进与检查点，允许检查与重建。
    const auto maximum = std::numeric_limits<Integer>::max();
    CHECK(session->set_instance_config(
              "", "plant",
              Config::object({{"x", Config::integer(maximum)}, {"y", Config::integer(0)},
                              {"z", Config::integer(0)}}))
              .ok);
    CHECK(session->apply().ok);
    CHECK(session->step().ok == false);
    CHECK(session->status().phase == Phase::failed);
    CHECK(!session->step().ok);
    CHECK(!session->create_checkpoint().ok);
    CHECK(session->check().passed);
    CHECK(session->apply().ok);
    CHECK(session->status().phase == Phase::runnable);

    // 输入类型不匹配被拒绝。
    const auto wrong_type = session->set_input("a", std::any(1.5));
    CHECK(!wrong_type.ok);
    const auto unknown = session->set_input("nope", std::any(Integer{1}));
    CHECK(!unknown.ok);
}

void limits() {
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(set_input(*session, "a", 1).ok);
    CHECK(!session->run(0).ok);
    CHECK(!session->run(max_steps_per_command + 1).ok);
    const auto full = session->run(max_steps_per_command);
    CHECK(full.ok);
    CHECK(full.status.tracks[0].frame == max_trace_frames);
    const auto over = session->run(1);
    CHECK(!over.ok);
    CHECK(over.status.tracks[0].frame == max_trace_frames);
    CHECK(over.diagnostics.front().message.find("上限") != std::string::npos);
}

void dual_input_replay() {
    // 多输入规格：同一驱动前逻辑帧上的两项输入按记录顺序驱动后只推进一次。
    auto session = make_session(dual_template());
    CHECK(session->load().ok);
    CHECK(set_input(*session, "a", 1).ok);
    CHECK(set_input(*session, "b", 2).ok);
    CHECK(session->run(2).ok);
    const auto trace = session->trace(0);
    CHECK(trace.samples.size() == 3);
    CHECK(trace.samples[1].observations[0].display == "12");
    CHECK(trace.samples[2].observations[0].display == "24");
    const auto record = session->record();
    CHECK(record.inputs == 4);
    CHECK(record.input_list[0].name == "a" && record.input_list[1].name == "b");
    CHECK(record.input_list[0].frame == 0 && record.input_list[1].frame == 0);
    const auto report = session->replay();
    CHECK(report.ok);
    CHECK(report.verified_frames == 3);
    CHECK(report.notes.empty());
}

void autonomous_replay() {
    // 自主演化（未驱动任何输入）同样逐逻辑帧核对全部记录采样。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(session->run(3).ok);
    CHECK(session->trace(0).samples.size() == 4);
    CHECK(session->record().inputs == 0);
    const auto report = session->replay();
    CHECK(report.ok);
    CHECK(report.complete);
    CHECK(report.verified_frames == 4);
    CHECK(report.notes.empty());

    // 先自主演化、随后驱动输入的混合记录同样逐逻辑帧核对。
    auto mixed = example_session();
    CHECK(mixed->load().ok);
    CHECK(mixed->run(2).ok);
    CHECK(set_input(*mixed, "a", 2).ok);
    CHECK(mixed->run(2).ok);
    CHECK(mixed->trace(0).samples.size() == 5);
    CHECK(mixed->record().inputs == 2);
    const auto mixed_report = mixed->replay();
    CHECK(mixed_report.ok);
    CHECK(mixed_report.complete);
    CHECK(mixed_report.verified_frames == 5);
}

void partial_input_records() {
    // 步骤内逐输入记录：a 成功、b 失败时，a 保留在共同输入记录中。
    auto session = make_session(dual_template());
    CHECK(session->load().ok);
    CHECK(set_input(*session, "a", 7).ok);
    CHECK(set_input(*session, "b", 100).ok);
    const auto result = session->step();
    CHECK(!result.ok);
    CHECK(result.status.phase == Phase::failed);
    const auto record = session->record();
    CHECK(record.inputs == 1);
    CHECK(record.input_list[0].name == "a");
    CHECK(record.input_list[0].frame == 0);
    CHECK(record.input_list[0].value == "7");
    // 失败步骤不产生采样；已记录的采样逐逻辑帧核对。
    const auto report = session->replay();
    CHECK(report.ok);
    CHECK(report.verified_frames == 1);
}

void exception_boundaries() {
    // 检查点状态捕获与分支首次采样的引擎异常转换为诊断，不逃出会话接口。
    auto session = make_session(poison_template());
    CHECK(session->load().ok);
    CHECK(session->step().ok);
    poison_capture() = true;
    const auto checkpoint = session->create_checkpoint();
    poison_capture() = false;
    CHECK(!checkpoint.ok);
    CHECK(!checkpoint.diagnostics.empty());
    CHECK(session->status().phase == Phase::runnable);
    CHECK(!session->status().has_checkpoint);
    CHECK(session->create_checkpoint().ok);

    auto branches_session = make_session(poison_template());
    CHECK(branches_session->load().ok);
    CHECK(branches_session->create_checkpoint().ok);
    poison_capture() = true;
    const auto branches = branches_session->create_branches({{"", {}}, {"", {}}});
    poison_capture() = false;
    CHECK(!branches.ok);
    CHECK(!branches.diagnostics.empty());
    CHECK(branches_session->status().phase == Phase::runnable);
    CHECK(branches_session->status().has_checkpoint);
    CHECK(branches_session->create_branches({{"", {}}, {"", {}}}).ok);
}


void save_open_round_trip() {
    // 场景 A 保存后在新会话打开：接管为活动运行并可继续推进。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);
    CHECK(session->create_branches(
              {{"对照", {}},
               {"干预", {{example::reference_intervention_module, example::reference_intervention_field,
                          Config::integer(example::reference_intervention_value)}}}})
              .ok);
    CHECK(advance(*session, 1, 3).ok);
    const auto saved = session->record_file();
    CHECK(saved.traces.size() == 3);
    CHECK(saved.traces.front().exploration);
    CHECK(saved.inputs.size() == 3);
    CHECK(saved.checkpoint.has_value() && saved.checkpoint->frame == 2);

    auto opened = example_session();
    const auto result = opened->open_record(saved);
    CHECK(result.ok);
    CHECK(opened->status().phase == Phase::runnable);
    CHECK(opened->status().tracks.size() == 2);
    CHECK(opened->status().tracks[0].frame == 5);
    CHECK(opened->status().tracks[1].frame == 5);
    const auto comparison = opened->comparison();
    CHECK(comparison.rows.size() == 4);
    CHECK(comparison.rows.back().frame == 5);
    CHECK(comparison.rows.back().cells[0].comparable);
    CHECK(comparison.rows.back().cells[0].difference == "8");
    const auto report = opened->replay();
    CHECK(report.ok && report.complete);
    // 继续推进：两分支到逻辑帧 6，并可再次保存。
    CHECK(opened->run(1).ok);
    CHECK(opened->status().tracks[0].frame == 6);
    CHECK(opened->status().tracks[1].frame == 6);
    const auto again = opened->record_file();
    CHECK(again.traces.size() == 3);
    CHECK(again.traces[0].trace.label == saved.traces[0].trace.label);
}

void open_record_state() {
    // 含失败记录的文件只读打开：禁止推进，重放可用，应用重建退出记录态。
    auto session = example_session();
    CHECK(session->load().ok);
    const auto maximum = std::numeric_limits<Integer>::max();
    CHECK(session->set_instance_config("", "plant",
              Config::object({{"x", Config::integer(maximum)}, {"y", Config::integer(0)},
                              {"z", Config::integer(0)}}))
              .ok);
    CHECK(session->apply().ok);
    CHECK(set_input(*session, "a", 1).ok);
    CHECK(!session->step().ok);
    CHECK(session->status().phase == Phase::failed);
    const auto saved = session->record_file();
    CHECK(saved.traces.size() == 1);
    CHECK(!saved.traces.front().events.empty());

    auto opened = example_session();
    const auto result = opened->open_record(saved);
    CHECK(result.ok);
    CHECK(result.status.phase == Phase::record);
    // 目录按打开的模型重建：不残留上一会话目录，且与打开后的草稿修订一致。
    const auto opened_catalog = opened->catalog();
    CHECK(!opened_catalog.modules.empty());
    CHECK(opened_catalog.revision == opened->status().draft_revision);
    bool has_plant = false;
    for (const auto& node : opened_catalog.modules) {
        if (node.path == "plant") has_plant = true;
    }
    CHECK(has_plant);
    CHECK(opened->status().tracks.size() == 1);
    CHECK(opened->status().tracks[0].failed);
    CHECK(!opened->step().ok);
    CHECK(!opened->run(1).ok);
    CHECK(!opened->create_checkpoint().ok);
    const auto report = opened->replay();
    CHECK(report.ok);
    CHECK(opened->trace(0).samples.size() == 1);
    CHECK(!opened->trace(0).events.empty());
    CHECK(opened->trace(0).events.back().diagnostic.has_value());
    CHECK(opened->apply().ok);
    CHECK(opened->status().phase == Phase::runnable);
}

void open_rejects_and_falls_back() {
    // 实现标识不匹配与采样不一致都只进入只读记录态；空轨迹文件拒绝且会话不变。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    const auto saved = session->record_file();

    auto mismatched = saved;
    mismatched.implementations[example::plant_definition] = "wrong.implementation";
    auto opened = example_session();
    const auto first = opened->open_record(mismatched);
    CHECK(first.ok);
    CHECK(first.status.phase == Phase::record);
    CHECK(!first.diagnostics.empty());
    CHECK(!opened->step().ok);

    auto corrupted = saved;
    corrupted.traces.front().trace.samples.back().observations["x"] = Integer(99);
    auto second_session = example_session();
    const auto second = second_session->open_record(corrupted);
    CHECK(second.ok);
    CHECK(second.status.phase == Phase::record);

    auto empty = saved;
    empty.traces.clear();
    const auto before = opened->status();
    const auto rejected = opened->open_record(empty);
    CHECK(!rejected.ok);
    CHECK(opened->status().phase == before.phase);
    CHECK(opened->status().tracks.size() == before.tracks.size());
}

void open_exploration_only() {
    // 仅探索轨迹的文件按无分支单运行接管；不一致或单分支都只进入只读记录态。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);
    CHECK(session->create_branches(
              {{"对照", {}},
               {"干预", {{example::reference_intervention_module, example::reference_intervention_field,
                          Config::integer(example::reference_intervention_value)}}}})
              .ok);
    CHECK(advance(*session, 1, 3).ok);
    const auto full = session->record_file();
    CHECK(full.traces.size() == 3);

    auto exploration_only = full;
    exploration_only.traces.resize(1);
    exploration_only.inputs = exploration_only.traces.front().trace.driven;  // 单运行的共同输入
    CHECK(exploration_only.traces.front().exploration);
    auto opened = example_session();
    const auto first = opened->open_record(exploration_only);
    CHECK(first.ok);
    CHECK(first.status.phase == Phase::runnable);
    CHECK(opened->status().tracks.size() == 1);
    CHECK(opened->status().tracks[0].frame == 2);
    const auto report = opened->replay();
    CHECK(report.ok && report.complete);
    CHECK(opened->run(1).ok);
    CHECK(opened->status().tracks[0].frame == 3);

    auto corrupted = exploration_only;
    corrupted.traces.front().trace.samples.back().observations["x"] = Integer(99);
    auto record = example_session();
    const auto second = record->open_record(corrupted);
    CHECK(second.ok);
    CHECK(second.status.phase == Phase::record);
    CHECK(!record->step().ok);
    CHECK(!record->replay().ok);  // 重放探索轨迹报采样不一致

    auto single_branch = full;
    single_branch.traces.resize(2);
    auto half = example_session();
    const auto third = half->open_record(single_branch);
    CHECK(third.ok);
    CHECK(third.status.phase == Phase::record);
    CHECK(!half->step().ok);
}

void module_packages() {
    // 模块库（ENV-18）：载入、概要、重复拒绝、卸载与无模块库拒绝。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(session->module_packages().empty());

    Engine engine;
    engine.add(example::module_library_source("probe", {}));
    ModulePackage package;
    package.manifest = export_module_manifest(engine, "", "probe");
    package.manifest.definition = example::library_source_definition;
    package.manifest.version = "1.0";
    package.manifest.implementation = example::library_source_implementation;
    const auto bytes = encode_module_package(package);

    const auto loaded = session->load_module_package(bytes);
    CHECK(loaded.ok);
    CHECK(session->module_packages().size() == 1);
    const auto view = session->module_packages().front();
    CHECK(view.definition == example::library_source_definition);
    CHECK(view.version == "1.0");
    CHECK(view.implementation == example::library_source_implementation);
    CHECK(view.stateless);
    CHECK(view.declarations == 1);
    CHECK(view.requirements == 0);
    CHECK(view.resources == 0);

    const auto duplicate = session->load_module_package(bytes);
    CHECK(!duplicate.ok);
    CHECK(!duplicate.diagnostics.empty());
    CHECK(session->module_packages().size() == 1);

    const auto unloaded = session->unload_module_package(example::library_source_definition);
    CHECK(unloaded.ok);
    CHECK(session->module_packages().empty());
    const auto again = session->unload_module_package(example::library_source_definition);
    CHECK(!again.ok);
    CHECK(!again.diagnostics.empty());

    // 未提供模块库的模板拒绝命令，且不影响会话。
    auto plain_template = example_template();
    plain_template.modules.reset();
    auto plain = make_session(std::move(plain_template));
    CHECK(plain->load().ok);
    const auto rejected = plain->load_module_package(bytes);
    CHECK(!rejected.ok);
    CHECK(!rejected.diagnostics.empty());
    CHECK(plain->module_packages().empty());
    CHECK(!plain->unload_module_package(example::library_source_definition).ok);
}

void system_assembly() {
    // 因果系统装配（切片 A）：新建、加模块、接线、规格、检查与应用。
    auto session = example_session();
    CHECK(session->status().phase == Phase::empty);
    const auto modules = session->available_modules();
    CHECK(std::find(modules.begin(), modules.end(), example::plant_definition) != modules.end());
    CHECK(std::find(modules.begin(), modules.end(), example::library_source_definition) != modules.end());

    // 空会话不预置模板规格：规格、输入与观测均为空（无示例残留）。
    const auto fresh_spec = session->spec();
    CHECK(fresh_spec.advance == Reference{});
    CHECK(fresh_spec.inputs.empty());
    CHECK(fresh_spec.observations.empty());
    CHECK(session->input_names().empty());
    CHECK(session->observation_names().empty());

    // 空会话尚未确立系统身份：编辑命令返回诊断且状态不变（含本地化文案）。
    const auto before = session->status();
    const auto empty_edit = session->add_module(example::plant_definition, "plant");
    CHECK(!empty_edit.ok);
    CHECK(!empty_edit.diagnostics.empty());
    CHECK(empty_edit.status.phase == Phase::empty);
    CHECK(empty_edit.status.draft_revision == before.draft_revision);
    CHECK(!empty_edit.status.dirty);
    CHECK(empty_edit.status.model_name.empty());
    CHECK(empty_edit.diagnostics.front().message.find("因果系统") != std::string::npos);
    CHECK(!session->remove_module("absent").ok);
    CHECK(!session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(!session->set_spec_advance({"plant", "advance"}).ok);
    const auto empty_apply = session->apply();
    CHECK(!empty_apply.ok);
    CHECK(empty_apply.diagnostics.front().message.find("研究") != std::string::npos);
    CHECK(session->status().phase == Phase::empty);
    CHECK(session->status().draft_revision == before.draft_revision);

    // 新建系统：清空草稿与规格，进入编辑态；名称进入状态。
    const auto created = session->new_system("接线测试");
    CHECK(created.ok);
    CHECK(created.status.phase == Phase::editing);
    CHECK(session->status().model_name == "接线测试");
    CHECK(session->spec().advance == Reference{});
    CHECK(session->catalog().revision == session->status().draft_revision);  // 空系统目录修订对齐

    // 增加模块实例：未知定义、非法名与重名被拒绝。
    CHECK(!session->add_module("no.such", "x").ok);
    CHECK(!session->add_module(example::plant_definition, "a/b").ok);
    CHECK(session->add_module(example::plant_definition, "plant").ok);
    CHECK(session->add_module(example::stimulus_definition, "input").ok);  // 未给配置：用定义登记的默认值
    CHECK(!session->add_module(example::plant_definition, "plant").ok);
    CHECK(session->instances().size() == 2);
    // 默认构造配置预填（plant 0,0,0；stimulus 1）。
    const auto preconfigured = session->instances();
    CHECK(preconfigured[0].name == "plant");
    CHECK(preconfigured[0].config.find("x")->integer() == 0);
    CHECK(preconfigured[0].config.find("z")->integer() == 0);
    CHECK(preconfigured[1].config.integer() == 1);
    // 观测未显式选择时默认记录根作用域全部公开量：plant/x,y,z 与 input/value。
    const auto derived_observations = session->spec().observations;
    CHECK(derived_observations.size() == 4);

    // 接线：需求 → 提供方；重复连接替换；断开后重复断开为无操作成功。
    CHECK(session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(session->disconnect_requirement({"plant", "input"}).ok);
    CHECK(session->disconnect_requirement({"plant", "input"}).ok);

    // 未接线时检查失败；提供方实例不存在时连接被拒绝；接上后通过。
    CHECK(!session->check().passed);
    CHECK(!session->connect_requirement({"ghost", "input"}, {"input", "value"}).ok);
    CHECK(session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);

    // 规格：推进入口与观测显式选择；输入按公开单参数方法推导。
    CHECK(session->set_spec_advance({"plant", "advance"}).ok);
    CHECK(session->set_spec_observations({{"x", {"plant", "x"}}, {"y", {"plant", "y"}}, {"z", {"plant", "z"}}}).ok);
    CHECK(session->spec().inputs.size() == 1);
    CHECK(session->spec().inputs[0].first == "input.drive");
    CHECK((session->spec().inputs[0].second == Reference{"input", "drive"}));

    const auto report = session->check();
    CHECK(report.passed);

    // 应用重建运行：推进两步后观测与示例参考一致（a = 1 恒定）。
    CHECK(session->apply().ok);
    CHECK(session->status().phase == Phase::runnable);
    CHECK(session->run(2).ok);
    const auto trace = session->trace(0);
    CHECK(trace.samples.size() == 3);
    CHECK(trace.samples.back().frame == 2);
    const auto x_index = std::find(trace.variables.begin(), trace.variables.end(), "x");
    CHECK(x_index != trace.variables.end());
    const auto& x_cell = trace.samples.back().observations[static_cast<std::size_t>(x_index - trace.variables.begin())];
    CHECK(x_cell.integer.has_value() && *x_cell.integer == 2);

    // 结构编辑使草稿修订递增并进入未应用状态；移除实例一并移除其连接。
    const auto revision = session->status().draft_revision;
    CHECK(session->remove_module("input").ok);
    CHECK(session->status().draft_revision > revision);
    CHECK(session->status().dirty);
    CHECK(!session->check().passed);
    CHECK(!session->remove_module("input").ok);  // 已移除

    // 模块库定义可直接实例化；实现标识由宿主核对：未载入包时检查拒绝，载入包后由清单提供。
    CHECK(session->add_module(example::library_source_definition, "source").ok);
    CHECK(session->spec().observations.size() == 3);  // 显式选择后不被推导覆盖
    CHECK(session->add_module(example::library_accumulator_definition, "counter").ok);
    CHECK(!session->check().passed);
    const auto package = [](const auto& factory, const std::string& definition, const std::string& implementation) {
        Engine engine;
        engine.add(factory("probe", {}));
        ModulePackage encoded;
        encoded.manifest = export_module_manifest(engine, "", "probe");
        encoded.manifest.definition = definition;
        encoded.manifest.version = "1.0";
        encoded.manifest.implementation = implementation;
        return encode_module_package(encoded);
    };
    CHECK(session->load_module_package(package(example::module_library_source, example::library_source_definition,
                                               example::library_source_implementation)).ok);
    CHECK(session->load_module_package(package(example::module_library_accumulator,
                                               example::library_accumulator_definition,
                                               example::library_accumulator_implementation)).ok);
    CHECK(session->add_module(example::stimulus_definition, "input", Config::integer(1)).ok);
    CHECK(session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(session->check().passed);
    CHECK(session->apply().ok);

    // 再次新建系统：放弃运行与记录，回到编辑态。
    const auto reset = session->new_system("第二个系统");
    CHECK(reset.ok);
    CHECK(reset.status.phase == Phase::editing);
    CHECK(session->status().tracks.empty());
    CHECK(session->instances().empty());
    CHECK(session->status().model_name == "第二个系统");

    // 未应用的草稿变化不改变当前运行：运行沿用已应用规格，草稿新增输入不被驱动。
    auto probe = example_session();
    CHECK(probe->load().ok);
    CHECK(probe->add_module(example::library_accumulator_definition, "counter").ok);
    CHECK(probe->set_input("counter.add", std::any(Integer(5))).ok);
    CHECK(probe->run(1).ok);
    CHECK(probe->status().phase == Phase::runnable);
    CHECK(probe->status().tracks.front().frame == 1);
    CHECK(probe->observation_names().size() == 3);  // 轨迹与信号树仍用已应用规格
}

void system_file() {
    // 因果系统文件（`.aasm`）：编码、重新打开与失败路径；规格属于研究，不随系统保存。
    auto session = example_session();
    CHECK(session->new_system("保存测试").ok);
    CHECK(session->add_module(example::plant_definition, "plant").ok);
    CHECK(session->add_module(example::stimulus_definition, "input", Config::integer(1)).ok);
    CHECK(session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(session->set_spec_advance({"plant", "advance"}).ok);
    const auto encoded = session->encode_system();
    CHECK(encoded.ok);
    CHECK(!encoded.bytes.empty());

    auto reopened = example_session();
    const auto opened = reopened->open_system(encoded.bytes, "重新打开", "memory.aasm");
    CHECK(opened.ok);
    CHECK(opened.status.phase == Phase::editing);
    CHECK(reopened->status().model_name == "重新打开");
    CHECK(reopened->instances().size() == 2);
    CHECK(reopened->spec().advance == Reference{});  // 规格不属于因果系统
    CHECK(reopened->spec().inputs.size() == 1);      // 输入按系统公开方法推导
    CHECK(reopened->spec().observations.size() == 4);  // 观测默认记录公开量
    CHECK(reopened->set_spec_advance({"plant", "advance"}).ok);
    CHECK(reopened->set_spec_observations({{"x", {"plant", "x"}}}).ok);
    CHECK(reopened->check().passed);
    CHECK(reopened->apply().ok);
    CHECK(reopened->run(1).ok);

    // 解析失败不改变当前会话；未给名称时沿用现有系统名。
    auto fresh = example_session();
    CHECK(fresh->new_system("原系统").ok);
    const auto bad = fresh->open_system("not a system", "坏文件");
    CHECK(!bad.ok);
    CHECK(!bad.diagnostics.empty());
    CHECK(fresh->status().model_name == "原系统");
    CHECK(fresh->instances().empty());
    CHECK(fresh->open_system(encoded.bytes, "", "memory.aasm").ok);
    CHECK(fresh->status().model_name == "原系统");
    CHECK(fresh->instances().size() == 2);
}

void comparison_limits() {
    // 同一逻辑帧差值溢出与不可比较的明确标注。
    auto session = example_session();
    CHECK(session->load().ok);
    CHECK(advance(*session, 1, 2).ok);
    CHECK(session->create_checkpoint().ok);
    const auto minimum = std::numeric_limits<Integer>::min();
    const auto maximum = std::numeric_limits<Integer>::max();
    CHECK(session->create_branches(
              {{"对照", {{"plant/state", "x", Config::integer(minimum)}}},
               {"干预", {{"plant/state", "x", Config::integer(maximum)}}}})
              .ok);
    const auto comparison = session->comparison();
    CHECK(comparison.rows.size() == 1);
    CHECK(comparison.rows.front().frame == 2);
    const auto& cells = comparison.rows.front().cells;
    CHECK(cells.size() == 3);
    CHECK(!cells[0].comparable);
    CHECK(cells[0].difference == "差值超出 64 位");
    CHECK(!cells[0].integer.has_value());
    CHECK(cells[1].comparable);
    CHECK(cells[1].difference == "0");
    CHECK(cells[1].integer.has_value());
    CHECK(*cells[1].integer == 0);
}

}  // namespace

// ---------------------------------------------------------------------------
// 研究项目（ENV-16）

std::string temp_project_dir(const std::string& name) {
    const auto dir = std::filesystem::temp_directory_path() / ("ascend-project-" + name);
    std::error_code error;
    std::filesystem::remove_all(dir, error);
    return dir.string();
}

struct TempDirCleanup {
    std::string dir;
    ~TempDirCleanup() {
        std::error_code error;
        std::filesystem::remove_all(dir, error);
    }
};

void project_workflow() {
    namespace fs = std::filesystem;
    const std::string dir = temp_project_dir("workflow");
    TempDirCleanup cleanup{dir};

    // 新建研究：目录、研究文件、装配文件与 records/ 一并生成。
    auto session = example_session();
    const auto created = session->new_project(dir, "项目甲");
    CHECK(created.ok);
    CHECK(session->project().has_value());
    const fs::path study(session->project()->study_file);
    CHECK(fs::exists(study));
    CHECK(study.extension() == ".aexp");
    CHECK(fs::exists(session->project()->assembly_file));
    CHECK(fs::is_directory(fs::path(dir) / project_records_directory));
    CHECK(session->status().project_directory == dir);
    CHECK(session->status().study_saved);
    CHECK(session->status().record_saved);
    CHECK(session->system_name() == "项目甲");

    // 打开项目：研究名与装配恢复，进入编辑态。
    auto opened = example_session();
    const auto result = opened->open_project(dir);
    CHECK(result.ok);
    CHECK(opened->status().phase == Phase::editing);
    CHECK(opened->project().has_value());
    CHECK(opened->system_name() == "项目甲");
    CHECK(opened->status().study_saved);
    CHECK(opened->project()->assembly_file == session->project()->assembly_file);

    // 在项目草稿中装配示例世界并运行：保存项目写入研究文件与运行记录。
    CHECK(opened->add_module(example::plant_definition, "plant").ok);
    CHECK(opened->add_module(example::stimulus_definition, "input").ok);
    CHECK(opened->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(opened->set_spec_advance({"plant", "advance"}).ok);
    CHECK(opened->apply().ok);
    CHECK(!opened->status().record_saved);
    CHECK(opened->save_project().ok);
    CHECK(opened->status().record_saved);
    const auto entries = opened->records();
    CHECK(entries.size() == 1);
    CHECK(entries[0].readable);
    CHECK(entries[0].model == "项目甲");
    CHECK(entries[0].series >= 1);
    CHECK(opened->status().record_name.has_value());

    // 再次保存：写回同一记录文件，不新增记录。
    CHECK(opened->step().ok);
    CHECK(opened->save_project().ok);
    CHECK(opened->records().size() == 1);

    // 打开记录：接管为活动运行并关联当前记录。
    auto record_session = example_session();
    CHECK(record_session->open_project(dir).ok);
    const auto listed = record_session->records();
    CHECK(listed.size() == 1);
    const auto opened_record = record_session->open_record_file(listed[0].path);
    CHECK(opened_record.ok);
    CHECK(record_session->status().phase == Phase::runnable);
    CHECK(record_session->status().record_name.has_value());
    CHECK(record_session->project()->current_record.has_value());

    // 失败路径：空目录没有研究文件；非空目录拒绝新建。
    const std::string empty_dir = temp_project_dir("empty");
    TempDirCleanup empty_cleanup{empty_dir};
    fs::create_directories(empty_dir);
    auto empty_session = example_session();
    CHECK(!empty_session->open_project(empty_dir).ok);
    write_text_file((fs::path(empty_dir) / "keep.txt").string(), "x");
    auto nonempty_session = example_session();
    CHECK(!nonempty_session->new_project(empty_dir, "重复").ok);
}

void project_record_history() {
    namespace fs = std::filesystem;
    const std::string dir = temp_project_dir("history");
    TempDirCleanup cleanup{dir};

    auto session = example_session();
    CHECK(session->new_project(dir, "历史").ok);
    CHECK(session->add_module(example::plant_definition, "plant").ok);
    CHECK(session->add_module(example::stimulus_definition, "input").ok);
    CHECK(session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(session->set_spec_advance({"plant", "advance"}).ok);
    const auto plant_config = [](std::int64_t x) {
        return Config::object({{"x", Config::integer(x)},
                               {"y", Config::integer(0)},
                               {"z", Config::integer(0)}});
    };
    CHECK(session->set_instance_config("", "plant", plant_config(99)).ok);
    CHECK(session->apply().ok);
    CHECK(session->save_project().ok);
    const auto first_records = session->records();
    CHECK(first_records.size() == 1);
    const std::string first_record = first_records[0].path;

    // 重建运行后再次保存：分配新的记录文件，历史记录保留。
    CHECK(session->set_instance_config("", "plant", plant_config(100)).ok);
    CHECK(session->apply().ok);
    CHECK(session->save_project().ok);
    CHECK(session->records().size() == 2);

    // 打开历史记录：只接管运行快照，项目草稿仍是 x=100。
    const auto opened = session->open_record_file(first_record);
    CHECK(opened.ok);
    CHECK(session->status().phase == Phase::runnable);
    const auto instances = session->instances();
    const auto plant = std::find_if(instances.begin(), instances.end(),
                                    [](const InstanceView& view) { return view.name == "plant"; });
    CHECK(plant != instances.end());
    CHECK(plant->config.find("x") != nullptr && plant->config.find("x")->integer() == 100);

    // 保存不会把旧快照写回项目装配文件；历史记录自身仍是 x=99。
    CHECK(session->save_project().ok);
    const auto saved = AssemblyDefinition::parse(read_text_file(session->project()->assembly_file), "assembly");
    bool saved_ok = false;
    for (const auto& instance : saved.instances("")) {
        if (instance.name == "plant" && instance.config.find("x") != nullptr &&
            instance.config.find("x")->integer() == 100) {
            saved_ok = true;
        }
    }
    CHECK(saved_ok);
    const auto reopened = decode_record_file(read_text_file(first_record));
    bool snapshot_ok = false;
    for (const auto& instance : reopened.assembly.instances("")) {
        if (instance.name == "plant" && instance.config.find("x") != nullptr &&
            instance.config.find("x")->integer() == 99) {
            snapshot_ok = true;
        }
    }
    CHECK(snapshot_ok);
}

void project_record_lineage() {
    namespace fs = std::filesystem;
    const std::string dir = temp_project_dir("lineage");
    TempDirCleanup cleanup{dir};

    auto session = example_session();
    CHECK(session->new_project(dir, "血缘").ok);
    CHECK(session->add_module(example::plant_definition, "plant").ok);
    CHECK(session->add_module(example::stimulus_definition, "input").ok);
    CHECK(session->connect_requirement({"plant", "input"}, {"input", "value"}).ok);
    CHECK(session->set_spec_advance({"plant", "advance"}).ok);
    CHECK(session->apply().ok);
    CHECK(session->run(1).ok);
    CHECK(session->create_checkpoint().ok);
    CHECK(session->save_project().ok);
    const auto first = session->records();
    CHECK(first.size() == 1);
    const std::string single_record = first[0].path;
    CHECK(session->record_file().spec.observations.size() == 4);  // 示例根作用域公开量

    // 项目取消观测后保存：草稿规格与历史记录规格不同。
    CHECK(session->set_spec_observations({}).ok);
    CHECK(session->apply().ok);
    CHECK(session->save_project().ok);
    CHECK(session->records().size() == 2);

    // 打开历史记录：草稿仍是项目规格；从记录建立分支使用记录规格，重放可核对。
    const auto opened = session->open_record_file(single_record);
    CHECK(opened.ok);
    CHECK(session->status().phase == Phase::runnable);
    CHECK(session->spec().observations.empty());
    const std::vector<BranchRequest> branches = {{"对照", {}}, {"干预", {}}};
    CHECK(session->create_branches(branches).ok);
    const auto replay = session->replay();
    CHECK(replay.ok);
    CHECK(replay.complete);
    CHECK(session->save_project().ok);  // 接管会话保存回同一记录（分支追加）
    CHECK(session->records().size() == 2);

    // 只读记录重建分支：建立新运行身份，保存生成新记录而不覆盖历史。
    const auto branch_record = session->project()->current_record.value();
    auto damaged = decode_record_file(read_text_file(branch_record));
    for (auto& [definition, implementation] : damaged.implementations) {
        implementation = "damaged." + implementation;
    }
    const std::string damaged_path =
        (fs::path(dir) / project_records_directory / "damaged.arec").string();
    write_text_file(damaged_path, encode_record_file(damaged));
    CHECK(session->records().size() == 3);
    const auto read_only = session->open_record_file(damaged_path);
    CHECK(read_only.ok);
    CHECK(session->status().phase == Phase::record);
    const auto run_id_read_only = session->status().run_id;
    CHECK(session->reset_branches().ok);
    CHECK(session->status().phase == Phase::runnable);
    CHECK(session->status().run_id.has_value() && session->status().run_id != run_id_read_only);
    // 新运行按当前工厂登记实现标识：重建后可立即重放。
    const auto rebuilt_replay = session->replay();
    CHECK(rebuilt_replay.ok);
    CHECK(rebuilt_replay.complete);
    CHECK(session->save_project().ok);
    CHECK(session->records().size() == 4);
    const auto preserved = decode_record_file(read_text_file(damaged_path));
    bool damaged_kept = !preserved.implementations.empty();
    for (const auto& [definition, implementation] : preserved.implementations) {
        if (implementation.rfind("damaged.", 0) != 0) damaged_kept = false;
    }
    CHECK(damaged_kept);
    CHECK(session->project()->current_record.has_value() &&
          *session->project()->current_record != damaged_path);

    // 新记录可重新接管：实现标识与当前工厂一致、重放完全通过。
    const std::string rebuilt_record = *session->project()->current_record;
    auto verifier = example_session();
    CHECK(verifier->open_project(dir).ok);
    const auto reopened_rebuilt = verifier->open_record_file(rebuilt_record);
    CHECK(reopened_rebuilt.ok);
    CHECK(verifier->status().phase == Phase::runnable);
    const auto verifier_replay = verifier->replay();
    CHECK(verifier_replay.ok);
    CHECK(verifier_replay.complete);

    // 跨会话修订号碰撞不误报「已应用」：记录修订与项目修订相同但内容不同。
    auto collision = decode_record_file(read_text_file(*session->project()->current_record));
    collision.run_revision = session->status().draft_revision;
    const std::string collision_path =
        (fs::path(dir) / project_records_directory / "collision.arec").string();
    write_text_file(collision_path, encode_record_file(collision));
    const auto collision_open = session->open_record_file(collision_path);
    CHECK(collision_open.ok);
    CHECK(session->status().dirty);
}

void project_errors() {
    namespace fs = std::filesystem;

    // 已存在的空目录可创建项目；重复创建被拒绝且不留下第二个研究文件。
    const std::string dir = temp_project_dir("errors");
    TempDirCleanup cleanup{dir};
    fs::create_directories(dir);
    auto session = example_session();
    CHECK(session->new_project(dir, "空目录项目").ok);
    auto repeat = example_session();
    CHECK(!repeat->new_project(dir, "重复项目").ok);
    std::size_t studies = 0;
    for (const auto& entry : fs::directory_iterator(dir)) {
        if (entry.path().extension() == project_study_extension) ++studies;
    }
    CHECK(studies == 1);

    // 多研究文件目录拒绝打开。
    const std::string multiple_dir = temp_project_dir("multiple");
    TempDirCleanup multiple_cleanup{multiple_dir};
    fs::create_directories(multiple_dir);
    StudyFile other;
    other.assembly = "world.aasm";
    write_text_file((fs::path(multiple_dir) / "first.aexp").string(), encode_study_file(other));
    write_text_file((fs::path(multiple_dir) / "second.aexp").string(), encode_study_file(other));
    auto multiple = example_session();
    CHECK(!multiple->open_project(multiple_dir).ok);

    // 记录路径必须在项目内。
    const std::string outside = dir + ".arec";
    TempDirCleanup outside_cleanup{outside};
    write_text_file(outside, "x");
    auto scoped = example_session();
    CHECK(scoped->open_project(dir).ok);
    const auto refused = scoped->open_record_file(outside);
    CHECK(!refused.ok);
    CHECK(!refused.diagnostics.empty());

    // 不可读记录保留条目与原因，不影响其他记录。
    write_text_file((fs::path(dir) / project_records_directory / "bad.arec").string(), "not a record");
    bool found_bad = false;
    for (const auto& entry : scoped->records()) {
        if (entry.name != "bad.arec") continue;
        found_bad = true;
        CHECK(!entry.readable);
        CHECK(entry.problem.has_value());
    }
    CHECK(found_bad);

    // 写入失败不覆盖原文件：临时路径被占用时失败且原文件保持，临时目录不被删除。
    const std::string target_file = (fs::path(dir) / "keep.txt").string();
    write_text_file(target_file, "OLD");
    fs::create_directories(target_file + ".tmp");
    bool write_failed = false;
    try {
        write_text_file(target_file, "NEW");
    } catch (const EngineError& error) {
        write_failed = error.diagnostic().code == ErrorCode::io_failure;
    }
    CHECK(write_failed);
    CHECK(read_text_file(target_file) == "OLD");
    CHECK(fs::is_directory(target_file + ".tmp"));

    // 替换失败（目标为目录）时清理临时文件，不留下半成品。
    const std::string target_dir = temp_project_dir("write-target");
    TempDirCleanup target_cleanup{target_dir};
    fs::create_directories(target_dir);
    bool replace_failed = false;
    try {
        write_text_file(target_dir, "NEW");
    } catch (const EngineError& error) {
        replace_failed = error.diagnostic().code == ErrorCode::io_failure;
    }
    CHECK(replace_failed);
    CHECK(fs::is_directory(target_dir));
    CHECK(!fs::exists(target_dir + ".tmp"));

    // 打开缺失模块的项目：先校验后切换，失败时当前会话与运行保持不变。
    const std::string missing_dir = temp_project_dir("missing-module");
    TempDirCleanup missing_cleanup{missing_dir};
    fs::create_directories(missing_dir);
    AssemblyDefinition ghost;
    ghost.add_instance("missing.module", "ghost");
    StudyFile ghost_study;
    ghost_study.assembly = "world.aasm";
    write_text_file((fs::path(missing_dir) / "world.aasm").string(), ghost.to_json());
    write_text_file((fs::path(missing_dir) / "missing.aexp").string(), encode_study_file(ghost_study));
    auto active = example_session();
    CHECK(active->load().ok);
    const auto before = active->status();
    const auto blocked = active->open_project(missing_dir);
    CHECK(!blocked.ok);
    CHECK(!blocked.diagnostics.empty());
    CHECK(!active->project().has_value());
    CHECK(active->status().phase == before.phase);
    CHECK(active->status().run_id == before.run_id);
}

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"adapter_int64", adapter_int64},
        {"load_and_check", load_and_check},
        {"config_revision", config_revision},
        {"step_run_stop", step_run_stop},
        {"checkpoint_branches", checkpoint_branches},
        {"branch_order", branch_order},
        {"intervention_errors", intervention_errors},
        {"partial_branch_failure", partial_branch_failure},
        {"failure_paths", failure_paths},
        {"record_inputs", record_inputs},
        {"replay", replay},
        {"state_rules", state_rules},
        {"limits", limits},
        {"dual_input_replay", dual_input_replay},
        {"comparison_limits", comparison_limits},
        {"localized_texts", localized_texts},
        {"autonomous_replay", autonomous_replay},
        {"partial_input_records", partial_input_records},
        {"exception_boundaries", exception_boundaries},
        {"save_open_round_trip", save_open_round_trip},
        {"open_record_state", open_record_state},
        {"open_rejects_and_falls_back", open_rejects_and_falls_back},
        {"open_exploration_only", open_exploration_only},
        {"module_packages", module_packages},
        {"system_assembly", system_assembly},
        {"system_file", system_file},
        {"project_workflow", project_workflow},
        {"project_record_history", project_record_history},
        {"project_record_lineage", project_record_lineage},
        {"project_errors", project_errors},
    };
    if (argc != 2 || tests.count(argv[1]) == 0) {
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
