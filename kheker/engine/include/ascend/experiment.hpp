#pragma once

#include <ascend/assembly.hpp>

#include <any>
#include <cstdint>
#include <map>
#include <string>
#include <utility>
#include <vector>

namespace ascend {

// 实验规格：宿主驱动的推进入口、逐步输入入口与观测映射。
// advance 是无参数顶层方法；每次成功推进记为一个边界。
// inputs 与 observations 的名称分别在其列表内唯一；引用的存在与签名在相应操作时核对。
struct ExperimentSpec {
    Reference advance;
    std::vector<std::pair<std::string, Reference>> inputs;
    std::vector<std::pair<std::string, Reference>> observations;
};

// 宿主传给主体的数据：只含边界与规格声明的观测，不含运行句柄或真值。
struct Observation {
    std::int64_t boundary = 0;
    std::map<std::string, std::any> values;
};

// 一次边界采样：边界编号（已完成的推进次数）、完整真值与按规格读取的观测。
// 该类型属于研究入口并携带真值；主体观测入口不得直接取得 Sample，只能获得规定的观测数据。
struct Sample {
    std::int64_t boundary = 0;
    StateSnapshot truth;
    std::map<std::string, std::any> observations;
};

// 可恢复的检查点：边界编号与完整真值；恢复后从该边界继续演化。
// 边界由调用方维护且必须非负，恢复时核对。
struct Checkpoint {
    std::int64_t boundary = 0;
    StateSnapshot truth;
};

// 一次性外部状态干预：把模块状态对象中指定字段改为给定值。
// module 是模块完整路径；field 是状态对象内的对象字段路径（以 '/' 分隔，
// 如 "x"、"position/x"），空串表示整个状态对象。
struct Intervention {
    std::string module;
    std::string field;
    Config value;
};

// 对检查点施加干预并返回修改后的副本；原检查点不受影响。
// 同一路径的多次修改按请求顺序应用，后者覆盖前者。
// 目标模块缺失、目标模块无状态、路径经过非对象或字段不存在时报 invalid_intervention。
Checkpoint apply_interventions(const Checkpoint& checkpoint, const std::vector<Intervention>& requests);

// 一次推进失败的结果：失败时停留的边界（不前进）与诊断；宿主可继续运行或从检查点重建。
struct StepFailure {
    std::int64_t boundary = 0;
    Diagnostic diagnostic;
};

// 输入记录：boundary 是驱动前的边界；同边界的多项输入按列表顺序驱动。
// 原生参数按值保存；含共享资源的参数由宿主保证记录稳定。
struct DrivenInput {
    std::int64_t boundary = 0;
    std::string name;
    std::vector<std::any> arguments;
};

// 一条运行轨迹的记录：分支标签、共同起点、本分支干预、本分支实际成功驱动的
// 输入（含未完成步骤的部分成功）、逐步采样与推进失败。
struct RunTrace {
    std::string label;
    Checkpoint origin;
    std::vector<Intervention> interventions;
    std::vector<Sample> samples;
    std::vector<StepFailure> failures;
    std::vector<DrivenInput> driven;
};

// 宿主维护的进程内实验记录：运行条件与各分支轨迹在同一个对象中关联。
// implementations 将模块定义标识映射到宿主核对过的实现标识；不自动推断代码版本。
// 本类型不自动记录调用，宿主负责记录与实际执行一致；自定义值不承诺深复制共享资源。
struct ExperimentRecord {
    AssemblyDefinition assembly;
    ExperimentSpec spec;
    std::map<std::string, std::string> implementations;
    std::vector<DrivenInput> inputs;
    std::vector<RunTrace> branches;
};

// 宿主显式驱动的实验运行：由装配定义实例化、检查并封闭，按规格推进与采样。
// 运行只通过公开接口驱动环境；完整真值通过状态快照另行捕获。
// 操作期间重入同一运行的 drive/step/observe/sample/checkpoint/restore 报 execution_failed。
class ExperimentRun {
public:
    // 实例化并封闭装配，解析推进入口；规格结构非法或推进入口不可用时报告诊断。
    ExperimentRun(const AssemblyDefinition& definition, const ModuleFactoryDirectory& factories,
                  ExperimentSpec spec, std::string label = {});

    const std::string& label() const noexcept { return label_; }
    std::int64_t boundary() const noexcept { return boundary_; }

    // 在推进前驱动一项命名输入；参数类型与数量由引擎调用检查核对。
    void drive(const std::string& input, std::vector<std::any> arguments = {});
    // 显式推进一次；推进入口成功返回后边界加一，失败时边界不变。
    // 失败前已完成的状态变化按引擎约定保留，不回滚；计数达上限时在执行模型前拒绝。
    void step();
    // 只读取命名观测，不捕获完整状态；宿主将返回值交给主体。
    Observation observe() const;
    // 采样当前边界：完整真值 + 按规格读取的观测。
    Sample sample() const;
    // 当前边界的检查点。
    Checkpoint checkpoint() const;
    // 从检查点恢复；成功后边界为检查点边界，失败时边界不变。
    void restore(const Checkpoint& checkpoint);

    // 封闭后的只读运行目录视图：作用域路径（含根，空字符串）按路径排序；
    // 声明、需求与连接来自实际运行装配，不执行 getter 或方法，不暴露可变引擎。
    std::vector<std::string> scopes() const;
    std::vector<Declaration> catalog(const std::string& scope = {}) const;
    std::vector<Requirement> requirements(const std::string& scope = {}) const;
    std::vector<Connection> connections(const std::string& scope = {}) const;
    // 实验规格按构造时校验后的结果保存。
    const ExperimentSpec& spec() const noexcept { return spec_; }

private:
    class Operation;
    std::map<std::string, std::any> read_observations() const;
    ExperimentSpec spec_;
    Engine engine_;
    MethodBinding<void> advance_;
    std::string label_;
    std::int64_t boundary_ = 0;
    mutable bool operating_ = false;
};

}  // namespace ascend
