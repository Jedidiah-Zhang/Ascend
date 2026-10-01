#include <ascend/experiment_file.hpp>

#include "container.hpp"
#include "json.hpp"

#include <cmath>
#include <cstdint>
#include <cstring>
#include <map>
#include <optional>
#include <set>
#include <string>
#include <utility>
#include <vector>

namespace ascend {
namespace {

constexpr std::uint64_t kSectionMeta = 1;
constexpr std::uint64_t kSectionTraces = 2;
constexpr std::size_t kMaxVarintBytes = 10;
constexpr std::size_t kMaxDepth = 128;

enum ValueTag : std::uint8_t {
    kTagNull = 0,
    kTagFalse = 1,
    kTagTrue = 2,
    kTagInt = 3,
    kTagDouble = 4,
    kTagString = 5,
    kTagArray = 6,
    kTagObject = 7,
};

[[noreturn]] void fail(ErrorCode code, const char* key, const char* fallback,
                       const Reference& target = {}) {
    throw EngineError({code, target, TextRef(TextKey{engine_text_domain, key}, std::string(fallback))});
}

const char* error_code_name(ErrorCode code) {
    switch (code) {
        case ErrorCode::invalid_declaration: return "invalid_declaration";
        case ErrorCode::duplicate_module: return "duplicate_module";
        case ErrorCode::duplicate_symbol: return "duplicate_symbol";
        case ErrorCode::missing_symbol: return "missing_symbol";
        case ErrorCode::wrong_kind: return "wrong_kind";
        case ErrorCode::type_mismatch: return "type_mismatch";
        case ErrorCode::argument_count: return "argument_count";
        case ErrorCode::registration_open: return "registration_open";
        case ErrorCode::registration_closed: return "registration_closed";
        case ErrorCode::validation_failed: return "validation_failed";
        case ErrorCode::execution_failed: return "execution_failed";
        case ErrorCode::missing_module: return "missing_module";
        case ErrorCode::missing_requirement: return "missing_requirement";
        case ErrorCode::duplicate_requirement: return "duplicate_requirement";
        case ErrorCode::duplicate_connection: return "duplicate_connection";
        case ErrorCode::unconnected_requirement: return "unconnected_requirement";
        case ErrorCode::contract_mismatch: return "contract_mismatch";
        case ErrorCode::duplicate_definition: return "duplicate_definition";
        case ErrorCode::unknown_definition: return "unknown_definition";
        case ErrorCode::invalid_config: return "invalid_config";
        case ErrorCode::invalid_assembly: return "invalid_assembly";
        case ErrorCode::invalid_json: return "invalid_json";
        case ErrorCode::unsupported_format_version: return "unsupported_format_version";
        case ErrorCode::io_failure: return "io_failure";
        case ErrorCode::invalid_i18n: return "invalid_i18n";
        case ErrorCode::state_incomplete: return "state_incomplete";
        case ErrorCode::state_mismatch: return "state_mismatch";
        case ErrorCode::invalid_state: return "invalid_state";
        case ErrorCode::invalid_intervention: return "invalid_intervention";
    }
    return "execution_failed";
}

ErrorCode error_code_from_name(const std::string& name) {
    static const std::map<std::string, ErrorCode> table = {
        {"invalid_declaration", ErrorCode::invalid_declaration},
        {"duplicate_module", ErrorCode::duplicate_module},
        {"duplicate_symbol", ErrorCode::duplicate_symbol},
        {"missing_symbol", ErrorCode::missing_symbol},
        {"wrong_kind", ErrorCode::wrong_kind},
        {"type_mismatch", ErrorCode::type_mismatch},
        {"argument_count", ErrorCode::argument_count},
        {"registration_open", ErrorCode::registration_open},
        {"registration_closed", ErrorCode::registration_closed},
        {"validation_failed", ErrorCode::validation_failed},
        {"execution_failed", ErrorCode::execution_failed},
        {"missing_module", ErrorCode::missing_module},
        {"missing_requirement", ErrorCode::missing_requirement},
        {"duplicate_requirement", ErrorCode::duplicate_requirement},
        {"duplicate_connection", ErrorCode::duplicate_connection},
        {"unconnected_requirement", ErrorCode::unconnected_requirement},
        {"contract_mismatch", ErrorCode::contract_mismatch},
        {"duplicate_definition", ErrorCode::duplicate_definition},
        {"unknown_definition", ErrorCode::unknown_definition},
        {"invalid_config", ErrorCode::invalid_config},
        {"invalid_assembly", ErrorCode::invalid_assembly},
        {"invalid_json", ErrorCode::invalid_json},
        {"unsupported_format_version", ErrorCode::unsupported_format_version},
        {"io_failure", ErrorCode::io_failure},
        {"invalid_i18n", ErrorCode::invalid_i18n},
        {"state_incomplete", ErrorCode::state_incomplete},
        {"state_mismatch", ErrorCode::state_mismatch},
        {"invalid_state", ErrorCode::invalid_state},
        {"invalid_intervention", ErrorCode::invalid_intervention},
    };
    const auto found = table.find(name);
    return found == table.end() ? ErrorCode::execution_failed : found->second;
}

struct Encoder {
    std::string bytes;

