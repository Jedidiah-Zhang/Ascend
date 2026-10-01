#pragma once

#include <ascend/session/session.hpp>

#include <QObject>
#include <QMetaType>
#include <QString>

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace ascend::workbench {

// 一次命令后随界面发布的值模型：目录、草稿实例、输入、状态字段、序列元数据与记录。
struct ModelSnapshot {
    session::CatalogView catalog;
    session::SpecView spec;
    bool catalog_stale = false;
    std::vector<session::InstanceView> instances;
    std::vector<session::StateFieldView> state_fields;
    std::vector<std::string> observations;
    std::vector<std::string> series_labels;
    session::RecordView record;
};

// 专属执行线程中的会话控制层：界面线程按队列提交命令，结果以值快照发布。
// 所有会话命令在持有对象所属线程串行执行；停止请求由任意线程原子设置。
class SessionController : public QObject {
    Q_OBJECT

public:
    SessionController(session::ModelTemplate model, std::shared_ptr<const session::AdapterRegistry> adapters,
                      QObject* parent = nullptr);

    // 线程安全：请求在逻辑帧边界停止当前运行命令。
    void requestStop();
    // 线程安全：提交新的运行命令前清除上一次的停止请求。
    void clearStop();

public slots:
    void load();
    void check();
    void apply();
    void step();
    void runN(int steps);
    void setInstanceConfig(const QString& scope, const QString& name, const ascend::Config& config);
    void setInputValue(const QString& name, const std::any& value);
    void createCheckpoint();
    void createBranches(const std::vector<ascend::session::BranchRequest>& branches);
    void resetBranches();
    void replay();
    void requestComparison();
    // 保存当前实验到文件；打开实验文件（读取、解码、核对与接管/记录态）。
    void saveExperiment(const QString& path);
    void openExperiment(const QString& path);
    void requestRecord();
    void requestSampleDetail(int series, std::int64_t frame);
    // 空操作：关闭窗口时用阻塞调用等待当前命令结束。
    void prepareShutdown();

signals:
    void busyChanged(bool busy);
    void operationFinished(const ascend::session::OperationResult& result);
    void statusChanged(const ascend::session::Status& status);
    void modelChanged(const ascend::workbench::ModelSnapshot& model);
    void tracesReset(const std::vector<ascend::session::TrackTraceView>& traces);
    void samplesAppended(int series, const std::vector<ascend::session::SampleView>& samples,
                         const std::vector<ascend::session::StepEvent>& events);
    void comparisonReady(const ascend::session::ComparisonView& comparison);
    void checkFinished(const ascend::session::CheckReport& report);
    void replayFinished(const ascend::session::ReplayReport& report);
    void recordChanged(const ascend::session::RecordView& record);
    void sampleDetailReady(int series, std::int64_t frame, const ascend::session::SampleDetailView& detail);
    void diagnosticsReported(const std::vector<ascend::session::DiagnosticView>& diagnostics);
    void experimentSaved(const QString& path, bool ok);
    void experimentOpened(const QString& path, bool ok);

private:
    enum class TraceUpdate { none, reset, deltas };

    ModelSnapshot snapshot() const;
    void publish(const session::OperationResult& result, bool model, TraceUpdate traces,
                 bool comparison = false);
    void publishModel();
    void publishTracesReset();
    void publishSampleDeltas();

    session::Session session_;
    std::atomic<bool> stop_requested_{false};
    std::vector<std::size_t> emitted_samples_;
    std::vector<std::size_t> emitted_events_;
    bool reported_resources_ = false;
};

}  // namespace ascend::workbench

Q_DECLARE_METATYPE(ascend::workbench::ModelSnapshot)
Q_DECLARE_METATYPE(ascend::session::OperationResult)
Q_DECLARE_METATYPE(ascend::session::Status)
Q_DECLARE_METATYPE(ascend::session::CheckReport)
Q_DECLARE_METATYPE(ascend::session::ReplayReport)
Q_DECLARE_METATYPE(ascend::session::ComparisonView)
Q_DECLARE_METATYPE(ascend::session::TrackTraceView)
Q_DECLARE_METATYPE(ascend::session::SampleDetailView)
Q_DECLARE_METATYPE(ascend::session::DiagnosticView)
Q_DECLARE_METATYPE(ascend::session::BranchRequest)
Q_DECLARE_METATYPE(std::vector<ascend::session::TrackTraceView>)
Q_DECLARE_METATYPE(std::vector<ascend::session::SampleView>)
Q_DECLARE_METATYPE(std::vector<ascend::session::StepEvent>)
Q_DECLARE_METATYPE(std::vector<ascend::session::BranchRequest>)
Q_DECLARE_METATYPE(std::vector<ascend::session::DiagnosticView>)
Q_DECLARE_METATYPE(std::any)
