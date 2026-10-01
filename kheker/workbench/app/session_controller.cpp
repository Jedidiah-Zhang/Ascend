#include "session_controller.hpp"

#include <utility>

namespace ascend::workbench {

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
    const auto series = session_.series_info();
    model.series_labels.reserve(series.size());
    for (const auto& info : series) model.series_labels.push_back(info.label);
    model.record = session_.record();
    return model;
}

void SessionController::publish(const session::OperationResult& result, bool model, TraceUpdate traces) {
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
    publish(result, false, TraceUpdate::deltas);
    if (result.status.branches == session::max_branches) emit comparisonReady(session_.comparison());
}

void SessionController::runN(int steps) {
    const auto result = session_.run(steps, [this] { return stop_requested_.load(); });
    publish(result, false, TraceUpdate::deltas);
    if (result.status.branches == session::max_branches) emit comparisonReady(session_.comparison());
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
    publish(result, true, TraceUpdate::reset);
    if (result.status.branches == session::max_branches) emit comparisonReady(session_.comparison());
}

void SessionController::resetBranches() {
    const auto result = session_.reset_branches();
    publish(result, true, TraceUpdate::reset);
    if (result.status.branches == session::max_branches) emit comparisonReady(session_.comparison());
}

void SessionController::replay() {
    emit replayFinished(session_.replay());
    emit busyChanged(false);
}

void SessionController::requestComparison() { emit comparisonReady(session_.comparison()); }

void SessionController::requestRecord() { emit recordChanged(session_.record()); }

void SessionController::requestSampleDetail(int series, std::int64_t frame) {
    emit sampleDetailReady(series, frame, session_.sample_detail(static_cast<std::size_t>(series), frame));
}

void SessionController::prepareShutdown() {}

}  // namespace ascend::workbench