    void u8(std::uint8_t value) { bytes.push_back(static_cast<char>(value)); }

    void varint(std::uint64_t value) {
        while (value >= 0x80) {
            bytes.push_back(static_cast<char>((value & 0x7f) | 0x80));
            value >>= 7;
        }
        bytes.push_back(static_cast<char>(value));
    }

    void zigzag(std::int64_t value) {
        const auto raw = (static_cast<std::uint64_t>(value) << 1) ^
                         static_cast<std::uint64_t>(value >> 63);
        varint(raw);
    }

    void raw(const std::string& data) { bytes += data; }

    void text(const std::string& value) {
        varint(value.size());
        bytes += value;
    }

    void number(double value) {
        std::uint64_t bits = 0;
        std::memcpy(&bits, &value, sizeof(bits));
        for (int index = 0; index < 8; ++index) {
            u8(static_cast<std::uint8_t>((bits >> (8 * index)) & 0xff));
        }
    }
};

struct Reader {
    const std::string& data;
    std::size_t pos = 0;

    void need(std::size_t count) const {
        if (pos > data.size() || count > data.size() - pos) {
            fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                 "Experiment file is truncated");
        }
    }

    std::uint8_t u8() {
        need(1);
        return static_cast<std::uint8_t>(data[pos++]);
    }

    std::uint64_t varint() {
        std::uint64_t value = 0;
        for (std::size_t index = 0; index < kMaxVarintBytes; ++index) {
            const auto byte = u8();
            if (index == kMaxVarintBytes - 1 && (byte & 0x7e) != 0) {
                fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                     "Experiment file varint overflows 64 bits");
            }
            value |= static_cast<std::uint64_t>(byte & 0x7f) << (7 * index);
            if ((byte & 0x80) == 0) return value;
        }
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file varint is too long");
    }

    std::int64_t zigzag() {
        const auto raw = varint();
        return static_cast<std::int64_t>((raw >> 1) ^ (~(raw & 1) + 1));
    }

    std::string text() {
        const auto size = varint();
        need(size);
        std::string value = data.substr(pos, size);
        pos += size;
        return value;
    }

    std::string bytes_n(std::uint64_t size) {
        need(size);
        std::string value = data.substr(pos, static_cast<std::size_t>(size));
        pos += static_cast<std::size_t>(size);
        return value;
    }

    double number() {
        std::uint64_t bits = 0;
        for (int index = 0; index < 8; ++index) {
            bits |= static_cast<std::uint64_t>(u8()) << (8 * index);
        }
        double value = 0.0;
        std::memcpy(&value, &bits, sizeof(value));
        if (!std::isfinite(value)) {
            fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                 "Experiment file contains a non-finite number");
        }
        return value;
    }
};

struct StringTable {
    std::vector<std::string> strings;
    std::map<std::string, std::uint64_t> lookup;

