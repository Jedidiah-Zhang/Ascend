#include "session_controller.hpp"

#include <QFile>
#include <QSaveFile>

#include <utility>

namespace ascend::workbench {
namespace {

// 文件 I/O 失败也进入诊断面板，便于定位（WB-16）。
session::DiagnosticView io_failure(const QString& message) {
    session::DiagnosticView view;
    view.code = ErrorCode::io_failure;
    view.message = message.toUtf8().toStdString();
    return view;
}

// 单个模块包的载入结果：失败原因与本次诊断；成功时诊断为空。
struct PackageOutcome {
    bool ok = false;
    QString detail;
    std::vector<session::DiagnosticView> diagnostics;
};

// 读取一个模块包文件并交给会话载入；读取失败与引擎拒绝都以诊断返回，不抛出。
PackageOutcome load_package_file(session::Session& session, const QString& path) {
    PackageOutcome outcome;
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        outcome.detail = file.errorString();
        outcome.diagnostics.push_back(io_failure(outcome.detail));
        return outcome;
    }
    const QByteArray data = file.readAll();
    const auto result = session.load_module_package(
        std::string(data.constData(), static_cast<std::size_t>(data.size())));
    outcome.ok = result.ok;
    if (!result.diagnostics.empty()) {
        outcome.detail = QString::fromUtf8(result.diagnostics.front().message);
        outcome.diagnostics = result.diagnostics;
    }
    return outcome;
}

}  // namespace

SessionController::SessionController(session::ModelTemplate model,
                                     std::shared_ptr<const session::AdapterRegistry> adapters,
                                     QObject* parent)
    : QObject(parent), session_(std::move(model), std::move(adapters)) {}

void SessionController::requestStop() { stop_requested_.store(true); }

void SessionController::clearStop() { stop_requested_.store(false); }

ModelSnapshot SessionController::snapshot() const {
    ModelSnapshot model;
    model.catalog = session_.catalog();
    model.spec = session_.spec();
    model.catalog_stale = model.catalog.revision != session_.status().draft_revision;
    model.instances = session_.instances();
    model.state_fields = session_.state_fields();
    model.observations = session_.observation_names();
    model.available_modules = session_.available_modules();
    const auto series = session_.series_info();
    model.series_labels.reserve(series.size());
    for (const auto& info : series) model.series_labels.push_back(info.label);
    return model;
}

void SessionController::publish(const session::OperationResult& result, bool model, TraceUpdate traces,
                                bool comparison) {
    if (!result.diagnostics.empty()) emit diagnosticsReported(result.diagnostics);
    emit statusChanged(result.status);
    if (model) {
        publishModel();
        emit recordChanged(session_.record());
    }
    if (traces == TraceUpdate::reset) {
        publishTracesReset();
    } else if (traces == TraceUpdate::deltas) {
        publishSampleDeltas();
    }
    if (comparison) emit comparisonReady(session_.comparison());
    emit operationFinished(result);
    emit busyChanged(false);
}

void SessionController::publishModel() { emit modelChanged(snapshot()); }

void SessionController::publishTracesReset() {
    std::vector<session::TrackTraceView> traces;
    const auto count = session_.series_count();
    traces.reserve(count);
    emitted_samples_.assign(count, 0);
    emitted_events_.assign(count, 0);
    for (std::size_t index = 0; index < count; ++index) {
        traces.push_back(session_.trace(index));
        emitted_samples_[index] = traces.back().samples.size();
        emitted_events_[index] = traces.back().events.size();
    }
    emit tracesReset(traces);
}

void SessionController::publishSampleDeltas() {
    const auto info = session_.series_info();
    if (emitted_samples_.size() != info.size()) {
        publishTracesReset();
        return;
    }
    for (std::size_t index = 0; index < info.size(); ++index) {
        if (info[index].samples == emitted_samples_[index] && info[index].events == emitted_events_[index]) {
            continue;
        }
        const auto trace = session_.trace_delta(index, emitted_samples_[index], emitted_events_[index]);
        emit samplesAppended(static_cast<int>(index), trace.samples, trace.events);
        emitted_samples_[index] = info[index].samples;
        emitted_events_[index] = info[index].events;
    }
}

void SessionController::load() {
    const auto result = session_.load();
    if (!reported_resources_ && !session_.resource_diagnostics().empty()) {
        reported_resources_ = true;
        emit diagnosticsReported(session_.resource_diagnostics());
    }
    publish(result, true, TraceUpdate::reset);
}

void SessionController::check() {
    const auto report = session_.check();
    emit checkFinished(report);
    publishModel();
    emit busyChanged(false);
}

