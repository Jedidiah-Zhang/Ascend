#pragma once

#include <ascend/assembly.hpp>
#include <ascend/experiment.hpp>
#include <ascend/i18n.hpp>

#include <ascend/session/adapter.hpp>

#include <cstddef>
#include <cstdint>
#include <functional>
#include <map>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace ascend::session {

// 首版运行上限：单次命令的推进步数、单分支轨迹的边界总数与同组分支数。
inline constexpr std::int64_t max_steps_per_command = 10000;
inline constexpr std::int64_t max_trace_boundaries = 10000;
inline constexpr std::size_t max_branches = 2;

// 渲染后的诊断视图：保留类别、目标、来源、字段路径与原因链。
struct DiagnosticView {
    ErrorCode code = ErrorCode::invalid_declaration;
    Reference target;
    std::string message;
    std::string source;
    std::string path;
    std::vector<DiagnosticView> causes;
};

// 检查项：检查区域、对象、结果与范围说明。
struct CheckItemView {
    std::string area;
    std::string subject;
    bool passed = false;
    std::string note;
};

struct CheckReport {
    bool passed = false;
    std::vector<CheckItemView> items;
    std::vector<DiagnosticView> diagnostics;
};

struct ParameterView {
    std::string name;
    std::string type;  // 适配器名或"未登记适配器"
    bool supported = false;
};

// 目录中的一项公开声明。
struct DeclarationView {
    Reference reference;
    SymbolKind kind = SymbolKind::value;
    std::string result_type;
    bool result_supported = false;
    std::vector<ParameterView> parameters;
    std::string description;
    std::string contract;
    std::vector<Reference> reads;
    std::vector<Reference> writes;
};

struct RequirementView {
    Reference reference;
    SymbolKind kind = SymbolKind::value;
    std::string result_type;
    std::vector<ParameterView> parameters;
    std::string contract;
    std::string description;
};

struct ConnectionView {
    Reference requirement;
    Reference provider;
    bool forwarded = false;
};

// 一个作用域/模块节点：直接子模块的公开项、需求与本作用域连接。
struct ModuleNodeView {
    std::string path;  // 完整路径；根作用域为空
    std::vector<DeclarationView> declarations;
    std::vector<RequirementView> requirements;
    std::vector<ConnectionView> connections;
};

struct CatalogView {
    std::uint64_t revision = 0;         // 目录对应的草稿修订
    std::vector<ModuleNodeView> modules;  // 按路径排序，含根
};

// 实验规格视图：推进入口、输入参数与驱动目标、观测名与符号。
struct SpecView {
    Reference advance;
    std::vector<std::pair<std::string, Reference>> inputs;
    std::vector<std::pair<std::string, Reference>> observations;
};

// 草稿中的一个实例及其构造配置。
struct InstanceView {
    std::string scope;
    std::string name;
    std::string definition;
    Config config;
};

// 检查点上可干预的状态字段。
struct StateFieldView {
    std::string module;   // 模块完整路径
    std::string field;    // 状态对象内的字段路径；空串表示整个状态对象
    std::string display;  // 当前值显示
    bool integer = false; // 是否为可编辑的 64 位整数字段
    std::int64_t value = 0;
};

// 干预的展示信息：目标、干预前显示与新值显示。
struct InterventionView {
    std::string module;
    std::string field;
    std::string previous;
    std::string replacement;
};

// 逐步结果事件；区分输入、推进与采样失败。
struct StepEvent {
    enum class Kind { completed, input_failed, advance_failed, sample_failed, stopped };
    Kind kind = Kind::completed;
    std::int64_t boundary = 0;
    std::optional<DiagnosticView> diagnostic;
};

struct TrackStatus {
    std::string label;
    std::int64_t boundary = 0;
    std::int64_t origin = 0;
    std::size_t samples = 0;
    std::size_t input_failures = 0;
    std::size_t advance_failures = 0;
    std::size_t sample_failures = 0;
    bool failed = false;
    std::vector<InterventionView> interventions;
    std::optional<DiagnosticView> last_failure;
};

// 会话状态：空、可编辑、可运行、已停止与失败；运行中由控制层标识。
enum class Phase { empty, editing, runnable, stopped, failed };

struct Status {
    Phase phase = Phase::empty;
    std::string model_name;
    std::uint64_t draft_revision = 0;  // 配置草稿修订；编辑构造配置时递增
    std::uint64_t run_revision = 0;    // 当前运行来自的草稿修订
    std::optional<std::uint64_t> run_id;
    bool dirty = false;  // draft_revision != run_revision
    bool has_checkpoint = false;
    std::int64_t checkpoint_boundary = -1;
    std::size_t recorded_inputs = 0;
    std::size_t branches = 0;
    std::vector<TrackStatus> tracks;  // 当前活动的分支轨迹；源模式为单条
    std::optional<DiagnosticView> last_failure;
};