    std::uint64_t intern(const std::string& value) {
        const auto found = lookup.find(value);
        if (found != lookup.end()) return found->second;
        const auto index = static_cast<std::uint64_t>(strings.size());
        strings.push_back(value);
        lookup.emplace(value, index);
        return index;
    }
};

void write_config(Encoder& out, StringTable& table, const Config& value) {
    switch (value.kind()) {
        case Config::Kind::null_value:
            out.u8(kTagNull);
            return;
        case Config::Kind::boolean:
            out.u8(value.boolean() ? kTagTrue : kTagFalse);
            return;
        case Config::Kind::integer:
            out.u8(kTagInt);
            out.zigzag(value.integer());
            return;
        case Config::Kind::number:
            out.u8(kTagDouble);
            out.number(value.number());
            return;
        case Config::Kind::string:
            out.u8(kTagString);
            out.varint(table.intern(value.string()));
            return;
        case Config::Kind::array:
            out.u8(kTagArray);
            out.varint(value.elements().size());
            for (const auto& element : value.elements()) write_config(out, table, element);
            return;
        case Config::Kind::object:
            out.u8(kTagObject);
            out.varint(value.members().size());
            for (const auto& [name, member] : value.members()) {
                out.varint(table.intern(name));
                write_config(out, table, member);
            }
            return;
    }
}

Config read_config(Reader& in, const std::vector<std::string>& strings, std::size_t depth) {
    if (depth > kMaxDepth) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file nesting depth exceeds the limit");
    }
    const auto tag = in.u8();
    switch (tag) {
        case kTagNull:
            return Config{};
        case kTagFalse:
            return Config::boolean(false);
        case kTagTrue:
            return Config::boolean(true);
        case kTagInt:
            return Config::integer(in.zigzag());
        case kTagDouble:
            return Config::number(in.number());
        case kTagString: {
            const auto index = in.varint();
            if (index >= strings.size()) {
                fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                     "Experiment file string index is out of range");
            }
            return Config::string(strings[static_cast<std::size_t>(index)]);
        }
        case kTagArray: {
            const auto count = in.varint();
            std::vector<Config> elements;
            for (std::uint64_t index = 0; index < count; ++index) {
                elements.push_back(read_config(in, strings, depth + 1));
            }
            return Config::array(std::move(elements));
        }
        case kTagObject: {
            const auto count = in.varint();
            std::vector<std::pair<std::string, Config>> members;
            std::set<std::string> names;
            for (std::uint64_t index = 0; index < count; ++index) {
                const auto name_index = in.varint();
                if (name_index >= strings.size()) {
                    fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                         "Experiment file string index is out of range");
                }
                auto name = strings[static_cast<std::size_t>(name_index)];
                if (!names.insert(name).second) {
                    fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                         "Experiment file object repeats a member name");
                }
                members.emplace_back(std::move(name), read_config(in, strings, depth + 1));
            }
            return Config::object(std::move(members));
        }
        default:
            fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                 "Experiment file value tag is not recognized");
    }
}

void write_any(Encoder& out, const std::any& value, const std::string& name) {
    if (const auto* number = std::any_cast<std::int64_t>(&value)) {
        out.u8(kTagInt);
        out.zigzag(*number);
        return;
    }
    fail(ErrorCode::type_mismatch, "experiment_file.unsupported_type",
         "Experiment files support only 64-bit integer inputs and observations",
         Reference{std::string{}, name});
}

std::any read_any(Reader& in) {
    const auto tag = in.u8();
    if (tag != kTagInt) {
        fail(ErrorCode::type_mismatch, "experiment_file.unsupported_type",
             "Experiment files support only 64-bit integer inputs and observations");
    }
    return in.zigzag();
}

void write_text(Encoder& out, StringTable& table, const TextRef& text) {
    out.u8(text.is_literal() ? 1 : 0);
    if (!text.is_literal()) {
        out.varint(table.intern(text.key().domain));
        out.varint(table.intern(text.key().key));
    }
    out.varint(table.intern(text.literal()));
    out.u8(text.fallback().has_value() ? 1 : 0);
    if (text.fallback().has_value()) out.varint(table.intern(*text.fallback()));
    out.varint(text.arguments().size());
    for (const auto& [name, value] : text.arguments()) {
        out.varint(table.intern(name));
        write_text(out, table, value);
    }
}