void SessionController::apply() { publish(session_.apply(), true, TraceUpdate::reset); }

void SessionController::step() {
    const auto result = session_.step();
    publish(result, false, TraceUpdate::deltas, result.status.branches == session::max_branches);
}

void SessionController::runN(int steps) {
    const auto result = session_.run(steps, [this] { return stop_requested_.load(); });
    publish(result, false, TraceUpdate::deltas, result.status.branches == session::max_branches);
}

void SessionController::setInstanceConfig(const QString& scope, const QString& name, const ascend::Config& config) {
    publish(session_.set_instance_config(scope.toStdString(), name.toStdString(), config), true, TraceUpdate::none);
}

void SessionController::setInputValue(const QString& name, const std::any& value) {
    publish(session_.set_input(name.toStdString(), value), true, TraceUpdate::none);
}

void SessionController::createCheckpoint() {
    publish(session_.create_checkpoint(), true, TraceUpdate::none);
}

void SessionController::createBranches(const std::vector<session::BranchRequest>& branches) {
    const auto result = session_.create_branches(branches);
    publish(result, true, TraceUpdate::reset, result.status.branches == session::max_branches);
}

void SessionController::resetBranches() {
    const auto result = session_.reset_branches();
    publish(result, true, TraceUpdate::reset, result.status.branches == session::max_branches);
}

void SessionController::replay() {
    emit replayFinished(session_.replay());
    emit busyChanged(false);
}

void SessionController::requestComparison() { emit comparisonReady(session_.comparison()); }

void SessionController::saveExperiment(const QString& path) {
    emit busyChanged(true);
    const auto encoded = session_.encode_experiment();
    bool ok = encoded.ok;
    QString detail;
    if (encoded.diagnostic.has_value()) detail = QString::fromUtf8(encoded.diagnostic->message);
    if (ok) {
        QSaveFile file(path);
        if (!file.open(QIODevice::WriteOnly)) {
            ok = false;
            detail = file.errorString();
        } else {
            const auto size = static_cast<qint64>(encoded.bytes.size());
            if (file.write(encoded.bytes.data(), size) != size) {
                ok = false;
                detail = file.errorString();
                file.cancelWriting();
            } else if (!file.commit()) {
                ok = false;
                detail = file.errorString();
            }
        }
        if (!ok) emit diagnosticsReported({io_failure(detail)});
    }
    if (encoded.diagnostic.has_value()) emit diagnosticsReported({*encoded.diagnostic});
    emit experimentSaved(path, ok, detail);
    emit busyChanged(false);
}

void SessionController::openExperiment(const QString& path) {
    emit busyChanged(true);
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        const QString detail = file.errorString();
        emit diagnosticsReported({io_failure(detail)});
        emit experimentOpened(path, false, detail);
        emit busyChanged(false);
        return;
    }
    const QByteArray data = file.readAll();
    const auto result = session_.open_experiment(
        std::string(data.constData(), static_cast<std::size_t>(data.size())));
    QString detail;
    if (!result.diagnostics.empty()) {
        detail = QString::fromUtf8(result.diagnostics.front().message);
    }
    emit experimentOpened(path, result.ok, detail);
    publish(result, true, TraceUpdate::reset, result.status.branches == session::max_branches);
}

void SessionController::loadModulePackages(const QStringList& paths) {
    emit busyChanged(true);
    int loaded = 0;
    QStringList failures;
    std::vector<session::DiagnosticView> diagnostics;
    for (const QString& path : paths) {
        const PackageOutcome outcome = load_package_file(session_, path);
        if (outcome.ok) {
            ++loaded;
        } else if (outcome.detail.isEmpty()) {
            failures << path;
        } else {
            failures << QStringLiteral("%1：%2").arg(path, outcome.detail);
        }
        diagnostics.insert(diagnostics.end(), outcome.diagnostics.begin(), outcome.diagnostics.end());
    }
    if (!diagnostics.empty()) emit diagnosticsReported(diagnostics);
    emit modulePackagesLoaded(loaded, static_cast<int>(paths.size()) - loaded,
                              failures.join(QStringLiteral("\n")));
    emit modulePackagesChanged(session_.module_packages());
    emit busyChanged(false);
}

void SessionController::unloadModulePackage(const QString& definition) {
    emit busyChanged(true);
    const auto result = session_.unload_module_package(definition.toStdString());
    QString detail;
    if (!result.diagnostics.empty()) {
        detail = QString::fromUtf8(result.diagnostics.front().message);
        emit diagnosticsReported(result.diagnostics);
    }
    emit modulePackageUnloaded(definition, result.ok, detail);
    emit modulePackagesChanged(session_.module_packages());
    emit busyChanged(false);
}

