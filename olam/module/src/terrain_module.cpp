#include <olam/module/terrain_module.hpp>

#include <olam/terrain.hpp>

#include <algorithm>
#include <cstdint>
#include <initializer_list>
#include <limits>
#include <memory>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace olam::module {
namespace {

using Integer = std::int64_t;
using ascend::TextKey;
using ascend::TextRef;

constexpr Integer default_columns = 512;
constexpr Integer default_rows = 512;
constexpr Integer default_seed = 0;
constexpr Integer default_octaves = 6;
constexpr Integer default_feature_cells = 128;
constexpr Integer default_base_elevation = 0;
constexpr Integer default_amplitude = 150000;
constexpr Integer default_origin = 0;
constexpr Integer default_layer = 0;
constexpr Integer default_checksum_limit_cells = 0;

TextRef described(const char* key, const char* fallback) {
    return TextRef(TextKey{terrain_text_domain, key}, fallback);
}

[[noreturn]] void fail(const std::string& instance, ascend::ErrorCode code, const char* key,
                       const char* fallback, TextRef::Arguments arguments = {}) {
    throw ascend::EngineError(
        {code, {instance, {}}, TextRef(TextKey{terrain_text_domain, key}, fallback,
                                       std::move(arguments))});
}

[[noreturn]] void fail_state(const std::string& instance, const char* key, const char* fallback,
                             TextRef::Arguments arguments = {}) {
    fail(instance, ascend::ErrorCode::invalid_state, key, fallback, std::move(arguments));
}

const char* terrain_error_key(TerrainErrorCode code) {
    switch (code) {
        case TerrainErrorCode::invalid_space_size: return "error.space_size";
        case TerrainErrorCode::grid_overflow: return "error.grid_overflow";
        case TerrainErrorCode::invalid_parameters: return "error.parameters";
        case TerrainErrorCode::elevation_range: return "error.elevation_range";
        case TerrainErrorCode::invalid_space: return "error.space";
    }
    return "error.parameters";
}

const char* terrain_error_fallback(TerrainErrorCode code) {
    switch (code) {
        case TerrainErrorCode::invalid_space_size:
            return "Space size must be an exact multiple of one meter";
        case TerrainErrorCode::grid_overflow:
            return "Terrain grid dimensions exceed the addressable cell count";
        case TerrainErrorCode::invalid_parameters:
            return "Terrain generation parameters are invalid";
        case TerrainErrorCode::elevation_range:
            return "Terrain elevation range exceeds the 32-bit elevation type";
        case TerrainErrorCode::invalid_space: return "Terrain space is invalid";
    }
    return "Terrain description is invalid";
}

const char* space_error_key(SpaceErrorCode code) {
    switch (code) {
        case SpaceErrorCode::invalid_identifier: return "error.invalid_identifier";
        case SpaceErrorCode::invalid_size: return "error.invalid_size";
        case SpaceErrorCode::negative_origin: return "error.negative_origin";
        case SpaceErrorCode::arithmetic_overflow: return "error.arithmetic_overflow";
        case SpaceErrorCode::duplicate_identifier: return "error.duplicate_identifier";
        case SpaceErrorCode::same_layer_overlap: return "error.same_layer_overlap";
    }
    return "error.unknown";
}

const char* space_error_fallback(SpaceErrorCode code) {
    switch (code) {
        case SpaceErrorCode::invalid_identifier: return "Space identifier must not be empty";
        case SpaceErrorCode::invalid_size: return "Space size must be positive";
        case SpaceErrorCode::negative_origin: return "Space origin must be non-negative";
        case SpaceErrorCode::arithmetic_overflow: return "Space range exceeds the tick range";
        case SpaceErrorCode::duplicate_identifier: return "Space identifier is already registered";
        case SpaceErrorCode::same_layer_overlap:
            return "Space overlaps another space on the same layer";
    }
    return "Space description is invalid";
}

// 空间创建失败时保留原因链；其余地形错误使用自身类别。
[[noreturn]] void fail_terrain(const std::string& instance, const TerrainError& error,
                               ascend::ErrorCode code) {
    if (error.code == TerrainErrorCode::invalid_space && error.space.has_value()) {
        const TextRef reason(TextKey{terrain_text_domain, space_error_key(error.space->code)},
                             space_error_fallback(error.space->code));
        fail(instance, code, "error.space", "Terrain space '{id}' is invalid: {reason}",
             {{"id", TextRef(error.space->space.value)}, {"reason", reason}});
    }
    fail(instance, code, terrain_error_key(error.code), terrain_error_fallback(error.code));
}

const ascend::Config* member(const ascend::Config& object, const char* name) {
    return object.find(name);
}

Integer integer_field(const ascend::Config& object, const char* name, Integer fallback,
                      const std::string& instance, ascend::ErrorCode code) {
    const ascend::Config* value = member(object, name);
    if (value == nullptr) return fallback;
    if (value->kind() != ascend::Config::Kind::integer) {
        fail(instance, code, "config.integer", "Terrain field '{field}' must be an integer",
             {{"field", TextRef(name)}});
    }
    return value->integer();
}

std::string identifier_field(const ascend::Config& object, const std::string& fallback,
                             const std::string& instance, ascend::ErrorCode code) {
    const ascend::Config* value = member(object, "id");
    if (value == nullptr) return fallback;
    if (value->kind() != ascend::Config::Kind::string) {
        fail(instance, code, "config.identifier", "Terrain space identifier must be a string");
    }
    return value->string();
}

void reject_unknown(const ascend::Config& object, const std::string& instance,
                    ascend::ErrorCode code, bool allow_state_fields) {
    for (const auto& entry : object.members()) {
        const bool known = entry.first == "id" || entry.first == "layer" ||
                           entry.first == "origin_x" || entry.first == "origin_y" ||
                           entry.first == "columns" || entry.first == "rows" ||
                           entry.first == "cell_ticks" || entry.first == "seed" ||
                           entry.first == "octaves" || entry.first == "feature_cells" ||
                           entry.first == "base_elevation" || entry.first == "amplitude" ||
                           entry.first == "generator_version" || entry.first == "edits" ||
                           entry.first == "checksum_limit_cells" ||
                           (allow_state_fields && entry.first == "base_checksum");
        if (!known) {
            fail(instance, code, allow_state_fields ? "state.unknown" : "config.unknown",
                 "Unknown terrain field '{field}'",
                 {{"field", TextRef(entry.first)}});
        }
    }
}

struct ParsedConfig {
    TerrainDescription description;
    Integer checksum_limit_cells = 0;
};

ParsedConfig parse_description(const ascend::Config& config, const std::string& instance,
                               ascend::ErrorCode code, bool allow_state_fields) {
    if (config.kind() != ascend::Config::Kind::object) {
        fail(instance, code, "config.object", "Terrain module config must be an object");
    }
    reject_unknown(config, instance, code, allow_state_fields);

    const Integer cell_ticks =
        integer_field(config, "cell_ticks", terrain_cell_ticks, instance, code);
    if (cell_ticks != terrain_cell_ticks) {
        fail(instance, code, "config.cell_ticks",
             "Terrain cell size is fixed at one meter (1000 ticks)");
    }
    const Integer checksum_limit =
        integer_field(config, "checksum_limit_cells", default_checksum_limit_cells, instance, code);
    if (checksum_limit < 0) {
        fail(instance, code, "config.checksum_limit_negative",
             "Terrain checksum cell limit must be non-negative");
    }
    const Integer generator_version =
        integer_field(config, "generator_version", terrain_generator_version, instance, code);
    if (generator_version != static_cast<Integer>(terrain_generator_version)) {
        fail(instance, code, "config.generator_version",
             "Terrain generator version does not match this implementation");
    }
    const Integer columns = integer_field(config, "columns", default_columns, instance, code);
    if (columns <= 0) {
        fail(instance, code, "config.columns", "Terrain columns must be positive");
    }
    const Integer rows = integer_field(config, "rows", default_rows, instance, code);
    if (rows <= 0) {
        fail(instance, code, "config.rows", "Terrain rows must be positive");
    }
    if (columns > std::numeric_limits<Integer>::max() / terrain_cell_ticks ||
        rows > std::numeric_limits<Integer>::max() / terrain_cell_ticks) {
        fail(instance, code, "config.grid_overflow",
             "Terrain grid dimensions exceed the space tick range");
    }
    if (checksum_limit > 0 && rows > 0 && columns > checksum_limit / rows) {
        fail(instance, code, "config.checksum_limit",
             "Terrain grid exceeds the configured checksum cell limit {limit}",
             {{"limit", TextRef(std::to_string(checksum_limit))}});
    }
    const Integer seed = integer_field(config, "seed", default_seed, instance, code);
    if (seed < 0) {
        fail(instance, code, "config.seed", "Terrain seed must be non-negative");
    }
    const Integer octaves = integer_field(config, "octaves", default_octaves, instance, code);
    if (octaves < 1 || octaves > 8) {
        fail(instance, code, "config.octaves", "Terrain octaves must be between 1 and 8");
    }
    const Integer feature_cells =
        integer_field(config, "feature_cells", default_feature_cells, instance, code);
    if (feature_cells < 1 || feature_cells > max_terrain_feature_cells) {
        fail(instance, code, "config.feature_cells",
             "Terrain feature size is outside the supported range");
    }
    const Integer base_elevation =
        integer_field(config, "base_elevation", default_base_elevation, instance, code);
    if (base_elevation < std::numeric_limits<Elevation>::min() ||
        base_elevation > std::numeric_limits<Elevation>::max()) {
        fail(instance, code, "config.elevation",
             "Terrain base elevation is outside the signed 32-bit range");
    }
    const Integer amplitude =
        integer_field(config, "amplitude", default_amplitude, instance, code);
    if (amplitude < 0 || amplitude > std::numeric_limits<Elevation>::max()) {
        fail(instance, code, "config.amplitude",
             "Terrain amplitude must be between zero and the signed 32-bit maximum");
    }
    const Integer layer = integer_field(config, "layer", default_layer, instance, code);
    if (layer < std::numeric_limits<Layer>::min() ||
        layer > std::numeric_limits<Layer>::max()) {
        fail(instance, code, "config.layer",
             "Terrain space layer is outside the signed 32-bit range");
    }

    TerrainDescription description;
    description.space.id = SpaceId{identifier_field(config, instance, instance, code)};
    description.space.layer = static_cast<Layer>(layer);
    description.space.origin =
        AbsolutePosition{integer_field(config, "origin_x", default_origin, instance, code),
                         integer_field(config, "origin_y", default_origin, instance, code)};
    description.space.size =
        Size{columns * terrain_cell_ticks, rows * terrain_cell_ticks};
    description.parameters.seed = static_cast<std::uint64_t>(seed);
    description.parameters.octaves = static_cast<std::uint32_t>(octaves);
    description.parameters.feature_cells = feature_cells;
    description.parameters.base_elevation = static_cast<Elevation>(base_elevation);
    description.parameters.amplitude = static_cast<Elevation>(amplitude);
    if (const auto error = validate_terrain(description)) {
        fail_terrain(instance, *error, code);
    }
    return ParsedConfig{description, checksum_limit};
}

struct SaturatedInteger {
    Integer value = 0;
    bool saturated = false;
};

void add_saturated(SaturatedInteger& sum, Integer value) noexcept {
    if (sum.saturated) return;
    constexpr Integer largest = std::numeric_limits<Integer>::max();
    constexpr Integer smallest = std::numeric_limits<Integer>::min();
    if (value > 0 && sum.value > largest - value) {
        sum.value = largest;
        sum.saturated = true;
    } else if (value < 0 && sum.value < smallest - value) {
        sum.value = smallest;
        sum.saturated = true;
    } else {
        sum.value += value;
    }
}

bool parse_index(const std::string& text, std::uint64_t& value) noexcept {
    if (text.empty()) return false;
    std::uint64_t result = 0;
    for (const char character : text) {
        if (character < '0' || character > '9') return false;
        const std::uint64_t digit = static_cast<std::uint64_t>(character - '0');
        if (result > (std::numeric_limits<std::uint64_t>::max() - digit) / 10) return false;
        result = result * 10 + digit;
    }
    value = result;
    return true;
}

void parse_edits(const ascend::Config& config, const std::string& instance,
                 ascend::ErrorCode code, Terrain& terrain) {
    const ascend::Config* edits = member(config, "edits");
    if (edits == nullptr) return;
    if (edits->kind() != ascend::Config::Kind::object) {
        fail(instance, code, "edit.object", "Terrain edits must be an object");
    }
    for (const auto& entry : edits->members()) {
        std::uint64_t index = 0;
        if (!parse_index(entry.first, index)) {
            fail(instance, code, "edit.index",
                 "Terrain edit key '{key}' is not a valid cell index",
                 {{"key", TextRef(entry.first)}});
        }
        const auto cell = terrain.cell_of_flat(index);
        if (!cell.has_value()) {
            fail(instance, code, "edit.index",
                 "Terrain edit index '{key}' is outside the grid",
                 {{"key", TextRef(entry.first)}});
        }
        if (entry.second.kind() != ascend::Config::Kind::object) {
            fail(instance, code, "edit.shape",
                 "Terrain edit '{key}' must be an object",
                 {{"key", TextRef(entry.first)}});
        }
        for (const auto& field : entry.second.members()) {
            if (field.first != "elevation") {
                fail(instance, code, "edit.unknown",
                     "Unknown terrain edit field '{field}'",
                     {{"field", TextRef(field.first)}});
            }
        }
        const ascend::Config* elevation = entry.second.find("elevation");
        if (elevation == nullptr || elevation->kind() != ascend::Config::Kind::integer) {
            fail(instance, code, "edit.elevation",
                 "Terrain edit '{key}' must contain an integer elevation",
                 {{"key", TextRef(entry.first)}});
        }
        if (elevation->integer() < std::numeric_limits<Elevation>::min() ||
            elevation->integer() > std::numeric_limits<Elevation>::max()) {
            fail(instance, code, "edit.elevation",
                 "Terrain edit '{key}' elevation is outside the signed 32-bit range",
                 {{"key", TextRef(entry.first)}});
        }
        terrain.set_elevation(cell->first, cell->second,
                              static_cast<Elevation>(elevation->integer()));
    }
}

void require_field(const ascend::Config& state, const char* name, ascend::Config::Kind kind,
                   const std::string& instance) {
    const ascend::Config* value = member(state, name);
    if (value == nullptr) {
        fail_state(instance, "state.field", "Terrain state is missing field '{field}'",
                   {{"field", TextRef(name)}});
    }
    if (value->kind() != kind) {
        fail_state(instance, "state.type", "Terrain state field '{field}' has the wrong type",
                   {{"field", TextRef(name)}});
    }
}

void require_state_fields(const ascend::Config& state, const std::string& instance) {
    if (state.kind() != ascend::Config::Kind::object) {
        fail_state(instance, "state.shape", "Terrain state must be an object");
    }
    reject_unknown(state, instance, ascend::ErrorCode::invalid_state, true);
    require_field(state, "id", ascend::Config::Kind::string, instance);
    for (const char* name : {"layer", "origin_x", "origin_y", "columns", "rows", "cell_ticks",
                             "seed", "octaves", "feature_cells", "base_elevation", "amplitude",
                             "generator_version", "checksum_limit_cells", "base_checksum"}) {
        require_field(state, name, ascend::Config::Kind::integer, instance);
    }
    require_field(state, "edits", ascend::Config::Kind::object, instance);
}

ascend::Config capture_state(const Terrain& terrain, Integer checksum_limit_cells) {
    const TerrainDescription& description = terrain.description();
    std::vector<std::pair<std::string, ascend::Config>> edits;
    edits.reserve(terrain.edits().size());
    for (const auto& entry : terrain.edits()) {
        edits.emplace_back(std::to_string(entry.first),
                           ascend::Config::object(
                               {{"elevation", ascend::Config::integer(entry.second)}}));
    }
    return ascend::Config::object(
        {{"id", ascend::Config::string(description.space.id.value)},
         {"layer", ascend::Config::integer(description.space.layer)},
         {"origin_x", ascend::Config::integer(description.space.origin.x)},
         {"origin_y", ascend::Config::integer(description.space.origin.y)},
         {"columns", ascend::Config::integer(static_cast<Integer>(terrain.columns()))},
         {"rows", ascend::Config::integer(static_cast<Integer>(terrain.rows()))},
         {"cell_ticks", ascend::Config::integer(terrain_cell_ticks)},
         {"seed", ascend::Config::integer(static_cast<Integer>(description.parameters.seed))},
         {"octaves", ascend::Config::integer(description.parameters.octaves)},
         {"feature_cells", ascend::Config::integer(description.parameters.feature_cells)},
         {"base_elevation", ascend::Config::integer(description.parameters.base_elevation)},
         {"amplitude", ascend::Config::integer(description.parameters.amplitude)},
         {"generator_version", ascend::Config::integer(terrain_generator_version)},
         {"checksum_limit_cells", ascend::Config::integer(checksum_limit_cells)},
         {"base_checksum",
          ascend::Config::integer(static_cast<Integer>(terrain.base_checksum()))},
         {"edits", ascend::Config::object(std::move(edits))}});
}

void restore_terrain(Terrain& target, const std::string& instance, const ascend::Config& state,
                     Integer checksum_limit_cells) {
    require_state_fields(state, instance);
    const ParsedConfig parsed =
        parse_description(state, instance, ascend::ErrorCode::invalid_state, true);
    if (parsed.checksum_limit_cells != checksum_limit_cells) {
        fail_state(instance, "state.checksum_limit",
                   "Terrain state checksum cell limit does not match the module configuration");
    }
    const TerrainDescription& description = parsed.description;
    const Integer expected =
        integer_field(state, "base_checksum", 0, instance, ascend::ErrorCode::invalid_state);
    if (expected < 0) {
        fail_state(instance, "state.checksum_negative",
                   "Terrain state checksum must be a non-negative integer");
    }
    Terrain rebuilt(description);
    if (rebuilt.base_checksum() != static_cast<std::uint64_t>(expected)) {
        fail_state(instance, "state.checksum",
                   "Terrain state checksum mismatch (expected {expected}, actual {actual})",
                   {{"expected", TextRef(std::to_string(expected))},
                    {"actual", TextRef(std::to_string(rebuilt.base_checksum()))}});
    }
    parse_edits(state, instance, ascend::ErrorCode::invalid_state, rebuilt);
    target = std::move(rebuilt);
}

std::optional<std::size_t> checked_index(Integer value, std::size_t limit) noexcept {
    if (value < 0) return std::nullopt;
    if (static_cast<std::uint64_t>(value) >= limit) return std::nullopt;
    return static_cast<std::size_t>(value);
}

}  // namespace