const std::string& read_ref(Reader& in, const std::vector<std::string>& strings) {
    const auto index = in.varint();
    if (index >= strings.size()) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file string index is out of range");
    }
    return strings[static_cast<std::size_t>(index)];
}

TextRef read_text(Reader& in, const std::vector<std::string>& strings, std::size_t depth) {
    if (depth > text_max_depth) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file text nesting depth exceeds the limit");
    }
    const auto literal_flag = in.u8();
    if (literal_flag > 1) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file text flag is not recognized");
    }
    std::string domain;
    std::string key;
    if (literal_flag == 0) {
        domain = read_ref(in, strings);
        key = read_ref(in, strings);
    }
    const auto literal = read_ref(in, strings);
    const auto fallback_flag = in.u8();
    if (fallback_flag > 1) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file text flag is not recognized");
    }
    std::optional<std::string> fallback;
    if (fallback_flag == 1) fallback = read_ref(in, strings);
    const auto count = in.varint();
    TextRef::Arguments arguments;
    for (std::uint64_t index = 0; index < count; ++index) {
        auto name = read_ref(in, strings);
        arguments.emplace_back(std::move(name), read_text(in, strings, depth + 1));
    }
    if (literal_flag == 1 && !arguments.empty()) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file literal text has arguments");
    }
    try {
        if (literal_flag == 1) return TextRef(literal);
        return TextRef(TextKey{domain, key}, fallback, std::move(arguments));
    } catch (const std::invalid_argument&) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file text definition is not valid");
    }
}

void write_diagnostic(Encoder& out, StringTable& table, const Diagnostic& diagnostic) {
    out.varint(table.intern(error_code_name(diagnostic.code)));
    out.varint(table.intern(diagnostic.target.module));
    out.varint(table.intern(diagnostic.target.symbol));
    out.varint(table.intern(diagnostic.source));
    out.varint(table.intern(diagnostic.path));
    write_text(out, table, diagnostic.text);
    out.u8(diagnostic.cause != nullptr ? 1 : 0);
    if (diagnostic.cause != nullptr) write_diagnostic(out, table, *diagnostic.cause);
}

Diagnostic read_diagnostic(Reader& in, const std::vector<std::string>& strings) {
    Diagnostic diagnostic;
    diagnostic.code = error_code_from_name(read_ref(in, strings));
    diagnostic.target.module = read_ref(in, strings);
    diagnostic.target.symbol = read_ref(in, strings);
    diagnostic.source = read_ref(in, strings);
    diagnostic.path = read_ref(in, strings);
    diagnostic.text = read_text(in, strings, 0);
    const auto cause_flag = in.u8();
    if (cause_flag > 1) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file diagnostic flag is not recognized");
    }
    if (cause_flag == 1) diagnostic.cause = std::make_shared<const Diagnostic>(read_diagnostic(in, strings));
    return diagnostic;
}

void write_snapshot(Encoder& out, StringTable& table, const StateSnapshot& snapshot) {
    out.varint(snapshot.modules.size());
    for (const auto& module : snapshot.modules) {
        out.varint(table.intern(module.path));
        out.varint(table.intern(module.contract));
        out.u8(module.stateless ? 1 : 0);
        write_config(out, table, module.state);
    }
}

StateSnapshot read_snapshot(Reader& in, const std::vector<std::string>& strings) {
    const auto count = in.varint();
    StateSnapshot snapshot;
    for (std::uint64_t index = 0; index < count; ++index) {
        ModuleState module;
        module.path = read_ref(in, strings);
        module.contract = read_ref(in, strings);
        const auto stateless = in.u8();
        if (stateless > 1) {
            fail(ErrorCode::invalid_json, "experiment_file.corrupt",
                 "Experiment file state flag is not recognized");
        }
        module.stateless = stateless == 1;
        module.state = read_config(in, strings, 0);
        snapshot.modules.push_back(std::move(module));
    }
    return snapshot;
}

