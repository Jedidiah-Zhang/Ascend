#include <ascend/session/session.hpp>

#include <algorithm>
#include <iomanip>
#include <limits>
#include <locale>
#include <sstream>
#include <stdexcept>
#include <utility>

namespace ascend::session {
namespace {

DiagnosticView view_of(const Diagnostic& diagnostic, const TextCatalog& texts, const std::string& locale) {
    DiagnosticView view;
    view.code = diagnostic.code;
    view.target = diagnostic.target;
    view.message = render_text(diagnostic.text, &texts, locale);
    view.source = diagnostic.source;
    view.path = diagnostic.path;
    if (diagnostic.cause) view.causes.push_back(view_of(*diagnostic.cause, texts, locale));
    return view;
}

TextRef session_text(const char* key, const char* fallback, TextRef::Arguments arguments = {}) {
    return TextRef(TextKey{session_text_domain, key}, fallback, std::move(arguments));
}

// 按语言资源渲染会话文本；默认模板为英文，缺失翻译时回退。
std::string render_session(const TextCatalog& texts, const std::string& locale, const char* key,
                           const char* fallback, TextRef::Arguments arguments = {}) {
    return render_text(session_text(key, fallback, std::move(arguments)), &texts, locale);
}

Diagnostic make_diagnostic(ErrorCode code, Reference target, const char* key, const char* fallback,
                           TextRef::Arguments arguments = {}) {
    return Diagnostic{code, std::move(target), session_text(key, fallback, std::move(arguments)), {}, {}, {}};
}

std::string number_text(double value) {
    std::ostringstream stream;
    stream.imbue(std::locale::classic());
    stream << std::setprecision(17) << value;
    return stream.str();
}

// 结构化配置值的展示文本；整数保持精确十进制。
std::string config_display(const Config& value) {
    switch (value.kind()) {
        case Config::Kind::null_value: return "null";
        case Config::Kind::boolean: return value.boolean() ? "true" : "false";
        case Config::Kind::integer: return std::to_string(value.integer());
        case Config::Kind::number: return number_text(value.number());
        case Config::Kind::string: return value.string();
        case Config::Kind::array: {
            std::string text = "[";
            for (std::size_t index = 0; index < value.elements().size(); ++index) {
                if (index != 0) text += ", ";
                text += config_display(value.elements()[index]);
            }
            return text + "]";
        }
        case Config::Kind::object: {
            std::string text = "{";
            for (std::size_t index = 0; index < value.members().size(); ++index) {
                if (index != 0) text += ", ";
                text += value.members()[index].first + ": " + config_display(value.members()[index].second);
            }
            return text + "}";
        }
    }
    return "？";
}

void flatten_state(const Config& value, const std::string& prefix,
                   std::vector<std::pair<std::string, std::string>>& out) {
    if (value.kind() == Config::Kind::object && !value.members().empty()) {
        for (const auto& member : value.members()) {
            flatten_state(member.second, prefix.empty() ? member.first : prefix + "/" + member.first, out);
        }
        return;
    }
    out.emplace_back(prefix, config_display(value));
}

std::string type_display(std::type_index type, const AdapterRegistry& adapters, const TextCatalog& texts,
                         const std::string& locale) {
    if (type == typeid(void)) return "void";
    const ValueAdapter* adapter = adapters.find(type);
    return adapter ? adapter->name()
                   : render_session(texts, locale, "session.value.unsupported", "No adapter registered");
}

bool type_supported(std::type_index type, const AdapterRegistry& adapters) {
    return type == typeid(void) || adapters.find(type) != nullptr;
}

ParameterView parameter_view(const Parameter& parameter, const AdapterRegistry& adapters,
                             const TextCatalog& texts, const std::string& locale) {
    ParameterView view;
    view.name = parameter.name;
    view.type = type_display(parameter.type, adapters, texts, locale);
    view.supported = type_supported(parameter.type, adapters);
    return view;
}

DeclarationView declaration_view(const Declaration& declaration, const AdapterRegistry& adapters,
                                 const TextCatalog& texts, const std::string& locale) {
    DeclarationView view;
    view.reference = declaration.reference;
    view.kind = declaration.kind;
    view.result_type = type_display(declaration.result_type, adapters, texts, locale);
    view.result_supported = type_supported(declaration.result_type, adapters);
    for (const auto& parameter : declaration.parameters) {
        view.parameters.push_back(parameter_view(parameter, adapters, texts, locale));
    }
    view.description = render_text(declaration.description, &texts, locale);
    view.contract = declaration.contract;
    view.reads = declaration.reads;
    view.writes = declaration.writes;
    return view;
}

RequirementView requirement_view(const Requirement& requirement, const AdapterRegistry& adapters,
                                 const TextCatalog& texts, const std::string& locale) {
    RequirementView view;
    view.reference = requirement.reference;
    view.kind = requirement.kind;
    view.result_type = type_display(requirement.result_type, adapters, texts, locale);
    for (const auto& parameter : requirement.parameters) {
        ParameterView entry;
        entry.type = type_display(parameter, adapters, texts, locale);
        entry.supported = type_supported(parameter, adapters);
        view.parameters.push_back(std::move(entry));
    }
    view.contract = requirement.contract;
    view.description = render_text(requirement.description, &texts, locale);
    return view;
}

// 引擎与实验运行暴露相同的只读目录查询，共用同一转换。
template <class Source>
CatalogView collect_catalog(const Source& source, const AdapterRegistry& adapters, const TextCatalog& texts,
                            const std::string& locale, std::uint64_t revision) {
    CatalogView view;
    view.revision = revision;
    for (const auto& scope : source.scopes()) {
        ModuleNodeView node;
        node.path = scope;
        for (const auto& declaration : source.catalog(scope)) {
            node.declarations.push_back(declaration_view(declaration, adapters, texts, locale));
        }
        for (const auto& requirement : source.requirements(scope)) {
            node.requirements.push_back(requirement_view(requirement, adapters, texts, locale));
        }
        for (const auto& connection : source.connections(scope)) {
            node.connections.push_back(ConnectionView{connection.requirement, connection.provider, connection.forwarded});
        }
        view.modules.push_back(std::move(node));
    }
    return view;
}

const DeclarationView* root_declaration(const CatalogView& catalog, const Reference& reference) {
    for (const auto& module : catalog.modules) {
        if (!module.path.empty()) continue;
        for (const auto& declaration : module.declarations) {
            if (declaration.reference == reference) return &declaration;
        }
        return nullptr;
    }
    return nullptr;
}

CellView cell_of(const std::any& value, const AdapterRegistry& adapters, const TextCatalog& texts,
                 const std::string& locale) {
    CellView cell;
    const ValueAdapter* adapter = value.has_value() ? adapters.find(value.type()) : nullptr;
    if (!adapter) {
        cell.display = render_session(texts, locale, "session.value.unsupported", "No adapter registered");
        return cell;
    }
    cell.display = adapter->format(value);
    if (const auto numeric = adapter->number(value)) {
        cell.numeric = true;
        cell.value = numeric->value;
        cell.exact = numeric->exact;
    }
    cell.integer = adapter->integer(value);
    return cell;
}

std::string argument_display(const std::any& value, const AdapterRegistry& adapters, const TextCatalog& texts,
                             const std::string& locale) {
    if (!value.has_value()) {
        return render_session(texts, locale, "session.value.empty", "Empty value");
    }
    const ValueAdapter* adapter = adapters.find(value.type());
    return adapter ? adapter->format(value)
                   : render_session(texts, locale, "session.value.unsupported", "No adapter registered");
}

bool observations_equal(const std::map<std::string, std::any>& left,
                        const std::map<std::string, std::any>& right, const AdapterRegistry& adapters) {
    if (left.size() != right.size()) return false;
    for (const auto& item : left) {
        const auto found = right.find(item.first);
        if (found == right.end()) return false;
        const ValueAdapter* adapter = adapters.find(item.second.type());
        if (!adapter || !adapter->equal(item.second, found->second)) return false;
    }
    return true;
}

std::string first_difference(const Config& left, const Config& right, const std::string& prefix,
                             const TextCatalog& texts, const std::string& locale) {
    if (left.kind() != right.kind()) return prefix.empty() ? render_session(texts, locale, "session.replay.field.type", "(type)") : prefix;
    switch (left.kind()) {
        case Config::Kind::object: {
            for (const auto& member : left.members()) {
                const std::string path = prefix.empty() ? member.first : prefix + "/" + member.first;
                const Config* other = right.find(member.first);
                if (!other) return path;
                const auto difference = first_difference(member.second, *other, path, texts, locale);
                if (!difference.empty()) return difference;
            }
            if (right.members().size() != left.members().size()) {
            return prefix.empty() ? render_session(texts, locale, "session.replay.field.object", "(object)") : prefix;
        }
            return {};
        }
        case Config::Kind::array: {
            if (left.elements().size() != right.elements().size()) {
            return prefix.empty() ? render_session(texts, locale, "session.replay.field.array", "(array)") : prefix;
        }
            for (std::size_t index = 0; index < left.elements().size(); ++index) {
                const auto difference =
                    first_difference(left.elements()[index], right.elements()[index],
                                     prefix + "[" + std::to_string(index) + "]", texts, locale);
                if (!difference.empty()) return difference;
            }
            return {};
        }
        default:
            return left == right ? std::string{} : (prefix.empty() ? render_session(texts, locale, "session.replay.field.value", "(value)") : prefix);
    }
}

std::optional<std::int64_t> checked_subtract(std::int64_t left, std::int64_t right) {
    if (right > 0 && left < std::numeric_limits<std::int64_t>::min() + right) return std::nullopt;
    if (right < 0 && left > std::numeric_limits<std::int64_t>::max() + right) return std::nullopt;
    return left - right;
}

std::vector<std::string> split_field(const std::string& field) {
    std::vector<std::string> segments;
    std::size_t begin = 0;
    while (true) {
        const auto end = field.find('/', begin);
        segments.push_back(field.substr(begin, end == std::string::npos ? end : end - begin));
        if (end == std::string::npos) break;
        begin = end + 1;
    }
    return segments;
}

std::string state_value_display(const Checkpoint& origin, const Intervention& request, const TextCatalog& texts,
                                const std::string& locale) {
    const auto module = std::find_if(origin.truth.modules.begin(), origin.truth.modules.end(),
                                     [&](const ModuleState& item) { return item.path == request.module; });
    if (module == origin.truth.modules.end()) {
        return render_session(texts, locale, "session.state.module_missing", "(module not found)");
    }
    if (request.field.empty()) return config_display(module->state);
    const Config* current = &module->state;
    for (const auto& segment : split_field(request.field)) {
        if (current->kind() != Config::Kind::object) {
            return render_session(texts, locale, "session.state.path_not_object", "(path crosses a non-object)");
        }
        const Config* next = current->find(segment);
        if (!next) return render_session(texts, locale, "session.state.field_missing", "(field not found)");
        current = next;
    }
    return config_display(*current);
}

// 干预目标字段的现有值；模块/字段不存在或路径非法时返回空指针。
const Config* state_value(const Checkpoint& origin, const Intervention& request) {
    const auto module = std::find_if(origin.truth.modules.begin(), origin.truth.modules.end(),
                                     [&](const ModuleState& item) { return item.path == request.module; });
    if (module == origin.truth.modules.end()) return nullptr;
    if (request.field.empty()) return &module->state;
    const Config* current = &module->state;
    for (const auto& segment : split_field(request.field)) {
        if (current->kind() != Config::Kind::object) return nullptr;
        current = current->find(segment);
        if (!current) return nullptr;
    }
    return current;
}

std::vector<InterventionView> intervention_views(const Checkpoint& origin,
                                                 const std::vector<Intervention>& interventions,
                                                 const TextCatalog& texts, const std::string& locale) {
    std::vector<InterventionView> result;
    for (const auto& request : interventions) {
        result.push_back(InterventionView{request.module, request.field,
                                          state_value_display(origin, request, texts, locale),
                                          config_display(request.value)});
    }
    return result;
}

std::string reference_text(const Reference& reference) {
    if (reference.module.empty()) return reference.symbol;
    if (reference.symbol.empty()) return reference.module;
    return reference.module + "/" + reference.symbol;
}

}  // namespace

// 一条活动的轨迹：运行对象、记录下标与逐步事件。
struct Session::Track {
    std::unique_ptr<ExperimentRun> run;
    std::size_t index = 0;  // record_.branches 下标
    std::vector<StepEvent> events;
    std::size_t input_failures = 0;
    std::size_t advance_failures = 0;
    std::size_t sample_failures = 0;
    bool failed = false;
};

Session::Session(ModelTemplate model, std::shared_ptr<const AdapterRegistry> adapters)
    : model_(std::move(model)), adapters_(std::move(adapters)) {
    if (!adapters_) throw std::invalid_argument("session requires an adapter registry");
    locale_ = model_.locale.empty() ? "zh-CN" : model_.locale;
    texts_.set_default_locale(locale_);
    std::vector<I18nResource> loaded;
    for (const auto& resource : model_.resources) {
        const bool duplicate = std::any_of(loaded.begin(), loaded.end(), [&](const I18nResource& item) {
            return item.domain == resource.domain && item.path == resource.path;
        });
        if (duplicate) continue;
        try {
            texts_.load(resource);
            loaded.push_back(resource);
        } catch (const EngineError& error) {
            resource_diagnostics_.push_back(view_of(error.diagnostic(), texts_, locale_));
        }
    }
    status_ = make_status();
}

Session::~Session() = default;

// ---------------------------------------------------------------------------
// 状态与视图

Status Session::make_status() const {
    Status status;
    status.phase = phase_;
    status.model_name = model_.name;
    status.draft_revision = draft_revision_;
    status.run_revision = run_revision_;
    if (run_id_ != 0) status.run_id = run_id_;
    status.dirty = draft_revision_ != run_revision_;
    status.has_checkpoint = checkpoint_ != nullptr;
    status.checkpoint_frame = checkpoint_ ? checkpoint_->frame : -1;
    status.recorded_inputs = record_.inputs.size();
    status.branches = record_.branches.size();
    status.last_failure = last_failure_;
    for (const auto& track : tracks_) {
        const auto& trace = record_.branches[track->index];
        TrackStatus view;
        view.label = trace.label;
        view.frame = track->run ? track->run->frame() : trace.origin.frame;
        view.origin = trace.origin.frame;
        view.samples = trace.samples.size();
        view.input_failures = track->input_failures;
        view.advance_failures = track->advance_failures;
        view.sample_failures = track->sample_failures;
        view.failed = track->failed;
        view.interventions = intervention_views(trace.origin, trace.interventions, texts_, locale_);
        if (track->failed && !track->events.empty()) {
            view.last_failure = track->events.back().diagnostic;
        }
        status.tracks.push_back(std::move(view));
    }
    return status;
}

CatalogView Session::catalog() const { return catalog_; }

SpecView Session::spec() const {
    SpecView view;
    view.advance = model_.spec.advance;
    view.inputs = model_.spec.inputs;
    view.observations = model_.spec.observations;
    return view;
}

std::vector<InstanceView> Session::instances() const {
    std::vector<InstanceView> result;
    if (draft_revision_ == 0) return result;
    for (const auto& scope : draft_.scopes()) {
        for (const auto& instance : draft_.instances(scope)) {
            result.push_back(InstanceView{scope, instance.name, instance.definition, instance.config});
        }
    }
    return result;
}

std::vector<std::string> Session::input_names() const {
    std::vector<std::string> result;
    for (const auto& entry : model_.spec.inputs) result.push_back(entry.first);
    return result;
}

std::vector<std::string> Session::observation_names() const {
    std::vector<std::string> result;
    for (const auto& entry : model_.spec.observations) result.push_back(entry.first);
    return result;
}


std::vector<StateFieldView> Session::state_fields() const {
    std::vector<StateFieldView> result;
    if (!checkpoint_) return result;
    for (const auto& module : checkpoint_->truth.modules) {
        if (module.stateless) continue;
        if (module.state.kind() == Config::Kind::object && !module.state.members().empty()) {
            std::vector<std::pair<std::string, std::string>> leaves;
            flatten_state(module.state, {}, leaves);
            for (auto& leaf : leaves) {
                StateFieldView view;
                view.module = module.path;
                view.field = leaf.first;
                view.display = leaf.second;
                const Config* current = &module.state;
                for (const auto& segment : split_field(leaf.first)) {
                    if (!current) break;
                    current = current->find(segment);
                }
                if (current && current->kind() == Config::Kind::integer) {
                    view.integer = true;
                    view.value = current->integer();
                }
                result.push_back(std::move(view));
            }
            continue;
        }
        StateFieldView view;
        view.module = module.path;
        view.display = config_display(module.state);
        if (module.state.kind() == Config::Kind::integer) {
            view.integer = true;
            view.value = module.state.integer();
        }
        result.push_back(std::move(view));
    }
    return result;
}

std::size_t Session::series_count() const {
    return tracks_.size() + (has_exploration_ ? 1 : 0);
}

TrackTraceView Session::trace(std::size_t series) const { return trace_delta(series, 0, 0); }

std::vector<Session::SeriesInfo> Session::series_info() const {
    std::vector<SeriesInfo> result;
    if (has_exploration_) {
        result.push_back(SeriesInfo{exploration_trace_.label, exploration_trace_.origin.frame,
                                    exploration_trace_.samples.size(), exploration_events_.size()});
    }
    for (const auto& track : tracks_) {
        const auto& trace = record_.branches[track->index];
        result.push_back(SeriesInfo{trace.label, trace.origin.frame, trace.samples.size(),
                                    track->events.size()});
    }
    return result;
}

TrackTraceView Session::trace_delta(std::size_t series, std::size_t sample_begin,
                                    std::size_t event_begin) const {
    TrackTraceView view;
    const RunTrace* found = nullptr;
    const std::vector<StepEvent>* events = nullptr;
    if (has_exploration_ && series == 0) {
        found = &exploration_trace_;
        events = &exploration_events_;
    } else {
        const std::size_t index = series - (has_exploration_ ? 1 : 0);
        if (index >= record_.branches.size()) return view;
        found = &record_.branches[index];
        events = &tracks_[index]->events;
    }
    view.label = found->label;
    view.origin = found->origin.frame;
    view.variables = observation_names();
    view.interventions = intervention_views(found->origin, found->interventions, texts_, locale_);
    for (std::size_t position = sample_begin; position < found->samples.size(); ++position) {
        const auto& sample = found->samples[position];
        SampleView entry;
        entry.frame = sample.frame;
        for (const auto& name : view.variables) {
            const auto value = sample.observations.find(name);
            entry.observations.push_back(value == sample.observations.end()
                                             ? CellView{render_session(texts_, locale_, "session.observation.missing", "(missing)"), false, 0.0, false, std::nullopt}
                                             : cell_of(value->second, *adapters_, texts_, locale_));
        }
        view.samples.push_back(std::move(entry));
    }
    for (std::size_t position = event_begin; position < events->size(); ++position) {
        view.events.push_back((*events)[position]);
    }
    return view;
}

SampleDetailView Session::sample_detail(std::size_t series, std::int64_t frame) const {
    SampleDetailView view;
    const RunTrace* found = nullptr;
    if (has_exploration_ && series == 0) {
        found = &exploration_trace_;
    } else {
        const std::size_t index = series - (has_exploration_ ? 1 : 0);
        if (index >= record_.branches.size()) return view;
        found = &record_.branches[index];
    }
    const auto sample = std::find_if(found->samples.begin(), found->samples.end(),
                                     [&](const Sample& item) { return item.frame == frame; });
    if (sample == found->samples.end()) return view;
    view.found = true;
    view.frame = sample->frame;
    for (const auto& module : sample->truth.modules) {
        TruthModuleView module_view;
        module_view.path = module.path;
        module_view.contract = module.contract;
        module_view.stateless = module.stateless;
        if (!module.stateless) flatten_state(module.state, {}, module_view.fields);
        view.truth.push_back(std::move(module_view));
    }
    for (const auto& entry : sample->observations) {
        view.observations.emplace_back(entry.first, cell_of(entry.second, *adapters_, texts_, locale_));
    }
    return view;
}

ComparisonView Session::comparison() const {
    ComparisonView view;
    view.variables = observation_names();
    if (tracks_.size() != 2) return view;
    const auto& control = record_.branches[tracks_[0]->index];
    const auto& treated = record_.branches[tracks_[1]->index];
    std::vector<std::int64_t> control_frames;
    for (const auto& sample : control.samples) control_frames.push_back(sample.frame);
    std::vector<std::int64_t> treated_frames;
    for (const auto& sample : treated.samples) treated_frames.push_back(sample.frame);
    std::vector<std::int64_t> all = control_frames;
    all.insert(all.end(), treated_frames.begin(), treated_frames.end());
    std::sort(all.begin(), all.end());
    all.erase(std::unique(all.begin(), all.end()), all.end());
    for (const auto frame : all) {
        const auto control_sample = std::find_if(control.samples.begin(), control.samples.end(),
                                                 [&](const Sample& item) { return item.frame == frame; });
        const auto treated_sample = std::find_if(treated.samples.begin(), treated.samples.end(),
                                                 [&](const Sample& item) { return item.frame == frame; });
        if (control_sample == control.samples.end() || treated_sample == treated.samples.end()) {
            view.unpaired.push_back(frame);
            continue;
        }
        ComparisonView::Row row;
        row.frame = frame;
        for (const auto& name : view.variables) {
            DiffCellView cell;
            cell.variable = name;
            const auto control_value = control_sample->observations.find(name);
            const auto treated_value = treated_sample->observations.find(name);
            if (control_value == control_sample->observations.end() ||
                treated_value == treated_sample->observations.end()) {
                cell.control = render_session(texts_, locale_, "session.observation.missing", "(missing)");
                cell.treated = render_session(texts_, locale_, "session.observation.missing", "(missing)");
                cell.difference = render_session(texts_, locale_, "session.comparison.observation_missing", "Missing observation");
                row.cells.push_back(std::move(cell));
                continue;
            }
            cell.control = cell_of(control_value->second, *adapters_, texts_, locale_).display;
            cell.treated = cell_of(treated_value->second, *adapters_, texts_, locale_).display;
            const auto* left = std::any_cast<std::int64_t>(&treated_value->second);
            const auto* right = std::any_cast<std::int64_t>(&control_value->second);
            if (left != nullptr && right != nullptr) {
                if (const auto difference = checked_subtract(*left, *right)) {
                    cell.difference = std::to_string(*difference);
                    cell.comparable = true;
                } else {
                    cell.difference = render_session(texts_, locale_, "session.comparison.overflow", "Difference exceeds 64 bits");
                }
            } else {
                cell.difference = render_session(texts_, locale_, "session.comparison.incomparable", "Types are not comparable");
            }
            row.cells.push_back(std::move(cell));
        }
        view.rows.push_back(std::move(row));
    }
    return view;
}

RecordView Session::record() const {
    RecordView view;
    view.branches = record_.branches.size();
    view.inputs = record_.inputs.size();
    for (const auto& trace : record_.branches) view.failures += trace.failures.size();
    for (const auto& input : record_.inputs) {
        std::string value;
        for (std::size_t index = 0; index < input.arguments.size(); ++index) {
            if (index != 0) value += ", ";
            value += argument_display(input.arguments[index], *adapters_, texts_, locale_);
        }
        view.input_list.push_back(InputRecordView{input.frame, input.name, value});
    }
    return view;
}

// ---------------------------------------------------------------------------
// 命令

OperationResult Session::reject(TextRef text) const {
    OperationResult result;
    result.ok = false;
    result.status = make_status();
    result.diagnostics.push_back(
        view_of(Diagnostic{ErrorCode::invalid_declaration, {}, std::move(text), {}, {}, {}}, texts_, locale_));
    return result;
}

OperationResult Session::finish(bool ok, std::vector<DiagnosticView> diagnostics, std::size_t completed_steps,
                                bool stopped) {
    status_ = make_status();
    OperationResult result;
    result.ok = ok;
    result.completed_steps = completed_steps;
    result.stopped = stopped;
    result.status = status_;
    result.diagnostics = std::move(diagnostics);
    return result;
}

CheckReport Session::run_checks() {
    CheckReport report;
    Engine engine;
    try {
        engine = draft_.instantiate(model_.factories);
    } catch (const EngineError& error) {
        const auto view = view_of(error.diagnostic(), texts_, locale_);
        report.items.push_back(CheckItemView{
            render_session(texts_, locale_, "session.check.area.assembly", "Factory and assembly"),
            render_session(texts_, locale_, "session.check.subject.instantiate", "Instantiation"), false,
            view.message});
        report.diagnostics.push_back(view);
        return report;
    }
    const auto diagnostics = engine.check();
    if (!diagnostics.empty()) {
        for (const auto& diagnostic : diagnostics) report.diagnostics.push_back(view_of(diagnostic, texts_, locale_));
        report.items.push_back(CheckItemView{
            render_session(texts_, locale_, "session.check.area.assembly", "Factory and assembly"),
            render_session(texts_, locale_, "session.check.subject.assembly", "Assembly check"), false,
            render_session(texts_, locale_, "session.check.note.diagnostics", "{count} diagnostics",
                           {{"count", std::to_string(diagnostics.size())}})});
        catalog_ = collect_catalog(engine, *adapters_, texts_, locale_, draft_revision_);
        return report;
    }
    report.items.push_back(CheckItemView{
        render_session(texts_, locale_, "session.check.area.assembly", "Factory and assembly"),
        render_session(texts_, locale_, "session.check.subject.instantiate_assembly",
                       "Instantiation and assembly check"),
        true,
        render_session(texts_, locale_, "session.check.note.scopes", "{count} scopes",
                       {{"count", std::to_string(engine.scopes().size())}})});
    catalog_ = collect_catalog(engine, *adapters_, texts_, locale_, draft_revision_);

    bool passed = true;

    // 推进入口：顶层公开方法、无参数、无返回值。
    const auto* advance = root_declaration(catalog_, model_.spec.advance);
    const auto advance_subject = render_session(
        texts_, locale_, "session.check.subject.advance", "Advance entry {reference}",
        {{"reference", reference_text(model_.spec.advance)}});
    const auto spec_area = render_session(texts_, locale_, "session.check.area.spec", "Experiment specification");
    if (!advance) {
        passed = false;
        report.items.push_back(CheckItemView{spec_area, advance_subject, false,
                                             render_session(texts_, locale_, "session.check.note.entry_missing",
                                                            "No top-level declaration found")});
        report.diagnostics.push_back(view_of(
            make_diagnostic(ErrorCode::missing_symbol, model_.spec.advance, "session.entry.missing",
                            "Experiment specification entry not found: {reference}",
                            {{"reference", reference_text(model_.spec.advance)}}),
            texts_, locale_));
    } else if (advance->kind != SymbolKind::method || advance->result_type != "void" ||
               !advance->parameters.empty()) {
        passed = false;
        report.items.push_back(CheckItemView{
            spec_area, advance_subject, false,
            render_session(texts_, locale_, "session.check.note.advance_required",
                           "Must be a top-level public method with no parameters and no result")});
    } else {
        report.items.push_back(CheckItemView{
            spec_area, advance_subject, true,
            render_session(texts_, locale_, "session.check.note.advance_ok",
                           "Public method with no parameters and no result")});
    }

    const auto adapter_area = render_session(texts_, locale_, "session.check.area.adapters", "Type adapters");
    auto check_entry = [&](bool method, const std::string& name, const Reference& reference) {
        const auto subject = render_session(
            texts_, locale_,
            method ? "session.check.subject.input" : "session.check.subject.observation",
            method ? "Input {name} ({reference})" : "Observation {name} ({reference})",
            {{"name", name}, {"reference", reference_text(reference)}});
        const auto* declaration = root_declaration(catalog_, reference);
        if (!declaration) {
            passed = false;
            report.items.push_back(CheckItemView{spec_area, subject, false,
                                                 render_session(texts_, locale_,
                                                                "session.check.note.entry_missing",
                                                                "No top-level declaration found")});
            report.diagnostics.push_back(view_of(
                make_diagnostic(ErrorCode::missing_symbol, reference, "session.entry.missing",
                                "Experiment specification entry not found: {reference}",
                                {{"reference", reference_text(reference)}}),
                texts_, locale_));
            return;
        }
        if (declaration->kind != (method ? SymbolKind::method : SymbolKind::value)) {
            passed = false;
            report.items.push_back(CheckItemView{
                spec_area, subject, false,
                render_session(texts_, locale_,
                               method ? "session.check.note.method_required" : "session.check.note.value_required",
                               method ? "Must be a public method" : "Must be a public value")});
            return;
        }
        if (method) {
            if (declaration->parameters.size() != 1) {
                passed = false;
                report.items.push_back(CheckItemView{
                    spec_area, subject, false,
                    render_session(texts_, locale_, "session.check.note.single_parameter",
                                   "The first version supports single-argument input methods only")});
                return;
            }
            if (!declaration->parameters.front().supported) {
                passed = false;
                report.items.push_back(CheckItemView{
                    adapter_area, subject, false,
                    render_session(texts_, locale_, "session.check.note.adapter_missing",
                                   "Native type {type} has no registered adapter",
                                   {{"type", declaration->parameters.front().type}})});
                report.diagnostics.push_back(view_of(
                    make_diagnostic(ErrorCode::type_mismatch, reference, "session.adapter.missing",
                                    "No value adapter for the native type used by {reference}",
                                    {{"reference", reference_text(reference)}}),
                    texts_, locale_));
                return;
            }
            report.items.push_back(CheckItemView{
                spec_area, subject, true,
                render_session(texts_, locale_, "session.check.note.parameter_ok", "{type} parameter",
                               {{"type", declaration->parameters.front().type}})});
            return;
        }
        if (!declaration->result_supported) {
            passed = false;
            report.items.push_back(CheckItemView{
                adapter_area, subject, false,
                render_session(texts_, locale_, "session.check.note.adapter_missing",
                               "Native type {type} has no registered adapter",
                               {{"type", declaration->result_type}})});
            report.diagnostics.push_back(view_of(
                make_diagnostic(ErrorCode::type_mismatch, reference, "session.adapter.missing",
                                "No value adapter for the native type used by {reference}",
                                {{"reference", reference_text(reference)}}),
                texts_, locale_));
            return;
        }
        report.items.push_back(CheckItemView{
            spec_area, subject, true,
            render_session(texts_, locale_, "session.check.note.value_ok", "{type} public value",
                           {{"type", declaration->result_type}})});
    };

    for (const auto& input : model_.spec.inputs) check_entry(true, input.first, input.second);
    for (const auto& observation : model_.spec.observations) check_entry(false, observation.first, observation.second);

    // 实现标识：宿主必须为草稿使用的每个模块定义提供已核对的实现标识。
    std::vector<std::string> definitions;
    for (const auto& scope : draft_.scopes()) {
        for (const auto& instance : draft_.instances(scope)) {
            if (std::find(definitions.begin(), definitions.end(), instance.definition) == definitions.end()) {
                definitions.push_back(instance.definition);
            }
        }
    }
    for (const auto& definition : definitions) {
        const auto found = model_.implementations.find(definition);
        if (found == model_.implementations.end()) {
            passed = false;
            report.items.push_back(CheckItemView{
                render_session(texts_, locale_, "session.check.area.implementations", "Implementation identifiers"),
                definition, false,
                render_session(texts_, locale_, "session.check.note.implementation_missing",
                               "Missing host-checked implementation identifier")});
            report.diagnostics.push_back(view_of(
                make_diagnostic(ErrorCode::invalid_config, {definition, {}}, "session.implementation.missing",
                                "No host-checked implementation identifier for definition '{definition}'",
                                {{"definition", definition}}),
                texts_, locale_));
        } else {
            report.items.push_back(CheckItemView{
                render_session(texts_, locale_, "session.check.area.implementations", "Implementation identifiers"),
                definition, true, found->second});
        }
    }

    report.passed = passed;
    report.items.push_back(CheckItemView{
        render_session(texts_, locale_, "session.check.area.scope", "Check scope"),
        render_session(texts_, locale_, "session.check.subject.scope", "Workbench checks"), passed,
        render_session(texts_, locale_, "session.check.note.scope",
                       "Covers instantiation, assembly wiring, the advance entry, input/observation signatures "
                       "and type adapters; not a proof of implementation correctness")});
    return report;
}

void Session::build_source_run() {
    const auto source_label = render_session(texts_, locale_, "session.branch.source", "Run");
    auto run = std::make_unique<ExperimentRun>(draft_, model_.factories, model_.spec, source_label);
    auto origin = run->checkpoint();
    RunTrace trace{source_label, origin, {}, {run->sample()}, {}, {}};
    record_ = ExperimentRecord{draft_, model_.spec, model_.implementations, {}, {std::move(trace)}};
    auto track = std::make_unique<Track>();
    track->index = 0;
    track->run = std::move(run);
    tracks_.clear();
    tracks_.push_back(std::move(track));
    exploration_trace_ = {};
    exploration_events_.clear();
    has_exploration_ = false;
    checkpoint_.reset();
    last_failure_.reset();
    ++run_id_;
    run_revision_ = draft_revision_;
}

OperationResult Session::load() {
    draft_ = model_.assembly;
    draft_revision_ = 1;
    catalog_ = {};
    inputs_.clear();
    record_ = {};
    tracks_.clear();
    exploration_trace_ = {};
    exploration_events_.clear();
    has_exploration_ = false;
    checkpoint_.reset();
    run_id_ = 0;
    run_revision_ = 0;
    last_failure_.reset();
    phase_ = Phase::editing;

    const auto report = run_checks();
    if (!report.passed) return finish(false, report.diagnostics);
    try {
        build_source_run();
    } catch (const EngineError& error) {
        return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
    }
    phase_ = Phase::runnable;
    return finish(true);
}

CheckReport Session::check() {
    CheckReport report;
    if (phase_ == Phase::empty) {
        report.items.push_back(CheckItemView{
            render_session(texts_, locale_, "session.check.area.session", "Session state"),
            render_session(texts_, locale_, "session.check.subject.empty", "Empty session"), false,
            render_session(texts_, locale_, "session.check.note.empty", "Open the example before running checks")});
        report.diagnostics.push_back(view_of(
            make_diagnostic(ErrorCode::invalid_declaration, {}, "session.empty",
                            "Open the example before this operation"),
            texts_, locale_));
        return report;
    }
    return run_checks();
}

OperationResult Session::apply() {
    if (phase_ == Phase::empty) {
        return reject(session_text("session.empty", "Open the example before this operation"));
    }
    const auto report = run_checks();
    if (!report.passed) return finish(false, report.diagnostics);
    try {
        build_source_run();
    } catch (const EngineError& error) {
        return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
    }
    phase_ = Phase::runnable;
    return finish(true);
}

OperationResult Session::set_instance_config(const std::string& scope, const std::string& name, Config config) {
    if (phase_ == Phase::empty) {
        return reject(session_text("session.empty", "Open the example before this operation"));
    }
    try {
        for (const auto& instance : draft_.instances(scope)) {
            if (instance.name != name) continue;
            if (instance.config == config) return finish(true);
            draft_.set_instance_config(name, std::move(config), scope);
            ++draft_revision_;
            return finish(true);
        }
        const std::string path = scope.empty() ? name : scope + "/" + name;
        return finish(false, {view_of(make_diagnostic(ErrorCode::invalid_assembly, {path, {}},
                                                      "session.instance.missing",
                                                      "No instance '{name}' in scope '{scope}'",
                                                      {{"name", name}, {"scope", scope}}),
                                       texts_, locale_)});
    } catch (const EngineError& error) {
        return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
    }
}

OperationResult Session::set_input(const std::string& name, std::any value) {
    if (phase_ == Phase::empty) {
        return reject(session_text("session.empty", "Open the example before this operation"));
    }
    const auto found = std::find_if(model_.spec.inputs.begin(), model_.spec.inputs.end(),
                                    [&](const auto& item) { return item.first == name; });
    if (found == model_.spec.inputs.end()) {
        return finish(false, {view_of(make_diagnostic(ErrorCode::invalid_declaration, {},
                                                      "session.input.unknown", "No input named '{name}'",
                                                      {{"name", name}}),
                                       texts_, locale_)});
    }
    if (!value.has_value()) {
        return finish(false, {view_of(make_diagnostic(ErrorCode::type_mismatch, found->second, "session.input.empty",
                                                      "Input '{name}' needs a value", {{"name", name}}),
                                       texts_, locale_)});
    }
    if (const auto* declaration = root_declaration(catalog_, found->second)) {
        if (!declaration->parameters.empty() && declaration->parameters.front().supported &&
            declaration->parameters.front().type != type_display(value.type(), *adapters_, texts_, locale_)) {
            return finish(false, {view_of(make_diagnostic(ErrorCode::type_mismatch, found->second,
                                                          "session.input.type",
                                                          "Input '{name}' expects type {type}",
                                                          {{"name", name},
                                                           {"type", declaration->parameters.front().type}}),
                                           texts_, locale_)});
        }
    }
    inputs_[name] = std::move(value);
    return finish(true);
}

OperationResult Session::step() { return run(1, {}); }

OperationResult Session::run(std::int64_t steps, const std::function<bool()>& should_stop) {
    if (phase_ != Phase::runnable && phase_ != Phase::stopped) {
        return reject(session_text("session.state.rejected",
                                   "The session state does not allow this operation; check, apply or rebuild first",
                                   {{"operation", render_session(texts_, locale_, "session.operation.advance", "advance")}}));
    }
    if (steps < 1 || steps > max_steps_per_command) {
        return reject(session_text("session.steps.range", "Steps must be between 1 and {limit}",
                                   {{"limit", std::to_string(max_steps_per_command)}}));
    }
    std::int64_t frame_cap = 0;
    for (const auto& track : tracks_) frame_cap = std::max(frame_cap, track->run->frame());
    if (frame_cap + steps > max_trace_frames) {
        return reject(session_text("session.frame.limit", "Trace frame limit {limit} would be exceeded",
                                   {{"limit", std::to_string(max_trace_frames)}}));
    }

    std::vector<DiagnosticView> diagnostics;
    bool failed_cycle = false;
    bool stopped = false;
    std::size_t completed_cycles = 0;
    for (std::int64_t step = 0; step < steps; ++step) {
        if (should_stop && should_stop()) {
            stopped = true;
            for (const auto& track : tracks_) {
                track->events.push_back(StepEvent{StepEvent::Kind::stopped, track->run->frame(), std::nullopt});
            }
            break;
        }
        const auto frame = tracks_.front()->run->frame();
        // 驱动阶段：逐输入记录本分支实际成功执行的输入；共同输入随后由各分支结果整理。
        std::vector<char> common(model_.spec.inputs.size(), 1);
        bool any_driving = false;
        for (const auto& track : tracks_) {
            if (track->failed) continue;
            any_driving = true;
            bool branch_failed = false;
            for (std::size_t index = 0; index < model_.spec.inputs.size(); ++index) {
                const auto& input = model_.spec.inputs[index];
                const auto found = inputs_.find(input.first);
                if (found == inputs_.end()) continue;  // 未驱动：使用模块自身配置值
                if (branch_failed) {
                    common[index] = 0;
                    continue;
                }
                try {
                    track->run->drive(input.first, {found->second});
                    record_.branches[track->index].driven.push_back(
                        DrivenInput{frame, input.first, {found->second}});
                } catch (const EngineError& error) {
                    branch_failed = true;
                    common[index] = 0;
                    track->failed = true;
                    ++track->input_failures;
                    failed_cycle = true;
                    auto view = view_of(error.diagnostic(), texts_, locale_);
                    track->events.push_back(StepEvent{StepEvent::Kind::input_failed, frame, view});
                    diagnostics.push_back(std::move(view));
                }
            }
        }
        for (const auto& track : tracks_) {
            if (track->failed) continue;
            try {
                track->run->step();
            } catch (const EngineError& error) {
                track->failed = true;
                ++track->advance_failures;
                failed_cycle = true;
                record_.branches[track->index].failures.push_back(StepFailure{frame, error.diagnostic()});
                auto view = view_of(error.diagnostic(), texts_, locale_);
                track->events.push_back(StepEvent{StepEvent::Kind::advance_failed, frame, view});
                diagnostics.push_back(std::move(view));
                continue;
            }
            try {
                record_.branches[track->index].samples.push_back(track->run->sample());
            } catch (const EngineError& error) {
                track->failed = true;
                ++track->sample_failures;
                failed_cycle = true;
                auto view = view_of(error.diagnostic(), texts_, locale_);
                track->events.push_back(
                    StepEvent{StepEvent::Kind::sample_failed, track->run->frame(), view});
                diagnostics.push_back(std::move(view));
                continue;
            }
            track->events.push_back(StepEvent{StepEvent::Kind::completed, track->run->frame(), std::nullopt});
        }
        if (!failed_cycle) ++completed_cycles;
        // 共同输入：所有参与分支都成功驱动的输入（按规格顺序）；部分成功的输入保留在分支轨迹中。
        if (any_driving) {
            for (std::size_t index = 0; index < model_.spec.inputs.size(); ++index) {
                if (common[index] == 0) continue;
                const auto found = inputs_.find(model_.spec.inputs[index].first);
                if (found == inputs_.end()) continue;
                record_.inputs.push_back(
                    DrivenInput{frame, model_.spec.inputs[index].first, {found->second}});
            }
        }
        if (failed_cycle) break;
    }

    if (failed_cycle) {
        phase_ = Phase::failed;
        last_failure_ = diagnostics.empty() ? std::optional<DiagnosticView>{} : diagnostics.front();
        return finish(false, std::move(diagnostics), completed_cycles, false);
    }
    phase_ = stopped ? Phase::stopped : Phase::runnable;
    return finish(true, {}, completed_cycles, stopped);
}

OperationResult Session::create_checkpoint() {
    if (phase_ != Phase::runnable && phase_ != Phase::stopped) {
        return reject(session_text("session.state.rejected",
                                   "The session state does not allow this operation; check, apply or rebuild first",
                                   {{"operation", render_session(texts_, locale_, "session.operation.checkpoint", "create a checkpoint")}}));
    }
    if (has_exploration_ || tracks_.size() != 1) {
        return reject(session_text("session.checkpoint.after_branch",
                                   "A checkpoint can only be created before branches are built"));
    }
    try {
        checkpoint_ = std::make_unique<Checkpoint>(tracks_.front()->run->checkpoint());
    } catch (const EngineError& error) {
        return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
    }
    return finish(true);
}

OperationResult Session::replace_with_branches(const std::vector<BranchRequest>& branches) {
    std::vector<std::unique_ptr<ExperimentRun>> runs;
    std::vector<RunTrace> traces;
    for (const auto& branch : branches) {
        for (const auto& request : branch.interventions) {
            const Config* current = state_value(*checkpoint_, request);
            if (current && current->kind() != request.value.kind()) {
                const std::string target =
                    request.field.empty() ? request.module : request.module + "#" + request.field;
                return finish(false, {view_of(
                    make_diagnostic(ErrorCode::invalid_intervention, {request.module, request.field},
                                    "session.intervention.kind",
                                    "Intervention value kind does not match the current field value: {target}",
                                    {{"target", target}}),
                    texts_, locale_)});
            }
        }
        Checkpoint start;
        try {
            start = apply_interventions(*checkpoint_, branch.interventions);
        } catch (const EngineError& error) {
            return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
        }
        std::unique_ptr<ExperimentRun> run;
        try {
            run = std::make_unique<ExperimentRun>(record_.assembly, model_.factories, model_.spec, branch.label);
            run->restore(start);
        } catch (const EngineError& error) {
            return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
        }
        RunTrace trace{branch.label, *checkpoint_, branch.interventions, {}, {}, {}};
        try {
            trace.samples.push_back(run->sample());
        } catch (const EngineError& error) {
            return finish(false, {view_of(error.diagnostic(), texts_, locale_)});
        }
        traces.push_back(std::move(trace));
        runs.push_back(std::move(run));
    }
    if (!has_exploration_ && tracks_.size() == 1) {
        exploration_trace_ = record_.branches.front();
        exploration_events_ = tracks_.front()->events;
        has_exploration_ = true;
    }
    record_.inputs.clear();
    record_.branches = std::move(traces);
    std::vector<std::unique_ptr<Track>> tracks;
    for (std::size_t index = 0; index < runs.size(); ++index) {
        auto track = std::make_unique<Track>();
        track->index = index;
        track->run = std::move(runs[index]);
        tracks.push_back(std::move(track));
    }
    tracks_ = std::move(tracks);
    last_failure_.reset();
    return finish(true);
}

OperationResult Session::create_branches(std::vector<BranchRequest> branches) {
    if (phase_ != Phase::runnable && phase_ != Phase::stopped) {
        return reject(session_text("session.state.rejected",
                                   "The session state does not allow this operation; check, apply or rebuild first",
                                   {{"operation", render_session(texts_, locale_, "session.operation.branch", "build branches")}}));
    }
    if (!checkpoint_) {
        return reject(session_text("session.checkpoint.missing", "Create a checkpoint before building branches"));
    }
    if (has_exploration_ || tracks_.size() != 1) {
        return reject(session_text("session.branch.exists",
                                   "Branches already exist; rebuild them from the checkpoint instead"));
    }
    if (branches.size() != max_branches) {
        return reject(session_text("session.branch.count",
                                   "The first version builds exactly two branches from one checkpoint",
                                   {{"limit", std::to_string(max_branches)}}));
    }
    for (std::size_t index = 0; index < branches.size(); ++index) {
        if (branches[index].label.empty()) branches[index].label = index == 0
                ? render_session(texts_, locale_, "session.branch.control", "Control")
                : render_session(texts_, locale_, "session.branch.treated", "Treated");
    }
    return replace_with_branches(branches);
}

OperationResult Session::reset_branches() {
    if (!has_exploration_ || record_.branches.size() != max_branches) {
        return reject(session_text("session.branch.required", "This session has no branch comparison to rebuild"));
    }
    if (!checkpoint_) {
        return reject(session_text("session.checkpoint.missing", "Create a checkpoint before building branches"));
    }
    std::vector<BranchRequest> definitions;
    for (const auto& trace : record_.branches) definitions.push_back(BranchRequest{trace.label, trace.interventions});
    const auto result = replace_with_branches(definitions);
    if (!result.ok) {
        phase_ = Phase::failed;
        last_failure_ = result.diagnostics.empty() ? std::optional<DiagnosticView>{} : result.diagnostics.front();
        status_ = make_status();
        return OperationResult{false, result.completed_steps, result.stopped, status_, result.diagnostics};
    }
    phase_ = Phase::runnable;
    return finish(true);
}

ReplayReport Session::replay() {
    ReplayReport report;
    if (record_.branches.empty()) {
        report.diagnostic = view_of(make_diagnostic(ErrorCode::invalid_declaration, {}, "session.replay.empty",
                                                    "The session record has no trace to replay"),
                                    texts_, locale_);
        return report;
    }
    for (const auto& item : record_.implementations) {
        const auto found = model_.implementations.find(item.first);
        if (found == model_.implementations.end() || found->second != item.second) {
            report.diagnostic = view_of(
                make_diagnostic(ErrorCode::invalid_config, {}, "session.replay.implementation",
                                "Implementation identifier for '{definition}' does not match the host-checked one",
                                {{"definition", item.first}}),
                texts_, locale_);
            return report;
        }
    }
    for (const auto& scope : record_.assembly.scopes()) {
        for (const auto& instance : record_.assembly.instances(scope)) {
            if (record_.implementations.count(instance.definition) == 0) {
                report.diagnostic = view_of(
                    make_diagnostic(ErrorCode::invalid_config, {instance.definition, {}},
                                    "session.implementation.missing",
                                    "No host-checked implementation identifier for definition '{definition}'",
                                    {{"definition", instance.definition}}),
                    texts_, locale_);
                return report;
            }
        }
    }

    const auto mismatch = [&](const std::string& branch, std::int64_t frame, std::string field,
                              std::string expected, std::string received) {
        report.ok = false;
        report.first_mismatch =
            ReplayMismatchView{std::move(branch), frame, std::move(field), std::move(expected), std::move(received)};
    };

    try {
        const auto loaded = AssemblyDefinition::parse(record_.assembly.to_json());
        for (const auto& trace : record_.branches) {
            ExperimentRun run(loaded, model_.factories, record_.spec, trace.label);
            run.restore(apply_interventions(trace.origin, trace.interventions));
            std::size_t index = 0;
            const auto verify = [&]() -> bool {
                if (index >= trace.samples.size()) return false;
                const auto& expected = trace.samples[index];
                const auto actual = run.sample();
                ++index;
                if (actual.frame != expected.frame) {
                    mismatch(trace.label, expected.frame, render_session(texts_, locale_, "session.replay.subject.frame", "frame"),
                             std::to_string(expected.frame), std::to_string(actual.frame));
                    return true;
                }
                if (actual.truth.modules.size() != expected.truth.modules.size()) {
                    mismatch(trace.label, expected.frame, render_session(texts_, locale_, "session.replay.subject.module_count", "module count"),
                             std::to_string(expected.truth.modules.size()),
                             std::to_string(actual.truth.modules.size()));
                    return true;
                }
                for (std::size_t module = 0; module < expected.truth.modules.size(); ++module) {
                    const auto& expected_module = expected.truth.modules[module];
                    const auto& actual_module = actual.truth.modules[module];
                    if (expected_module.path != actual_module.path) {
                        mismatch(trace.label, expected.frame, render_session(texts_, locale_, "session.replay.subject.module_order", "module order"), expected_module.path, actual_module.path);
                        return true;
                    }
                    const auto difference = first_difference(expected_module.state, actual_module.state,
                                                             expected_module.path, texts_, locale_);
                    if (!difference.empty()) {
                        mismatch(trace.label, expected.frame, difference,
                                 config_display(expected_module.state), config_display(actual_module.state));
                        return true;
                    }
                }
                if (!observations_equal(expected.observations, actual.observations, *adapters_)) {
                    for (const auto& item : expected.observations) {
                        const auto actual_value = actual.observations.find(item.first);
                        const bool equal = actual_value != actual.observations.end() &&
                                           [&] {
                                               const ValueAdapter* adapter = adapters_->find(item.second.type());
                                               return adapter && adapter->equal(item.second, actual_value->second);
                                           }();
                        if (!equal) {
                            mismatch(trace.label, expected.frame, render_session(texts_, locale_, "session.replay.subject.observation", "observation") + " " + item.first,
                                     argument_display(item.second, *adapters_, texts_, locale_),
                                     actual_value == actual.observations.end()
                                         ? render_session(texts_, locale_, "session.observation.missing", "(missing)")
                                         : argument_display(actual_value->second, *adapters_, texts_, locale_));
                            return true;
                        }
                    }
                    mismatch(trace.label, expected.frame, render_session(texts_, locale_, "session.replay.subject.observation", "observation"),
                             render_session(texts_, locale_, "session.replay.recorded", "(recorded)"),
                             render_session(texts_, locale_, "session.replay.count_mismatch", "(count mismatch)"));
                    return true;
                }
                ++report.verified_frames;
                return false;
            };
            if (trace.samples.empty()) {
                report.notes.push_back(render_session(texts_, locale_, "session.replay.note.no_samples",
                                                      "Branch {branch} has no recorded samples; comparison skipped",
                                                      {{"branch", trace.label}}));
                continue;
            }
            if (verify()) return report;
            // 按记录采样逐逻辑帧推进：共同输入附着在驱动前逻辑帧上（可选）；
            // 本分支实际驱动的输入必须都在共同记录中，否则该步骤不可复现。
            std::size_t input_index = 0;
            while (index < trace.samples.size()) {
                const std::int64_t step_frame = trace.samples[index].frame - 1;
                const bool divergent = std::any_of(
                    trace.driven.begin(), trace.driven.end(), [&](const DrivenInput& driven) {
                        if (driven.frame != step_frame) return false;
                        return std::none_of(record_.inputs.begin(), record_.inputs.end(),
                                            [&](const DrivenInput& item) {
                                                return item.frame == driven.frame &&
                                                       item.name == driven.name;
                                            });
                    });
                if (divergent) {
                    report.notes.push_back(render_session(
                        texts_, locale_, "session.replay.note.divergent_step",
                        "Branch {branch} drove inputs at frame {frame} that are not in the common record; comparison ends early",
                        {{"branch", trace.label}, {"frame", std::to_string(step_frame)}}));
                    break;
                }
                while (input_index < record_.inputs.size() &&
                       record_.inputs[input_index].frame == step_frame) {
                    const auto& input = record_.inputs[input_index];
                    run.drive(input.name, input.arguments);
                    ++input_index;
                }
                if (input_index < record_.inputs.size() &&
                    record_.inputs[input_index].frame < step_frame) {
                    report.notes.push_back(render_session(
                        texts_, locale_, "session.replay.note.frame_end",
                        "Branch {branch} has no shared input records after frame {frame}; comparison ends early",
                        {{"branch", trace.label}, {"frame", std::to_string(step_frame)}}));
                    break;
                }
                run.step();
                if (verify()) return report;
            }
            if (index < trace.samples.size()) {
                report.notes.push_back(render_session(
                    texts_, locale_, "session.replay.note.uncompared",
                    "Branch {branch} has {count} recorded samples not compared",
                    {{"branch", trace.label}, {"count", std::to_string(trace.samples.size() - index)}}));
            } else if (input_index < record_.inputs.size()) {
                report.notes.push_back(render_session(
                    texts_, locale_, "session.replay.note.samples_end",
                    "Recorded samples for branch {branch} end at frame {frame}; later inputs are not compared",
                    {{"branch", trace.label},
                     {"frame", std::to_string(trace.samples.back().frame)}}));
            }
        }
    } catch (const EngineError& error) {
        report.diagnostic = view_of(error.diagnostic(), texts_, locale_);
        report.ok = false;
        return report;
    }
    report.ok = true;
    report.complete = report.notes.empty();
    return report;
}

}  // namespace ascend::session