ascend::Config terrain_preset() {
    return ascend::Config::object(
        {{"origin_x", ascend::Config::integer(default_origin)},
         {"origin_y", ascend::Config::integer(default_origin)},
         {"layer", ascend::Config::integer(default_layer)},
         {"columns", ascend::Config::integer(default_columns)},
         {"rows", ascend::Config::integer(default_rows)},
         {"cell_ticks", ascend::Config::integer(terrain_cell_ticks)},
         {"seed", ascend::Config::integer(default_seed)},
         {"octaves", ascend::Config::integer(default_octaves)},
         {"feature_cells", ascend::Config::integer(default_feature_cells)},
         {"base_elevation", ascend::Config::integer(default_base_elevation)},
         {"amplitude", ascend::Config::integer(default_amplitude)},
         {"generator_version", ascend::Config::integer(terrain_generator_version)},
         {"checksum_limit_cells", ascend::Config::integer(default_checksum_limit_cells)},
         {"edits", ascend::Config::object({})}});
}

ascend::Module make_terrain_module(const std::string& instance, const ascend::Config& config) {
    // 空配置与空对象都使用同一份预填默认配置，避免默认值漂移。
    const bool empty_object =
        config.kind() == ascend::Config::Kind::object && config.members().empty();
    const ascend::Config effective =
        (config.is_null() || empty_object) ? terrain_preset() : config;
    const ParsedConfig parsed =
        parse_description(effective, instance, ascend::ErrorCode::invalid_config, false);
    auto terrain = std::make_shared<Terrain>(parsed.description);
    const Integer checksum_limit_cells = parsed.checksum_limit_cells;
    parse_edits(effective, instance, ascend::ErrorCode::invalid_config, *terrain);

    ascend::Module module(instance);
    const auto value = [&](std::string name, auto getter, const char* key, const char* fallback) {
        module.add_value<Integer>(std::move(name), std::move(getter), described(key, fallback),
                                  "olam.terrain.scalar.v1");
    };
    value("columns", [terrain] { return static_cast<Integer>(terrain->columns()); },
          "value.columns", "Terrain grid columns");
    value("rows", [terrain] { return static_cast<Integer>(terrain->rows()); },
          "value.rows", "Terrain grid rows");
    value("cell_ticks", [terrain] { return terrain->cell_ticks(); },
          "value.cell_ticks", "Terrain cell size in space ticks");
    value("origin_x", [terrain] { return terrain->space().origin().x; },
          "value.origin_x", "Absolute origin x of the instantiated space");
    value("origin_y", [terrain] { return terrain->space().origin().y; },
          "value.origin_y", "Absolute origin y of the instantiated space");
    value("width", [terrain] { return terrain->space().size().width; },
          "value.width", "Width of the instantiated space in ticks");
    value("height", [terrain] { return terrain->space().size().height; },
          "value.height", "Height of the instantiated space in ticks");
    value("layer", [terrain] { return terrain->space().layer(); },
          "value.layer", "Layer of the instantiated space");
    value("seed", [terrain] { return static_cast<Integer>(terrain->description().parameters.seed); },
          "value.seed", "Terrain noise seed");
    value("octaves", [terrain] { return terrain->description().parameters.octaves; },
          "value.octaves", "Terrain fractal octave count");
    value("feature_cells", [terrain] { return terrain->description().parameters.feature_cells; },
          "value.feature_cells", "Terrain first-octave feature size in cells");
    value("base_elevation", [terrain] { return terrain->description().parameters.base_elevation; },
          "value.base_elevation", "Terrain base elevation in millimeters");
    value("amplitude", [terrain] { return terrain->description().parameters.amplitude; },
          "value.amplitude", "Terrain first-octave amplitude in millimeters");
    value("generator_version", [] { return static_cast<Integer>(terrain_generator_version); },
          "value.generator_version", "Terrain generator algorithm version");
    value("min_elevation", [terrain] { return terrain->base_min_elevation(); },
          "value.min_elevation", "Minimum base terrain elevation in millimeters");
    value("max_elevation", [terrain] { return terrain->base_max_elevation(); },
          "value.max_elevation", "Maximum base terrain elevation in millimeters");
    value("elevation_sum", [terrain] { return terrain->base_elevation_sum(); },
          "value.elevation_sum", "Sum of base terrain elevations (saturated when out of range)");
    value("elevation_sum_saturated",
          [terrain] { return terrain->base_elevation_sum_saturated() ? Integer{1} : Integer{0}; },
          "value.elevation_sum_saturated",
          "Whether the base elevation sum saturated at the 64-bit boundary");
    value("checksum_limit_cells", [checksum_limit_cells] { return checksum_limit_cells; },
          "value.checksum_limit_cells",
          "Configured cell limit for state checksum cost; 0 means unlimited");
    value("base_checksum",
          [terrain] { return static_cast<Integer>(terrain->base_checksum()); },
          "value.base_checksum", "Deterministic checksum of the base elevation grid");
    value("edit_count", [terrain] { return static_cast<Integer>(terrain->edit_count()); },
          "value.edit_count", "Number of cell-level elevation overrides");
    value("edit_elevation_sum",
          [terrain] {
              SaturatedInteger sum;
              for (const auto& entry : terrain->edits()) add_saturated(sum, entry.second);
              return sum.value;
          },
          "value.edit_elevation_sum",
          "Sum of cell-level override elevations (saturated when out of range)");
    value("edit_elevation_sum_saturated",
          [terrain] {
              SaturatedInteger sum;
              for (const auto& entry : terrain->edits()) add_saturated(sum, entry.second);
              return sum.saturated ? Integer{1} : Integer{0};
          },
          "value.edit_elevation_sum_saturated",
          "Whether the cell-level override sum saturated at the 64-bit boundary");
    value("exact", [] { return Integer{1}; },
          "value.exact", "Whether every grid cell is exact (full implementation)");

    module.add_method<Integer, Integer, Integer>(
        "contains", {"column", "row"},
        [terrain](Integer column, Integer row) -> Integer {
            const auto c = checked_index(column, terrain->columns());
            const auto r = checked_index(row, terrain->rows());
            return c.has_value() && r.has_value() ? Integer{1} : Integer{0};
        },
        ascend::MethodOptions{
            described("method.contains",
                      "Report whether a grid cell is inside the terrain as 1 or 0"),
            {}, {}, "olam.terrain.contains.v1"});
    module.add_method<Integer, Integer, Integer>(
        "contains_position", {"x", "y"},
        [terrain](Integer x, Integer y) -> Integer {
            return terrain->cell_of(AbsolutePosition{x, y}).has_value() ? Integer{1} : Integer{0};
        },
        ascend::MethodOptions{
            described("method.contains_position",
                      "Report whether an absolute millimeter position is inside the terrain as 1 or 0"),
            {}, {}, "olam.terrain.contains_position.v1"});
    module.add_method<Integer, Integer, Integer>(
        "cell_column", {"x", "y"},
        [terrain](Integer x, Integer y) -> Integer {
            const auto cell = terrain->cell_of(AbsolutePosition{x, y});
            return cell.has_value() ? static_cast<Integer>(cell->first) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.cell_column",
                      "Column containing an absolute millimeter position; missing marker outside the terrain"),
            {}, {}, "olam.terrain.cell_column.v1"});
    module.add_method<Integer, Integer, Integer>(
        "cell_row", {"x", "y"},
        [terrain](Integer x, Integer y) -> Integer {
            const auto cell = terrain->cell_of(AbsolutePosition{x, y});
            return cell.has_value() ? static_cast<Integer>(cell->second) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.cell_row",
                      "Row containing an absolute millimeter position; missing marker outside the terrain"),
            {}, {}, "olam.terrain.cell_row.v1"});
    module.add_method<Integer, Integer, Integer>(
        "base_elevation_at", {"x", "y"},
        [terrain](Integer x, Integer y) -> Integer {
            const auto cell = terrain->cell_of(AbsolutePosition{x, y});
            if (!cell.has_value()) return terrain_missing;
            const auto value = terrain->base_elevation(cell->first, cell->second);
            return value.has_value() ? static_cast<Integer>(*value) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.base_elevation_at",
                      "Read base terrain elevation at an absolute millimeter position; missing marker outside the terrain"),
            {}, {}, "olam.terrain.base_elevation_at.v1"});
    module.add_method<Integer, Integer, Integer>(
        "elevation_at", {"x", "y"},
        [terrain](Integer x, Integer y) -> Integer {
            const auto cell = terrain->cell_of(AbsolutePosition{x, y});
            if (!cell.has_value()) return terrain_missing;
            const auto value = terrain->elevation(cell->first, cell->second);
            return value.has_value() ? static_cast<Integer>(*value) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.elevation_at",
                      "Read effective terrain elevation at an absolute millimeter position; missing marker outside the terrain"),
            {}, {}, "olam.terrain.elevation_at.v1"});
    module.add_method<Integer, Integer, Integer>(
        "cell_base_elevation", {"column", "row"},
        [terrain](Integer column, Integer row) -> Integer {
            const auto c = checked_index(column, terrain->columns());
            const auto r = checked_index(row, terrain->rows());
            if (!c.has_value() || !r.has_value()) return terrain_missing;
            const auto value = terrain->base_elevation(*c, *r);
            return value.has_value() ? static_cast<Integer>(*value) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.cell_base_elevation",
                      "Read base terrain elevation in millimeters; missing marker outside the grid"),
            {}, {}, "olam.terrain.cell_base_elevation.v1"});
    module.add_method<Integer, Integer, Integer>(
        "elevation", {"column", "row"},
        [terrain](Integer column, Integer row) -> Integer {
            const auto c = checked_index(column, terrain->columns());
            const auto r = checked_index(row, terrain->rows());
            if (!c.has_value() || !r.has_value()) return terrain_missing;
            const auto value = terrain->elevation(*c, *r);
            return value.has_value() ? static_cast<Integer>(*value) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.elevation",
                      "Read effective terrain elevation in millimeters; missing marker outside the grid"),
            {}, {}, "olam.terrain.elevation.v1"});
    module.add_method<Integer, Integer, Integer>(
        "slope", {"column", "row"},
        [terrain](Integer column, Integer row) -> Integer {
            const auto c = checked_index(column, terrain->columns());
            const auto r = checked_index(row, terrain->rows());
            if (!c.has_value() || !r.has_value()) return terrain_missing;
            const auto value = terrain->slope(*c, *r);
            return value.has_value() ? static_cast<Integer>(*value) : terrain_missing;
        },
        ascend::MethodOptions{
            described("method.slope",
                      "Maximum four-neighbor elevation difference in millimeters per meter; missing marker outside the grid"),
            {}, {}, "olam.terrain.slope.v1"});
    module.add_method<void, Integer, Integer, Integer>(
        "set_elevation", {"column", "row", "elevation"},
        [terrain, instance](Integer column, Integer row, Integer elevation) {
            const auto c = checked_index(column, terrain->columns());
            const auto r = checked_index(row, terrain->rows());
            if (!c.has_value() || !r.has_value()) {
                fail(instance, ascend::ErrorCode::execution_failed, "error.edit_index",
                     "Terrain edit cell is outside the grid");
            }
            if (elevation < std::numeric_limits<Elevation>::min() ||
                elevation > std::numeric_limits<Elevation>::max()) {
                fail(instance, ascend::ErrorCode::execution_failed, "error.edit_elevation",
                     "Terrain edit elevation is outside the signed 32-bit range");
            }
            terrain->set_elevation(*c, *r, static_cast<Elevation>(elevation));
        },
        ascend::MethodOptions{
            described("method.set_elevation",
                      "Override one grid cell elevation in millimeters"),
            {}, {}, "olam.terrain.set_elevation.v1"});
    module.add_method<Integer, Integer, Integer>(
        "clear_elevation", {"column", "row"},
        [terrain, instance](Integer column, Integer row) -> Integer {
            const auto c = checked_index(column, terrain->columns());
            const auto r = checked_index(row, terrain->rows());
            if (!c.has_value() || !r.has_value()) {
                fail(instance, ascend::ErrorCode::execution_failed, "error.edit_index",
                     "Terrain edit cell is outside the grid");
            }
            return terrain->clear_elevation(*c, *r) ? Integer{1} : Integer{0};
        },
        ascend::MethodOptions{
            described("method.clear_elevation",
                      "Clear one cell override and fall back to base terrain; returns 1 when an override was removed"),
            {}, {}, "olam.terrain.clear_elevation.v1"});

    module.add_state(
        terrain_state_contract,
        [terrain, checksum_limit_cells] {
            return capture_state(*terrain, checksum_limit_cells);
        },
        [terrain, instance, checksum_limit_cells](const ascend::Config& state) {
            restore_terrain(*terrain, instance, state, checksum_limit_cells);
        });
    return module;
}

void register_terrain_module(ascend::ModuleLibrary& library) {
    library.register_implementation(terrain_definition, terrain_implementation,
                                    make_terrain_module, terrain_preset());
}

ascend::ModulePackage make_terrain_package(std::vector<ascend::ModuleResource> resources) {
    ascend::Engine engine;
    engine.add(make_terrain_module("probe", ascend::Config{}));
    ascend::ModulePackage package;
    package.manifest = ascend::export_module_manifest(engine, "", "probe");
    package.manifest.definition = terrain_definition;
    package.manifest.version = terrain_version;
    package.manifest.implementation = terrain_implementation;
    package.resources = std::move(resources);
    return package;
}

std::string terrain_package_bytes(std::vector<ascend::ModuleResource> resources) {
    return ascend::encode_module_package(make_terrain_package(std::move(resources)));
}

}  // namespace olam::module