void write_checkpoint(Encoder& out, StringTable& table, const Checkpoint& checkpoint) {
    out.zigzag(checkpoint.frame);
    write_snapshot(out, table, checkpoint.truth);
}

Checkpoint read_checkpoint(Reader& in, const std::vector<std::string>& strings) {
    Checkpoint checkpoint;
    checkpoint.frame = in.zigzag();
    checkpoint.truth = read_snapshot(in, strings);
    return checkpoint;
}

void write_intervention(Encoder& out, StringTable& table, const Intervention& intervention) {
    out.varint(table.intern(intervention.module));
    out.varint(table.intern(intervention.field));
    write_config(out, table, intervention.value);
}

Intervention read_intervention(Reader& in, const std::vector<std::string>& strings) {
    Intervention intervention;
    intervention.module = read_ref(in, strings);
    intervention.field = read_ref(in, strings);
    intervention.value = read_config(in, strings, 0);
    return intervention;
}

void write_driven_input(Encoder& out, StringTable& table, const DrivenInput& input) {
    out.zigzag(input.frame);
    out.varint(table.intern(input.name));
    out.varint(input.arguments.size());
    for (const auto& argument : input.arguments) write_any(out, argument, input.name);
}

DrivenInput read_driven_input(Reader& in, const std::vector<std::string>& strings) {
    DrivenInput input;
    input.frame = in.zigzag();
    input.name = read_ref(in, strings);
    const auto count = in.varint();
    for (std::uint64_t index = 0; index < count; ++index) {
        input.arguments.push_back(read_any(in));
    }
    return input;
}

void write_sample(Encoder& out, StringTable& table, const Sample& sample) {
    out.zigzag(sample.frame);
    write_snapshot(out, table, sample.truth);
    out.varint(sample.observations.size());
    for (const auto& [name, value] : sample.observations) {
        out.varint(table.intern(name));
        write_any(out, value, name);
    }
}

Sample read_sample(Reader& in, const std::vector<std::string>& strings) {
    Sample sample;
    sample.frame = in.zigzag();
    sample.truth = read_snapshot(in, strings);
    const auto count = in.varint();
    for (std::uint64_t index = 0; index < count; ++index) {
        auto name = read_ref(in, strings);
        sample.observations.emplace(std::move(name), read_any(in));
    }
    return sample;
}

void write_run_trace(Encoder& out, StringTable& table, const RunTrace& trace) {
    out.varint(table.intern(trace.label));
    write_checkpoint(out, table, trace.origin);
    out.varint(trace.interventions.size());
    for (const auto& intervention : trace.interventions) write_intervention(out, table, intervention);
    out.varint(trace.driven.size());
    for (const auto& input : trace.driven) write_driven_input(out, table, input);
    out.varint(trace.samples.size());
    for (const auto& sample : trace.samples) write_sample(out, table, sample);
    out.varint(trace.failures.size());
    for (const auto& failure : trace.failures) {
        out.zigzag(failure.frame);
        write_diagnostic(out, table, failure.diagnostic);
    }
}

RunTrace read_run_trace(Reader& in, const std::vector<std::string>& strings) {
    RunTrace trace;
    trace.label = read_ref(in, strings);
    trace.origin = read_checkpoint(in, strings);
    const auto interventions = in.varint();
    for (std::uint64_t index = 0; index < interventions; ++index) {
        trace.interventions.push_back(read_intervention(in, strings));
    }
    const auto driven = in.varint();
    for (std::uint64_t index = 0; index < driven; ++index) {
        trace.driven.push_back(read_driven_input(in, strings));
    }
    const auto samples = in.varint();
    for (std::uint64_t index = 0; index < samples; ++index) {
        trace.samples.push_back(read_sample(in, strings));
    }
    const auto failures = in.varint();
    for (std::uint64_t index = 0; index < failures; ++index) {
        StepFailure failure;
        failure.frame = in.zigzag();
        failure.diagnostic = read_diagnostic(in, strings);
        trace.failures.push_back(std::move(failure));
    }
    return trace;
}

