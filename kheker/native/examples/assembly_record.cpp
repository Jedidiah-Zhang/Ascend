#include <ascend/assembly.hpp>

#include <array>
#include <chrono>
#include <cstdint>
#include <filesystem>
#include <iostream>
#include <memory>
#include <random>
#include <stdexcept>
#include <string>

namespace {
using Integer = std::int64_t;

// 每次运行使用独立临时目录，退出时清理，不覆盖或删除其他运行的文件。
class TempDirectory {
public:
    TempDirectory() {
        std::random_device random;
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        const auto base = std::filesystem::temp_directory_path();
        for (int attempt = 0; attempt < 100; ++attempt) {
            const auto candidate = base / ("ascend-assembly-record-" + std::to_string(stamp) + "-" +
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

ascend::MethodOptions contract(std::string name) {
    ascend::MethodOptions options;
    options.contract = std::move(name);
    return options;
}

// 工厂维护模块的内部组合与独立状态；装配方只使用公开边界。
ascend::Module accumulator(const std::string& instance, const ascend::Config& config) {
    const ascend::Config* field = config.find("initial");
    if (!field) throw std::runtime_error("initial is required");
    ascend::Module model(instance);
    model.require_value<Integer>("input", "example.scalar.v1", {{"example.accumulator", "input"}, "One-step input"});

    auto value = std::make_shared<Integer>(field->integer());
    ascend::Module state("state");
    state.add_value<Integer>("value", [value] { return *value; },
                            {{"example.accumulator", "state.value"}, "Accumulated output"}, "example.scalar.v1");
    ascend::MethodOptions write_options = contract("example.write.v1");
    write_options.description = {{"example.accumulator", "state.write"}, "Write accumulated output"};
    state.add_method<void, Integer>("write", {"next"}, [value](Integer next) { *value = next; },
                                    write_options);

    ascend::Module update("update");
    const auto input = update.require_value<Integer>("input", "example.scalar.v1",
                                                     {{"example.accumulator", "update.input"}, "Input for this step"});
    const auto old = update.require_value<Integer>("old", "example.scalar.v1",
                                                   {{"example.accumulator", "update.old"}, "Previous accumulated output"});
    const auto write = update.require_method<void, Integer>("write", "example.write.v1",
                                                            {{"example.accumulator", "update.write"}, "Write back output"});
    ascend::MethodOptions advance_options = contract("example.advance.v1");
    advance_options.description = {{"example.accumulator", "update.advance"}, "b_next = b + 2 * a"};
    update.add_method<void>("advance", {}, [input, old, write](const ascend::Context& context) {
        write(context, old.read(context) + 2 * input.read(context));
    }, advance_options);

    model.add(std::move(state));
    model.add(std::move(update));
    model.forward("input", {"update", "input"});
    model.connect({"update", "old"}, {"state", "value"});
    model.connect({"update", "write"}, {"state", "write"});
    model.export_symbol("advance", {"update", "advance"});
    model.export_symbol("output", {"state", "value"});
    return model;
}

ascend::Module stimulus(const std::string& instance, const ascend::Config& config) {
    Integer initial = 0;
    if (const ascend::Config* field = config.find("value")) initial = field->integer();
    ascend::Module result(instance);
    auto value = std::make_shared<Integer>(initial);
    result.add_value<Integer>("value", [value] { return *value; },
                             {{"example.stimulus", "value"}, "Testbench input"}, "example.scalar.v1");
    ascend::MethodOptions drive_options = contract("example.scalar-drive.v1");
    drive_options.description = {{"example.stimulus", "drive"}, "Set testbench input"};
    result.add_method<void, Integer>("drive", {"next"}, [value](Integer next) { *value = next; },
                                     drive_options);
    return result;
}

// 装配定义由普通 C++ 代码创建；保存与实例化共用同一份描述。
ascend::AssemblyDefinition definition() {
    ascend::AssemblyDefinition assembly;
    assembly.add_scope("baseline");
    assembly.add_scope("alternate");
    assembly.add_instance("example.accumulator", "model",
                          ascend::Config::object({{"initial", ascend::Config::integer(0)}}), "baseline");
    assembly.add_instance("example.accumulator", "model",
                          ascend::Config::object({{"initial", ascend::Config::integer(0)}}), "alternate");
    assembly.forward_inherited("input", {"model", "input"}, "baseline");
    assembly.forward_inherited("input", {"model", "input"}, "alternate");
    assembly.export_symbol("advance", {"model", "advance"}, "baseline");
    assembly.export_symbol("output", {"model", "output"}, "baseline");
    assembly.export_symbol("advance", {"model", "advance"}, "alternate");
    assembly.export_symbol("output", {"model", "output"}, "alternate");
    assembly.add_instance("example.stimulus", "input_baseline",
                          ascend::Config::object({{"value", ascend::Config::integer(0)}}));
    assembly.add_instance("example.stimulus", "input_alternate",
                          ascend::Config::object({{"value", ascend::Config::integer(0)}}));
    assembly.connect({"baseline", "input"}, {"input_baseline", "value"});
    assembly.connect({"alternate", "input"}, {"input_alternate", "value"});
    return assembly;
}
}  // namespace

int main() {
    try {
        const TempDirectory temp;
        const auto path = (temp.path() / "record.json").string();
        definition().save(path);

        ascend::ModuleFactoryDirectory factories;
        factories.add_definition("example.accumulator", accumulator);
        factories.add_definition("example.stimulus", stimulus);

        ascend::AssemblyDefinition loaded = ascend::AssemblyDefinition::load(path);
        ascend::Engine engine = loaded.instantiate(factories);
        for (const auto& diagnostic : engine.check()) {
            std::cerr << ascend::render_diagnostic(diagnostic) << '\n';
        }
        engine.seal();

        const auto baseline_step = engine.bind_method<void>({"baseline", "advance"});
        const auto alternate_step = engine.bind_method<void>({"alternate", "advance"});
        const auto drive_baseline = engine.bind_method<void, Integer>({"input_baseline", "drive"});
        const auto drive_alternate = engine.bind_method<void, Integer>({"input_alternate", "drive"});
        const auto baseline_output = engine.bind_value<Integer>({"baseline", "output"});
        const auto alternate_output = engine.bind_value<Integer>({"alternate", "output"});

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
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
