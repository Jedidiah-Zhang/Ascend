#pragma once

#include <ascend/assembly.hpp>
#include <ascend/experiment.hpp>
#include <ascend/experiment_file.hpp>
#include <ascend/module_library.hpp>
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

// 首版运行上限：单次命令的推进步数、单分支轨迹的逻辑帧总数与同组分支数。
inline constexpr std::int64_t max_steps_per_command = 10000;
inline constexpr std::int64_t max_trace_frames = 10000;
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
    std::int64_t frame = 0;
    std::optional<Diagnostic> diagnostic;  // 结构化诊断；展示时按语言渲染
};

struct TrackStatus {
    std::string label;
    std::int64_t frame = 0;
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
enum class Phase { empty, editing, runnable, stopped, failed, record };

struct Status {
    Phase phase = Phase::empty;
    std::string model_name;
    std::uint64_t draft_revision = 0;  // 配置草稿修订；编辑构造配置时递增
    std::uint64_t run_revision = 0;    // 当前运行来自的草稿修订
    std::optional<std::uint64_t> run_id;
    bool dirty = false;  // draft_revision != run_revision
    bool has_checkpoint = false;
    std::int64_t checkpoint_frame = -1;
    std::size_t recorded_inputs = 0;
    std::size_t branches = 0;
    std::vector<TrackStatus> tracks;  // 当前活动的分支轨迹；源模式为单条
    std::optional<DiagnosticView> last_failure;
};

// 一次操作的结果：是否完成（停止按请求处理不算失败）、操作后状态与诊断。
struct OperationResult {
    bool ok = false;
    std::size_t completed_steps = 0;  // 本次命令实际完成的推进步数
    bool stopped = false;             // 停止请求在当前命令的逻辑帧边界生效
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
    std::int64_t frame = 0;
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

// 某个逻辑帧的完整真值与观测详情。
struct SampleDetailView {
    bool found = false;
    std::int64_t frame = 0;
    std::vector<TruthModuleView> truth;
    std::vector<std::pair<std::string, CellView>> observations;
};

struct DiffCellView {
    std::string variable;
    std::string control;
    std::string treated;
    std::string difference;  // 干预 − 对照；不可比较时说明原因
    std::optional<std::int64_t> integer;  // 可比较时的精确差值，供时间轴曲线与游标读数使用
    bool comparable = false;
};

struct ComparisonView {
    std::vector<std::string> variables;
    struct Row {
        std::int64_t frame = 0;
        std::vector<DiffCellView> cells;
    };
    std::vector<Row> rows;                     // 两分支都有采样的共同逻辑帧
    std::vector<std::int64_t> unpaired;        // 仅一方有采样的逻辑帧
};

struct InputRecordView {
    std::int64_t frame = 0;
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
    std::int64_t frame = 0;
    std::string field;  // 模块/字段路径、观测名或主体说明
    std::string expected;
    std::string received;
};

struct ReplayReport {
    bool ok = false;
    bool complete = false;  // 全部记录采样均已核对；存在未比较范围时为 false
    std::size_t verified_frames = 0;
    std::vector<std::string> notes;
    std::optional<ReplayMismatchView> first_mismatch;
    std::optional<DiagnosticView> diagnostic;
};

// 已载入模块包的概要（ENV-18）；模块库不属于实验文件与运行状态。
struct ModulePackageView {
    std::string definition;
    std::string version;
    std::string implementation;
    bool stateless = false;
    std::string state_contract;
    std::size_t declarations = 0;
    std::size_t requirements = 0;
    std::size_t resources = 0;
};

// 会话模板：模型装配、实验规格、工厂目录、已核对实现标识、语言资源与模块库。
struct ModelTemplate {
    std::string name;
    AssemblyDefinition assembly;
    ExperimentSpec spec;
    ModuleFactoryDirectory factories;
    std::map<std::string, std::string> implementations;
    std::vector<I18nResource> resources;  // 宿主提供的语言资源（核心包与模块包）
    std::string locale = "zh-CN";
    // 模块库（可选）：宿主登记内置实现；空表示不提供模块包载入与卸载。
    std::shared_ptr<ModuleLibrary> modules;
};

// 无界面实验会话：持有配置草稿、当前运行、检查点、分支轨迹与实验记录。
// 全部命令同步执行且必须在同一宿主线程串行调用；运行中由宿主以
// should_stop 回调请求在逻辑帧边界停止，会话自身不创建线程。
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
    SampleDetailView sample_detail(std::size_t series, std::int64_t frame) const;
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
    // 单步与有限步推进；should_stop 在每个逻辑帧边界检查。
    OperationResult step();
    OperationResult run(std::int64_t steps, const std::function<bool()>& should_stop = {});
    // 在当前逻辑帧创建检查点（源模式；分支建立后不再创建）。
    OperationResult create_checkpoint();
    // 从检查点建立对照与干预分支；要求恰好两个请求且位于源模式。
    OperationResult create_branches(std::vector<BranchRequest> branches);
    // 从保存的检查点与干预重建现有分支，清空其轨迹与记录输入。
    OperationResult reset_branches();
    // 依据会话记录重建并逐逻辑帧核对；不修改当前运行与轨迹。
    ReplayReport replay();
    // 当前会话的实验文件内容（保存用）。
    ExperimentFile experiment_file() const;
    // 打开实验文件内容：核对实现标识并重建；成功接管为活动运行或进入记录态。
    OperationResult open_experiment(const ExperimentFile& file);
    // 编码当前会话的实验文件内容；编码失败返回诊断（不改变会话）。
    struct EncodeResult {
        bool ok = false;
        std::string bytes;
        std::optional<DiagnosticView> diagnostic;
    };
    EncodeResult encode_experiment() const;
    // 解码并打开实验文件字节；解析失败返回诊断且不改变会话。
    OperationResult open_experiment(const std::string& bytes);