void write_event(Encoder& out, StringTable& table, const ExperimentEvent& event) {
    out.u8(static_cast<std::uint8_t>(event.kind) + 1);
    out.zigzag(event.frame);
    write_diagnostic(out, table, event.diagnostic);
}

ExperimentEvent read_event(Reader& in, const std::vector<std::string>& strings) {
    ExperimentEvent event;
    const auto kind = in.u8();
    if (kind < 1 || kind > 4) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file event kind is not recognized");
    }
    event.kind = static_cast<ExperimentEvent::Kind>(kind - 1);
    event.frame = in.zigzag();
    event.diagnostic = read_diagnostic(in, strings);
    return event;
}

void write_experiment_trace(Encoder& out, StringTable& table, const ExperimentTrace& trace) {
    write_run_trace(out, table, trace.trace);
    out.u8(trace.exploration ? 1 : 0);
    out.zigzag(trace.current_frame);
    out.varint(trace.events.size());
    for (const auto& event : trace.events) write_event(out, table, event);
}

ExperimentTrace read_experiment_trace(Reader& in, const std::vector<std::string>& strings) {
    ExperimentTrace trace;
    trace.trace = read_run_trace(in, strings);
    const auto exploration = in.u8();
    if (exploration > 1) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file trace flag is not recognized");
    }
    trace.exploration = exploration == 1;
    trace.current_frame = in.zigzag();
    const auto count = in.varint();
    for (std::uint64_t index = 0; index < count; ++index) {
        trace.events.push_back(read_event(in, strings));
    }
    return trace;
}

void write_traces(const ExperimentFile& file, Encoder& out, StringTable& table) {
    out.varint(file.inputs.size());
    for (const auto& input : file.inputs) write_driven_input(out, table, input);
    out.varint(file.input_settings.size());
    for (const auto& [name, value] : file.input_settings) {
        out.varint(table.intern(name));
        write_any(out, value, name);
    }
    out.u8(file.checkpoint.has_value() ? 1 : 0);
    if (file.checkpoint.has_value()) write_checkpoint(out, table, *file.checkpoint);
    out.varint(file.traces.size());
    for (const auto& trace : file.traces) write_experiment_trace(out, table, trace);
}

Config reference_config(const Reference& reference) {
    return Config::object({{"module", Config::string(reference.module)},
                           {"symbol", Config::string(reference.symbol)}});
}

Config spec_config(const ExperimentSpec& spec) {
    std::vector<Config> inputs;
    for (const auto& [name, reference] : spec.inputs) {
        inputs.push_back(Config::object({{"name", Config::string(name)},
                                         {"reference", reference_config(reference)}}));
    }
    std::vector<Config> observations;
    for (const auto& [name, reference] : spec.observations) {
        observations.push_back(Config::object({{"name", Config::string(name)},
                                               {"reference", reference_config(reference)}}));
    }
    return Config::object({{"advance", reference_config(spec.advance)},
                           {"inputs", Config::array(std::move(inputs))},
                           {"observations", Config::array(std::move(observations))}});
}

Config assembly_config(const AssemblyDefinition& assembly) {
    return detail::parse_json(assembly.to_json(), "assembly");
}

Config build_meta(const ExperimentFile& file) {
    std::vector<std::pair<std::string, Config>> members;
    members.emplace_back("format", Config::string("ascend.experiment"));
    members.emplace_back("version", Config::integer(1));
    if (!file.model.empty()) members.emplace_back("model", Config::string(file.model));
    members.emplace_back("assembly", assembly_config(file.assembly));
    members.emplace_back("spec", spec_config(file.spec));
    std::vector<std::pair<std::string, Config>> implementations;
    for (const auto& [definition, implementation] : file.implementations) {
        implementations.emplace_back(definition, Config::string(implementation));
    }
    members.emplace_back("implementations", Config::object(std::move(implementations)));
    if (file.run_id.has_value()) {
        members.emplace_back("run", Config::object({
            {"id", Config::integer(static_cast<std::int64_t>(*file.run_id))},
            {"source_revision", Config::integer(static_cast<std::int64_t>(file.run_revision))}}));
    }
    if (file.draft.has_value()) {
        members.emplace_back("draft", Config::object({
            {"revision", Config::integer(static_cast<std::int64_t>(file.draft->first))},
            {"assembly", assembly_config(file.draft->second)}}));
    }
    std::vector<Config> series;
    for (const auto& trace : file.traces) {
        series.push_back(Config::object({
            {"label", Config::string(trace.trace.label)},
            {"role", Config::string(trace.exploration ? "exploration" : "branch")}}));
    }
    members.emplace_back("series", Config::array(std::move(series)));
    return Config::object(std::move(members));
}

