#include <olam/terrain.hpp>

#include <algorithm>
#include <limits>
#include <stdexcept>
#include <utility>

namespace olam {
namespace {

// SplitMix64：仅使用无符号整数运算，结果不依赖平台字长或浮点环境。
std::uint64_t mix(std::uint64_t value) noexcept {
    value += 0x9e3779b97f4a7c15ULL;
    value = (value ^ (value >> 30)) * 0xbf58476d1ce4e5b9ULL;
    value = (value ^ (value >> 27)) * 0x94d049bb133111ebULL;
    return value ^ (value >> 31);
}

std::uint64_t hash_lattice(std::uint64_t seed, std::uint32_t octave, std::int64_t x,
                           std::int64_t y) noexcept {
    std::uint64_t hash = seed;
    hash ^= static_cast<std::uint64_t>(x) * 0x9e3779b97f4a7c15ULL;
    hash = mix(hash);
    hash ^= static_cast<std::uint64_t>(y) * 0xc2b2ae3d27d4eb4fULL;
    hash = mix(hash);
    hash ^= static_cast<std::uint64_t>(octave) * 0x165667b19e3779f9ULL;
    return mix(hash);
}

// 把格点哈希映射到 [-amplitude, amplitude]；span 不超过 2^32，乘积不会溢出 64 位。
std::int64_t lattice_value(std::uint64_t seed, std::uint32_t octave, std::int64_t x,
                           std::int64_t y, std::int64_t amplitude) noexcept {
    if (amplitude == 0) return 0;
    const std::uint64_t hash = hash_lattice(seed, octave, x, y);
    const std::uint64_t unit = hash >> 32;
    const std::uint64_t span =
        static_cast<std::uint64_t>(2) * static_cast<std::uint64_t>(amplitude) + 1;
    return -amplitude + static_cast<std::int64_t>((unit * span) >> 32);
}

// 平滑步进权重，返回 Q16 定点值（0～65536）。
std::int64_t smooth_weight(std::int64_t offset, std::int64_t spacing) noexcept {
    if (spacing <= 1) return 65536;
    const std::int64_t t = offset * 65536 / spacing;
    return t * t * (3 * 65536 - 2 * t) / (std::int64_t{65536} * 65536);
}

// 双线性插值分层值噪声；格坐标与偏移均为非负整数毫米。
std::int64_t value_noise(std::uint64_t seed, std::uint32_t octave, std::int64_t x,
                         std::int64_t y, std::int64_t spacing, std::int64_t amplitude) noexcept {
    const std::int64_t lattice_x = x / spacing;
    const std::int64_t lattice_y = y / spacing;
    const std::int64_t offset_x = x % spacing;
    const std::int64_t offset_y = y % spacing;

    const std::int64_t v00 = lattice_value(seed, octave, lattice_x, lattice_y, amplitude);
    const std::int64_t v10 = lattice_value(seed, octave, lattice_x + 1, lattice_y, amplitude);
    const std::int64_t v01 = lattice_value(seed, octave, lattice_x, lattice_y + 1, amplitude);
    const std::int64_t v11 =
        lattice_value(seed, octave, lattice_x + 1, lattice_y + 1, amplitude);

    const std::int64_t wx = smooth_weight(offset_x, spacing);
    const std::int64_t wy = smooth_weight(offset_y, spacing);
    const std::int64_t top = v00 + (v10 - v00) * wx / 65536;
    const std::int64_t bottom = v01 + (v11 - v01) * wx / 65536;
    return top + (bottom - top) * wy / 65536;
}

// 行优先遍历高程的 64 位 FNV-1a；显式按小端字节处理，避免平台字节序影响校验和。
std::uint64_t elevation_checksum(std::uint64_t value, Elevation elevation) noexcept {
    const std::uint32_t bits = static_cast<std::uint32_t>(elevation);
    for (int shift = 0; shift < 32; shift += 8) {
        value ^= (bits >> shift) & 0xffU;
        value *= 1099511628211ULL;
    }
    return value;
}

constexpr std::uint64_t checksum_offset = 14695981039346656037ULL;

std::optional<TerrainError> fail(TerrainErrorCode code, std::string message) {
    return TerrainError{code, std::move(message), std::nullopt};
}

std::optional<TerrainError> parameters_valid(const TerrainParameters& parameters) {
    if (parameters.octaves == 0 || parameters.octaves > 8) {
        return fail(TerrainErrorCode::invalid_parameters,
                    "Terrain octaves must be between 1 and 8");
    }
    if (parameters.feature_cells < 1 ||
        parameters.feature_cells > max_terrain_feature_cells) {
        return fail(TerrainErrorCode::invalid_parameters,
                    "Terrain feature size is outside the supported range");
    }
    if (parameters.amplitude < 0) {
        return fail(TerrainErrorCode::invalid_parameters,
                    "Terrain amplitude must be non-negative");
    }
    std::int64_t total_amplitude = 0;
    for (std::uint32_t octave = 0; octave < parameters.octaves; ++octave) {
        total_amplitude += static_cast<std::int64_t>(parameters.amplitude) >> octave;
    }
    const std::int64_t base = parameters.base_elevation;
    const std::int64_t lowest = base - total_amplitude;
    const std::int64_t highest = base + total_amplitude;
    if (lowest < std::numeric_limits<Elevation>::min() ||
        highest > std::numeric_limits<Elevation>::max()) {
        return fail(TerrainErrorCode::elevation_range,
                    "Terrain elevation range exceeds the 32-bit elevation type");
    }
    return std::nullopt;
}

std::optional<TerrainError> grid_dimensions(const TerrainDescription& description,
                                            std::size_t& columns, std::size_t& rows) {
    SpaceSet spaces;
    if (const auto error = spaces.add(description.space)) {
        TerrainError result{TerrainErrorCode::invalid_space, error->message, error};
        return result;
    }
    if (description.space.size.width % terrain_cell_ticks != 0 ||
        description.space.size.height % terrain_cell_ticks != 0) {
        return fail(TerrainErrorCode::invalid_space_size,
                    "Space size must be an exact multiple of one meter");
    }
    const std::uint64_t raw_columns =
        static_cast<std::uint64_t>(description.space.size.width / terrain_cell_ticks);
    const std::uint64_t raw_rows =
        static_cast<std::uint64_t>(description.space.size.height / terrain_cell_ticks);
    if (raw_columns == 0 || raw_rows == 0) {
        return fail(TerrainErrorCode::invalid_space_size,
                    "Terrain grid must contain at least one cell");
    }
    if (raw_columns > std::numeric_limits<std::size_t>::max() ||
        raw_rows > std::numeric_limits<std::size_t>::max()) {
        return fail(TerrainErrorCode::grid_overflow,
                    "Terrain grid dimensions exceed the addressable cell count");
    }
    const std::size_t grid_columns = static_cast<std::size_t>(raw_columns);
    const std::size_t grid_rows = static_cast<std::size_t>(raw_rows);
    if (grid_rows > std::numeric_limits<std::size_t>::max() / grid_columns) {
        return fail(TerrainErrorCode::grid_overflow,
                    "Terrain grid dimensions exceed the addressable cell count");
    }
    columns = grid_columns;
    rows = grid_rows;
    return std::nullopt;
}

}  // namespace

std::optional<TerrainError> validate_terrain(const TerrainDescription& description) {
    if (const auto error = parameters_valid(description.parameters)) return error;
    std::size_t columns = 0;
    std::size_t rows = 0;
    return grid_dimensions(description, columns, rows);
}

Terrain::Terrain(const TerrainDescription& description) : description_(description) {
    if (const auto error = validate_terrain(description)) {
        throw std::invalid_argument(error->message);
    }
    columns_ = static_cast<std::size_t>(description.space.size.width / terrain_cell_ticks);
    rows_ = static_cast<std::size_t>(description.space.size.height / terrain_cell_ticks);
    if (const auto error = spaces_.add(description_.space)) {
        throw std::logic_error(error->message);
    }
}

Elevation Terrain::generate_base(std::size_t column, std::size_t row) const noexcept {
    const std::int64_t x =
        space().origin().x + static_cast<std::int64_t>(column) * terrain_cell_ticks;
    const std::int64_t y =
        space().origin().y + static_cast<std::int64_t>(row) * terrain_cell_ticks;
    std::int64_t accumulated = 0;
    for (std::uint32_t octave = 0; octave < description_.parameters.octaves; ++octave) {
        std::int64_t spacing_cells = description_.parameters.feature_cells >> octave;
        if (spacing_cells < 1) spacing_cells = 1;
        const std::int64_t spacing = spacing_cells * terrain_cell_ticks;
        const std::int64_t amplitude =
            static_cast<std::int64_t>(description_.parameters.amplitude) >> octave;
        if (amplitude == 0) break;
        accumulated +=
            value_noise(description_.parameters.seed, octave, x, y, spacing, amplitude);
    }
    return static_cast<Elevation>(static_cast<std::int64_t>(description_.parameters.base_elevation) +
                                  accumulated);
}

std::optional<std::pair<std::size_t, std::size_t>> Terrain::cell_of(
    const AbsolutePosition& point) const noexcept {
    if (!space().contains(point)) return std::nullopt;
    const Tick local_x = point.x - space().origin().x;
    const Tick local_y = point.y - space().origin().y;
    return std::make_pair(static_cast<std::size_t>(local_x / terrain_cell_ticks),
                          static_cast<std::size_t>(local_y / terrain_cell_ticks));
}

std::optional<LocalPosition> Terrain::cell_origin(std::size_t column,
                                                  std::size_t row) const noexcept {
    if (!in_bounds(column, row)) return std::nullopt;
    return LocalPosition{static_cast<Tick>(column) * terrain_cell_ticks,
                         static_cast<Tick>(row) * terrain_cell_ticks};
}

std::optional<AbsolutePosition> Terrain::cell_origin_absolute(std::size_t column,
                                                              std::size_t row) const noexcept {
    const auto local = cell_origin(column, row);
    if (!local.has_value()) return std::nullopt;
    return AbsolutePosition{space().origin().x + local->x, space().origin().y + local->y};
}

std::optional<std::uint64_t> Terrain::flat_index(std::size_t column,
                                                 std::size_t row) const noexcept {
    if (!in_bounds(column, row)) return std::nullopt;
    return static_cast<std::uint64_t>(row) * columns_ + column;
}

std::optional<std::pair<std::size_t, std::size_t>> Terrain::cell_of_flat(
    std::uint64_t index) const noexcept {
    if (columns_ == 0) return std::nullopt;
    const std::uint64_t column = index % columns_;
    const std::uint64_t row = index / columns_;
    if (row >= rows_) return std::nullopt;
    return std::make_pair(static_cast<std::size_t>(column), static_cast<std::size_t>(row));
}

std::optional<Elevation> Terrain::base_elevation(std::size_t column,
                                                 std::size_t row) const noexcept {
    if (!in_bounds(column, row)) return std::nullopt;
    return generate_base(column, row);
}

std::optional<Elevation> Terrain::elevation(std::size_t column, std::size_t row) const noexcept {
    if (!in_bounds(column, row)) return std::nullopt;
    const auto found = edits_.find(static_cast<std::uint64_t>(row) * columns_ + column);
    if (found != edits_.end()) return found->second;
    return generate_base(column, row);
}

std::optional<std::int64_t> Terrain::slope(std::size_t column, std::size_t row) const noexcept {
    const auto value = elevation(column, row);
    if (!value.has_value()) return std::nullopt;
    std::int64_t maximum = 0;
    const auto compare = [&](std::size_t other_column, std::size_t other_row) {
        const auto other = elevation(other_column, other_row);
        if (!other.has_value()) return;
        std::int64_t difference = static_cast<std::int64_t>(*value) - *other;
        if (difference < 0) difference = -difference;
        maximum = std::max(maximum, difference);
    };
    if (column > 0) compare(column - 1, row);
    if (column + 1 < columns_) compare(column + 1, row);
    if (row > 0) compare(column, row - 1);
    if (row + 1 < rows_) compare(column, row + 1);
    return maximum;
}

bool Terrain::set_elevation(std::size_t column, std::size_t row, Elevation value) {
    const auto index = flat_index(column, row);
    if (!index.has_value()) return false;
    edits_[*index] = value;
    return true;
}

bool Terrain::clear_elevation(std::size_t column, std::size_t row) {
    const auto index = flat_index(column, row);
    if (!index.has_value()) return false;
    return edits_.erase(*index) != 0;
}

void Terrain::ensure_base_statistics() const {
    if (base_statistics_ready_) return;
    base_min_elevation_ = 0;
    base_max_elevation_ = 0;
    base_elevation_sum_ = 0;
    base_elevation_sum_saturated_ = false;
    base_checksum_ = checksum_offset;
    bool first = true;
    for (std::size_t row = 0; row < rows_; ++row) {
        for (std::size_t column = 0; column < columns_; ++column) {
            const Elevation elevation = generate_base(column, row);
            if (first) {
                base_min_elevation_ = elevation;
                base_max_elevation_ = elevation;
                first = false;
            } else {
                base_min_elevation_ = std::min(base_min_elevation_, elevation);
                base_max_elevation_ = std::max(base_max_elevation_, elevation);
            }
            if (!base_elevation_sum_saturated_) {
                constexpr std::int64_t largest = std::numeric_limits<std::int64_t>::max();
                constexpr std::int64_t smallest = std::numeric_limits<std::int64_t>::min();
                if (elevation > 0 && base_elevation_sum_ > largest - elevation) {
                    base_elevation_sum_ = largest;
                    base_elevation_sum_saturated_ = true;
                } else if (elevation < 0 && base_elevation_sum_ < smallest - elevation) {
                    base_elevation_sum_ = smallest;
                    base_elevation_sum_saturated_ = true;
                } else {
                    base_elevation_sum_ += elevation;
                }
            }
            base_checksum_ = elevation_checksum(base_checksum_, elevation);
        }
    }
    // 保持为非负的 63 位值，便于进入 Config 的有符号整数状态。
    base_checksum_ &= 0x7fffffffffffffffULL;
    base_statistics_ready_ = true;
}

Elevation Terrain::base_min_elevation() const {
    ensure_base_statistics();
    return base_min_elevation_;
}

Elevation Terrain::base_max_elevation() const {
    ensure_base_statistics();
    return base_max_elevation_;
}

std::int64_t Terrain::base_elevation_sum() const {
    ensure_base_statistics();
    return base_elevation_sum_;
}

bool Terrain::base_elevation_sum_saturated() const {
    ensure_base_statistics();
    return base_elevation_sum_saturated_;
}

std::uint64_t Terrain::base_checksum() const {
    ensure_base_statistics();
    return base_checksum_;
}

std::vector<Elevation> Terrain::materialize_base() const {
    std::vector<Elevation> result(columns_ * rows_);
    for (std::size_t row = 0; row < rows_; ++row) {
        for (std::size_t column = 0; column < columns_; ++column) {
            result[row * columns_ + column] = generate_base(column, row);
        }
    }
    return result;
}

}  // namespace olam