void SessionController::requestModulePackages() {
    emit modulePackagesChanged(session_.module_packages());
}

void SessionController::requestModel() {
    publishModel();
    emit statusChanged(session_.status());
}

void SessionController::newSystem(const QString& name) {
    publish(session_.new_system(name.toStdString()), true, TraceUpdate::reset);
}

void SessionController::addModule(const QString& definition, const QString& instance) {
    publish(session_.add_module(definition.toStdString(), instance.toStdString()), true, TraceUpdate::none);
}

void SessionController::removeModule(const QString& instance) {
    publish(session_.remove_module(instance.toStdString()), true, TraceUpdate::none);
}

void SessionController::connectRequirement(const QString& requirement_module, const QString& requirement_symbol,
                                           const QString& provider_module, const QString& provider_symbol) {
    const ascend::Reference requirement{requirement_module.toStdString(), requirement_symbol.toStdString()};
    const ascend::Reference provider{provider_module.toStdString(), provider_symbol.toStdString()};
    publish(session_.connect_requirement(requirement, provider), true, TraceUpdate::none);
}

void SessionController::disconnectRequirement(const QString& requirement_module, const QString& requirement_symbol) {
    const ascend::Reference requirement{requirement_module.toStdString(), requirement_symbol.toStdString()};
    publish(session_.disconnect_requirement(requirement), true, TraceUpdate::none);
}

void SessionController::setSpecAdvance(const QString& module, const QString& symbol) {
    publish(session_.set_spec_advance(ascend::Reference{module.toStdString(), symbol.toStdString()}), true,
            TraceUpdate::none);
}

void SessionController::setSpecObservations(const QStringList& references) {
    std::vector<std::pair<std::string, ascend::Reference>> observations;
    observations.reserve(static_cast<std::size_t>(references.size()));
    for (const QString& item : references) {
        const int slash = item.lastIndexOf(QLatin1Char('/'));
        if (slash <= 0 || slash + 1 >= item.size()) continue;
        observations.emplace_back(item.toStdString(), ascend::Reference{item.left(slash).toStdString(),
                                                                        item.mid(slash + 1).toStdString()});
    }
    publish(session_.set_spec_observations(std::move(observations)), true, TraceUpdate::none);
}

void SessionController::openSystem(const QString& path, const QString& name) {
    emit busyChanged(true);
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly)) {
        const QString detail = file.errorString();
        emit diagnosticsReported({io_failure(detail)});
        emit systemOpened(path, false, detail);
        emit busyChanged(false);
        return;
    }
    const QByteArray data = file.readAll();
    const auto result = session_.open_system(std::string(data.constData(), static_cast<std::size_t>(data.size())),
                                             name.toStdString(), path.toStdString());
    QString detail;
    if (!result.diagnostics.empty()) detail = QString::fromUtf8(result.diagnostics.front().message);
    emit systemOpened(path, result.ok, detail);
    publish(result, true, TraceUpdate::reset);
}

void SessionController::saveSystem(const QString& path) {
    emit busyChanged(true);
    const auto encoded = session_.encode_system();
    QString detail;
    if (!encoded.ok) {
        if (encoded.diagnostic.has_value()) {
            detail = QString::fromUtf8(encoded.diagnostic->message);
            emit diagnosticsReported({*encoded.diagnostic});
        }
        emit systemSaved(path, false, detail);
        emit busyChanged(false);
        return;
    }
    QSaveFile file(path);
    bool ok = file.open(QIODevice::WriteOnly);
    if (ok) {
        const auto size = static_cast<qint64>(encoded.bytes.size());
        if (file.write(encoded.bytes.data(), size) != size) {
            ok = false;
            detail = file.errorString();
            file.cancelWriting();
        } else if (!file.commit()) {
            ok = false;
            detail = file.errorString();
        }
    } else {
        detail = file.errorString();
    }
    if (!ok) {
        detail = detail.isEmpty() ? file.errorString() : detail;
        emit diagnosticsReported({io_failure(detail)});
    }
    emit systemSaved(path, ok, detail);
    emit busyChanged(false);
}

void SessionController::requestRecord() { emit recordChanged(session_.record()); }

void SessionController::requestSampleDetail(int series, std::int64_t frame) {
    emit sampleDetailReady(series, frame, session_.sample_detail(static_cast<std::size_t>(series), frame));
}

void SessionController::prepareShutdown() {}

}  // namespace ascend::workbench