const Config& require_member(const Config& object, const char* name) {
    const auto* member = object.find(name);
    if (member == nullptr) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata is missing a required field");
    }
    return *member;
}

std::string require_string(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::string) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata field has the wrong type");
    }
    return member.string();
}

std::int64_t require_integer(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::integer) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata field has the wrong type");
    }
    return member.integer();
}

const Config& require_object(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata field has the wrong type");
    }
    return member;
}

const Config& require_array(const Config& object, const char* name) {
    const auto& member = require_member(object, name);
    if (member.kind() != Config::Kind::array) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata field has the wrong type");
    }
    return member;
}

AssemblyDefinition parse_assembly(const Config& value) {
    detail::validate_json(value, "assembly");
    return AssemblyDefinition::parse(detail::write_json(value, "assembly"), "assembly");
}

Reference parse_reference(const Config& value) {
    Reference reference;
    reference.module = require_string(value, "module");
    reference.symbol = require_string(value, "symbol");
    return reference;
}

ExperimentSpec parse_spec(const Config& value) {
    ExperimentSpec spec;
    spec.advance = parse_reference(require_object(value, "advance"));
    for (const auto& entry : require_array(value, "inputs").elements()) {
        spec.inputs.emplace_back(require_string(entry, "name"),
                                 parse_reference(require_object(entry, "reference")));
    }
    for (const auto& entry : require_array(value, "observations").elements()) {
        spec.observations.emplace_back(require_string(entry, "name"),
                                       parse_reference(require_object(entry, "reference")));
    }
    return spec;
}

struct SeriesMeta {
    std::string label;
    bool exploration = false;
};

std::vector<SeriesMeta> parse_series(const Config& value) {
    std::vector<SeriesMeta> series;
    for (const auto& entry : require_array(value, "series").elements()) {
        SeriesMeta item;
        item.label = require_string(entry, "label");
        const auto role = require_string(entry, "role");
        if (role == "exploration") {
            item.exploration = true;
        } else if (role != "branch") {
            fail(ErrorCode::invalid_json, "experiment_file.meta",
                 "Experiment file metadata field has the wrong type");
        }
        series.push_back(std::move(item));
    }
    return series;
}

}  // namespace

std::string encode_experiment_file(const ExperimentFile& file) {
    const auto meta_text = detail::write_json(build_meta(file), "experiment");

    StringTable table;
    Encoder data;
    write_traces(file, data, table);

    Encoder traces;
    traces.varint(table.strings.size());
    for (const auto& value : table.strings) traces.text(value);
    traces.raw(data.bytes);

    return detail::write_container(detail::container_kind_experiment,
                                   {{kSectionMeta, detail::container_section_required, meta_text},
                                    {kSectionTraces, detail::container_section_required, traces.bytes}});
}