// 一次操作的结果：是否完成（停止按请求处理不算失败）、操作后状态与诊断。
struct OperationResult {
    bool ok = false;
    std::size_t completed_steps = 0;  // 本次命令实际完成的推进步数
    bool stopped = false;             // 停止请求在当前命令的步骤边界生效
    Status status;
    std::vector<DiagnosticView> diagnostics;
};

// 采样中的一个显示单元；数值坐标可能丢失精度。
struct CellView {
    std::string display;
    bool numeric = false;
    double value = 0.0;
    bool exact = false;
    std::optional<std::int64_t> integer;  // 精确 64 位整数视图；不支持时为空
};

struct SampleView {
    std::int64_t boundary = 0;
    std::vector<CellView> observations;  // 按规格观测顺序
};

// 一条轨迹的展示视图；探索轨迹与各分支使用同一结构。
struct TrackTraceView {
    std::string label;
    std::int64_t origin = 0;
    std::vector<std::string> variables;  // 观测名
    std::vector<SampleView> samples;
    std::vector<InterventionView> interventions;
    std::vector<StepEvent> events;
};

struct TruthModuleView {
    std::string path;
    std::string contract;
    bool stateless = false;
    std::vector<std::pair<std::string, std::string>> fields;  // 叶字段路径与显示值
};

// 某个边界的完整真值与观测详情。
struct SampleDetailView {
    bool found = false;
    std::int64_t boundary = 0;
    std::vector<TruthModuleView> truth;
    std::vector<std::pair<std::string, CellView>> observations;
};

struct DiffCellView {
    std::string variable;
    std::string control;
    std::string treated;
    std::string difference;  // 干预 − 对照；不可比较时说明原因
    bool comparable = false;
};

struct ComparisonView {
    std::vector<std::string> variables;
    struct Row {
        std::int64_t boundary = 0;
        std::vector<DiffCellView> cells;
    };
    std::vector<Row> rows;                     // 两分支都有采样的共同边界
    std::vector<std::int64_t> unpaired;        // 仅一方有采样的边界
};

struct InputRecordView {
    std::int64_t boundary = 0;
    std::string name;
    std::string value;
};

struct RecordView {
    std::size_t branches = 0;
    std::size_t inputs = 0;
    std::size_t failures = 0;
    std::vector<InputRecordView> input_list;
};

// 建立分支的请求：标签与有序干预；首版要求恰好两个（对照与干预）。
struct BranchRequest {
    std::string label;
    std::vector<Intervention> interventions;
};

struct ReplayMismatchView {
    std::string branch;
    std::int64_t boundary = 0;
    std::string field;  // 模块/字段路径、观测名或输入边界说明
    std::string expected;
    std::string received;
};

struct ReplayReport {
    bool ok = false;
    bool complete = false;  // 全部记录采样均已核对；存在未比较范围时为 false
    std::size_t verified_boundaries = 0;
    std::vector<std::string> notes;
    std::optional<ReplayMismatchView> first_mismatch;
    std::optional<DiagnosticView> diagnostic;
};

// 会话模板：模型装配、实验规格、工厂目录、已核对实现标识与语言资源。
struct ModelTemplate {
    std::string name;
    AssemblyDefinition assembly;
    ExperimentSpec spec;
    ModuleFactoryDirectory factories;
    std::map<std::string, std::string> implementations;
    std::vector<I18nResource> resources;  // 宿主提供的语言资源（核心包与模块包）
    std::string locale = "zh-CN";
};

// 无界面实验会话：持有配置草稿、当前运行、检查点、分支轨迹与实验记录。
// 全部命令同步执行且必须在同一宿主线程串行调用；运行中由宿主以
// should_stop 回调请求在步骤边界停止，会话自身不创建线程。
// 操作不会抛出引擎异常；失败以 OperationResult 与诊断返回。
class Session {
public:
    Session(ModelTemplate model, std::shared_ptr<const AdapterRegistry> adapters);
    ~Session();
    Session(const Session&) = delete;
    Session& operator=(const Session&) = delete;
    Session(Session&&) = delete;
    Session& operator=(Session&&) = delete;

