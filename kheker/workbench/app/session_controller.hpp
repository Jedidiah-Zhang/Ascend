#pragma once

#include "model_snapshot.hpp"

#include <ascend/session/session.hpp>

#include <QObject>
#include <QMetaType>
#include <QString>
#include <QStringList>

#include <atomic>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace ascend::workbench {

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
    // 研究项目（ENV-16）：新建/打开项目、保存项目、打开记录与请求记录清单。
    void newProject(const QString& directory, const QString& name);
    void openProject(const QString& directory);
    void saveProject();
    void openRecord(const QString& path);
    void requestRecords();
    // 模块包（ENV-18）：批量载入文件（逐个独立处理）、卸载定义标识、请求已载入概要。
    void loadModulePackages(const QStringList& paths);
    void unloadModulePackage(const QString& definition);
    void requestModulePackages();
    void requestModel();
    // 因果系统装配（切片 A）：新建、加/删模块、接线与规格，结果随模型快照发布。
    void newSystem(const QString& name);
    void addModule(const QString& definition, const QString& instance);
    void removeModule(const QString& instance);
    void connectRequirement(const QString& requirement_module, const QString& requirement_symbol,
                            const QString& provider_module, const QString& provider_symbol);
    void disconnectRequirement(const QString& requirement_module, const QString& requirement_symbol);
    void setSpecAdvance(const QString& module, const QString& symbol);
    // 观测项形如「模块/符号」；名称同文本。
    void setSpecObservations(const QStringList& references);
    // 因果系统文件（`.aasm`）：打开（可带系统名）与保存；控制器负责文件读写。
    void openSystem(const QString& path, const QString& name);
    void saveSystem(const QString& path);
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
    // 项目结果：目录、研究文件与装配文件路径；失败原因为空时表示成功。
    void projectOpened(const QString& directory, const QString& study, const QString& assembly, bool ok,
                       const QString& detail);
    // 保存结果：研究文件、装配文件与当前记录路径（无记录时为空）。
    void projectSaved(const QString& study, const QString& assembly, const QString& record, bool ok,
                      const QString& detail);
    void recordOpened(const QString& path, bool ok, const QString& detail);
    void recordsChanged(const std::vector<ascend::session::RecordEntryView>& records);
    void modulePackagesChanged(const std::vector<ascend::session::ModulePackageView>& packages);
    // 批量载入结果：成功数、失败数与失败明细（每行“路径：原因”，全部成功时为空）。
    void modulePackagesLoaded(int loaded, int failed, const QString& detail);
    void modulePackageUnloaded(const QString& definition, bool ok, const QString& detail);
    // 因果系统文件结果：路径与失败原因（成功时为空）。
    void systemOpened(const QString& path, bool ok, const QString& detail);
    void systemSaved(const QString& path, bool ok, const QString& detail);

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
Q_DECLARE_METATYPE(std::vector<ascend::session::RecordEntryView>)
Q_DECLARE_METATYPE(std::any)