ExperimentFile decode_experiment_file(const std::string& bytes) {
    std::optional<std::string> meta_text;
    std::optional<std::string> traces_bytes;
    for (auto& section : detail::read_container(bytes, detail::container_kind_experiment, "experiment_file")) {
        if (section.id == kSectionMeta) {
            meta_text = std::move(section.data);
        } else if (section.id == kSectionTraces) {
            traces_bytes = std::move(section.data);
        } else if ((section.flags & detail::container_section_required) != 0) {
            fail(ErrorCode::unsupported_format_version, "experiment_file.section",
                 "Experiment file has a required section this reader does not know");
        }
    }
    if (!meta_text.has_value() || !traces_bytes.has_value()) {
        fail(ErrorCode::invalid_json, "experiment_file.section",
             "Experiment file is missing a required section");
    }

    const auto meta = detail::parse_json(*meta_text, "experiment file");
    if (meta.kind() != Config::Kind::object) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata is not an object");
    }
    if (require_string(meta, "format") != "ascend.experiment") {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata format is not recognized");
    }
    if (require_integer(meta, "version") != 1) {
        fail(ErrorCode::unsupported_format_version, "experiment_file.version",
             "Experiment file metadata version is not supported");
    }

    ExperimentFile file;
    if (const auto* model = meta.find("model"); model != nullptr) {
        if (model->kind() != Config::Kind::string) {
            fail(ErrorCode::invalid_json, "experiment_file.meta",
                 "Experiment file metadata field has the wrong type");
        }
        file.model = model->string();
    }
    file.assembly = parse_assembly(require_object(meta, "assembly"));
    file.spec = parse_spec(require_object(meta, "spec"));
    for (const auto& [definition, implementation] : require_object(meta, "implementations").members()) {
        if (implementation.kind() != Config::Kind::string) {
            fail(ErrorCode::invalid_json, "experiment_file.meta",
                 "Experiment file metadata field has the wrong type");
        }
        file.implementations.emplace(definition, implementation.string());
    }
    if (const auto* run = meta.find("run"); run != nullptr) {
        if (run->kind() != Config::Kind::object) {
            fail(ErrorCode::invalid_json, "experiment_file.meta",
                 "Experiment file metadata field has the wrong type");
        }
        file.run_id = static_cast<std::uint64_t>(require_integer(*run, "id"));
        file.run_revision = static_cast<std::uint64_t>(require_integer(*run, "source_revision"));
    }
    if (const auto* draft = meta.find("draft"); draft != nullptr) {
        if (draft->kind() != Config::Kind::object) {
            fail(ErrorCode::invalid_json, "experiment_file.meta",
                 "Experiment file metadata field has the wrong type");
        }
        file.draft = std::make_pair(
            static_cast<std::uint64_t>(require_integer(*draft, "revision")),
            parse_assembly(require_object(*draft, "assembly")));
    }
    const auto series_meta = parse_series(meta);

    Reader traces{*traces_bytes};
    const auto string_count = traces.varint();
    std::vector<std::string> strings;
    for (std::uint64_t index = 0; index < string_count; ++index) {
        strings.push_back(traces.text());
    }

    const auto common = traces.varint();
    for (std::uint64_t index = 0; index < common; ++index) {
        file.inputs.push_back(read_driven_input(traces, strings));
    }
    const auto settings = traces.varint();
    for (std::uint64_t index = 0; index < settings; ++index) {
        auto name = read_ref(traces, strings);
        file.input_settings.emplace(std::move(name), read_any(traces));
    }
    const auto checkpoint_flag = traces.u8();
    if (checkpoint_flag > 1) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file checkpoint flag is not recognized");
    }
    if (checkpoint_flag == 1) file.checkpoint = read_checkpoint(traces, strings);

    const auto trace_count = traces.varint();
    if (trace_count != series_meta.size()) {
        fail(ErrorCode::invalid_json, "experiment_file.meta",
             "Experiment file metadata series do not match the trace section");
    }
    for (std::uint64_t index = 0; index < trace_count; ++index) {
        auto trace = read_experiment_trace(traces, strings);
        const auto& expected = series_meta[static_cast<std::size_t>(index)];
        if (trace.trace.label != expected.label || trace.exploration != expected.exploration) {
            fail(ErrorCode::invalid_json, "experiment_file.meta",
                 "Experiment file metadata series do not match the trace section");
        }
        file.traces.push_back(std::move(trace));
    }
    if (traces.pos != traces_bytes->size()) {
        fail(ErrorCode::invalid_json, "experiment_file.corrupt",
             "Experiment file trace section has trailing bytes");
    }
    return file;
}

}  // namespace ascend