    // ---- 状态与视图（只读；当前运行的目录在检查或应用后更新）----
    const Status& status() const noexcept { return status_; }
    std::string model_name() const { return model_.name; }
    const AdapterRegistry& adapters() const noexcept { return *adapters_; }
    // 构造时加载语言资源的失败诊断。
    const std::vector<DiagnosticView>& resource_diagnostics() const noexcept {
        return resource_diagnostics_;
    }
    CatalogView catalog() const;
    SpecView spec() const;
    std::vector<InstanceView> instances() const;
    std::vector<std::string> input_names() const;
    std::vector<std::string> observation_names() const;
    // 检查点上可干预的状态字段；无检查点时为空。
    std::vector<StateFieldView> state_fields() const;
    // 轨迹序列：源模式为单条运行轨迹；分支模式先探索轨迹、再对照与干预。
    std::size_t series_count() const;
    TrackTraceView trace(std::size_t series) const;
    // 轨迹元数据与计数：避免为读取标签、起点与数量而复制整条轨迹。
    struct SeriesInfo {
        std::string label;
        std::int64_t origin = 0;
        std::size_t samples = 0;
        std::size_t events = 0;
    };
    std::vector<SeriesInfo> series_info() const;
    // 轨迹尾部增量：从指定的采样与事件下标开始构造视图。
    TrackTraceView trace_delta(std::size_t series, std::size_t sample_begin,
                               std::size_t event_begin) const;
    SampleDetailView sample_detail(std::size_t series, std::int64_t boundary) const;
    ComparisonView comparison() const;
    RecordView record() const;

    // ---- 命令 ----
    // 打开示例：重置草稿与运行，检查并在通过时建立初态运行。
    OperationResult load();
    // 只检查当前草稿，不改变运行与轨迹。
    CheckReport check();
    // 应用草稿配置并重建运行；失败时保留原运行与轨迹。
    OperationResult apply();
    // 替换实例构造配置；内容变化时草稿修订递增。
    OperationResult set_instance_config(const std::string& scope, const std::string& name, Config config);
    // 设置共同输入值；不改变草稿修订，实际值随记录保存。
    OperationResult set_input(const std::string& name, std::any value);
    // 单步与有限步推进；should_stop 在每个步骤边界检查。
    OperationResult step();
    OperationResult run(std::int64_t steps, const std::function<bool()>& should_stop = {});
    // 在当前边界创建检查点（源模式；分支建立后不再创建）。
    OperationResult create_checkpoint();
    // 从检查点建立对照与干预分支；要求恰好两个请求且位于源模式。
    OperationResult create_branches(std::vector<BranchRequest> branches);
    // 从保存的检查点与干预重建现有分支，清空其轨迹与记录输入。
    OperationResult reset_branches();
    // 依据会话记录重建并逐边界核对；不修改当前运行与轨迹。
    ReplayReport replay();

private:
    struct Track;

    Status make_status() const;
    // 状态不允许操作：返回当前状态、诊断与 ok=false，不改变会话。
    OperationResult reject(TextRef text) const;
    // 命令结束：刷新缓存状态并返回结果。
    OperationResult finish(bool ok, std::vector<DiagnosticView> diagnostics = {},
                           std::size_t completed_steps = 0, bool stopped = false);
    // 执行工厂、装配、规格与适配检查；成功实例化时刷新目录。
    CheckReport run_checks();
    // 用草稿配置建立源运行（初态、边界 0 与首条采样），重置记录与检查点。
    void build_source_run();
    // 从检查点建立分支并用新轨迹替换当前跟踪；失败时不改变会话。
    OperationResult replace_with_branches(const std::vector<BranchRequest>& branches);

    ModelTemplate model_;
    std::shared_ptr<const AdapterRegistry> adapters_;
    TextCatalog texts_;
    std::vector<DiagnosticView> resource_diagnostics_;
    std::string locale_;

    AssemblyDefinition draft_;  // 当前配置草稿
    std::uint64_t draft_revision_ = 0;
    CatalogView catalog_;
    std::map<std::string, std::any> inputs_;

    ExperimentRecord record_;                     // 当前运行的记录（含分支轨迹）
    std::vector<std::unique_ptr<Track>> tracks_;  // 与 record_.branches 平行
    RunTrace exploration_trace_;                  // 建立分支前的探索轨迹（展示用）
    std::vector<StepEvent> exploration_events_;
    bool has_exploration_ = false;
    std::unique_ptr<Checkpoint> checkpoint_;
    std::uint64_t run_id_ = 0;
    std::uint64_t run_revision_ = 0;
    Phase phase_ = Phase::empty;
    std::optional<DiagnosticView> last_failure_;

    Status status_;
};

}  // namespace ascend::session