    // ---- 模块包（ENV-18；不进入实验文件与运行状态）----
    // 已载入模块包的概要；模板未提供模块库时为空。
    std::vector<ModulePackageView> module_packages() const;
    // 载入模块包：解码、解析实现、核对清单与资源；失败给诊断且已载入列表不变。
    OperationResult load_module_package(const std::string& bytes);
    // 卸载已载入模块包；未载入或模板没有模块库时报诊断。
    OperationResult unload_module_package(const std::string& definition);

    // ---- 因果系统装配（切片 A：根作用域；嵌套组合与装配文件保存后续）----
    // 新建空因果系统：清空装配草稿与规格，放弃当前运行与记录，进入编辑态。
    OperationResult new_system(std::string name);
    // 可用模块定义（宿主内置与模块库工厂合并后按名称排序）。
    std::vector<std::string> available_modules() const;
    // 当前系统名；为空时显示模板名。
    const std::string& system_name() const noexcept { return system_name_; }
    // 增加模块实例：定义须可用，实例名非空、不含 '/' 且在根作用域唯一。
    OperationResult add_module(std::string definition, std::string instance, Config config = {});
    // 移除模块实例；引用它的连接一并移除。
    OperationResult remove_module(const std::string& instance);
    // 连接需求到提供方：同一需求已有连接时替换；两端须是草稿中的根作用域实例。
    OperationResult connect_requirement(const Reference& requirement, const Reference& provider);
    // 断开需求连接；无该连接时为无操作成功。
    OperationResult disconnect_requirement(const Reference& requirement);
    // 设置推进入口；观测由调用方选择；输入映射按公开单参数方法自动推导。
    OperationResult set_spec_advance(const Reference& advance);
    OperationResult set_spec_observations(std::vector<std::pair<std::string, Reference>> observations);
    // 打开因果系统（`.aasm` 字节）：解析为装配草稿，清空实验规格与运行/记录，进入编辑态；
    // name 非空时作为系统名，source 用于诊断定位。失败不改变会话。
    OperationResult open_system(const std::string& bytes, std::string name = {}, std::string source = {});
    // 编码当前因果系统（装配草稿）为 `.aasm` 文本；失败给诊断（不改变会话）。
    EncodeResult encode_system() const;

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
    // 用草稿配置建立源运行（初态、逻辑帧 0 与首条采样），重置记录与检查点。
    void build_source_run();
    // 重建并核对给定记录的轨迹；runs 非空时保留重建的运行（接管用）。
    ReplayReport rebuild_record(const ExperimentRecord& record,
                                std::vector<std::unique_ptr<ExperimentRun>>* runs) const;
    // 核对一条独立轨迹；输入取自轨迹自身记录；out_run 非空且核对走完时保留重建的运行（单运行接管用）。
    ReplayReport verify_standalone(const AssemblyDefinition& assembly, const ExperimentSpec& spec,
                                   const RunTrace& trace, const std::vector<DrivenInput>& driven,
                                   std::unique_ptr<ExperimentRun>* out_run = nullptr) const;
    // 单次采样比较；不同时返回首个不一致。
    std::optional<ReplayMismatchView> sample_mismatch(const std::string& label, const Sample& expected,
                                                      const Sample& actual) const;
    // 分支逐步事件的存取（活动运行或记录态）。
    const std::vector<StepEvent>* trace_events(std::size_t branch_index) const;
    // 从检查点建立分支并用新轨迹替换当前跟踪；失败时不改变会话。
    OperationResult replace_with_branches(const std::vector<BranchRequest>& branches);

    // 合并宿主内置与模块库工厂；模块包载入/卸载后刷新。
    void rebuild_factories();
    // 尝试实例化当前草稿并刷新目录；失败返回诊断且保留旧目录。
    std::vector<DiagnosticView> refresh_catalog();
    // 按目录中的公开单参数方法推导输入映射；沿用引用未变的既有名称。返回是否变化。
    bool derive_spec_inputs();
    // 观测未显式选择时，默认取根作用域全部公开量（可经 set_spec_observations 覆盖与取消）。
    bool derive_spec_observations();
    // 空会话尚未确立系统身份：编辑命令返回诊断且不改变状态。
    OperationResult reject_without_system() const;
    // 根作用域是否存在该实例。
    bool has_instance(const std::string& name) const;
    // 宿主已核对实现标识：模板登记优先，其次是已载入模块库清单中的实现；没有返回空。
    std::string implementation_of(const std::string& definition) const;
    // 草稿使用到的定义到实现标识的映射（用于实验记录）。
    std::map<std::string, std::string> used_implementations() const;

    ModelTemplate model_;
    std::shared_ptr<const AdapterRegistry> adapters_;
    TextCatalog texts_;
    std::vector<DiagnosticView> resource_diagnostics_;
    std::string locale_;

    ModuleFactoryDirectory factories_;  // 宿主内置与模块库合并后的实例化目录
    ExperimentSpec spec_;               // 当前规格草稿
    bool observations_explicit_ = false;  // 观测是否由调用方显式设置（否则按公开量推导）
    std::string system_name_;           // 当前系统名（空则显示模板名）
    AssemblyDefinition draft_;  // 当前配置草稿
    std::uint64_t draft_revision_ = 0;
    CatalogView catalog_;
    std::map<std::string, std::any> inputs_;

    ExperimentRecord record_;                     // 当前运行的记录（含分支轨迹）
    std::vector<std::unique_ptr<Track>> tracks_;  // 与 record_.branches 平行
    RunTrace exploration_trace_;                  // 建立分支前的探索轨迹（展示用）
    std::vector<StepEvent> exploration_events_;
    std::vector<std::vector<StepEvent>> record_events_;  // 记录态：与 record_.branches 平行
    std::vector<std::int64_t> record_frames_;            // 记录态：各分支实际到达的逻辑帧
    bool has_exploration_ = false;
    std::unique_ptr<Checkpoint> checkpoint_;
    std::uint64_t run_id_ = 0;
    std::uint64_t run_revision_ = 0;
    Phase phase_ = Phase::empty;
    std::optional<DiagnosticView> last_failure_;

    Status status_;
};

}  // namespace ascend::session
