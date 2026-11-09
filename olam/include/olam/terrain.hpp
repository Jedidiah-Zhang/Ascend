#pragma once

#include <olam/space.hpp>

#include <cstddef>
#include <cstdint>
#include <map>
#include <optional>
#include <string>
#include <utility>
#include <vector>

namespace olam {

// 地形格固定为 1 米；空间位置与高程均使用毫米。
inline constexpr Tick terrain_cell_ticks = ticks_per_meter;
// 首版分形生成器的算法版本；状态恢复与重放据此拒绝不兼容地形。
inline constexpr std::uint32_t terrain_generator_version = 1;
// 单个噪声特征的最大格数，防止极端参数导致中间定点运算溢出；不是世界尺寸上限。
inline constexpr std::int64_t max_terrain_feature_cells = 1'000'000;

// 高程使用 32 位有符号毫米；与空间刻度同单位，避免换算歧义。
using Elevation = std::int32_t;

// 确定性基础地形参数。相同描述（空间、网格与这些参数）必须生成相同高程。
struct TerrainParameters {
    // 噪声种子的低 64 位；配置层只接受非负整数。
    std::uint64_t seed = 0;
    // 分形噪声的叠加层数，取值 1～8。
    std::uint32_t octaves = 6;
    // 第一层格点间距，单位为格；后续层逐次减半，最小为一格。
    std::int64_t feature_cells = 128;
    // 基准高程，单位毫米。
    Elevation base_elevation = 0;
    // 第一层噪声振幅，单位毫米；后续层逐次减半。
    Elevation amplitude = 150'000;
};

// 一个地形实例的描述：空间与生成参数。空间尺寸必须是 1 米的整数倍。
struct TerrainDescription {
    SpaceDescription space;
    TerrainParameters parameters;
};

enum class TerrainErrorCode {
    invalid_space_size,
    grid_overflow,
    invalid_parameters,
    elevation_range,
    invalid_space,
};

struct TerrainError {
    TerrainErrorCode code = TerrainErrorCode::invalid_parameters;
    std::string message;
    // code 为 invalid_space 时保留空间创建错误的原因链。
    std::optional<SpaceError> space;
};

// 校验空间与生成参数；不生成地形，不改变任何对象。
std::optional<TerrainError> validate_terrain(const TerrainDescription& description);

// 一个空间及其 1 米规则网格地形。
//
// 基础高程由 seed 与参数确定性生成，按需计算，不整体驻留；逐格修改是稀疏状态层。
// 基础高程与逐格覆盖合成为有效地形，覆盖优先。查询结果只取决于描述与修改，不取决于缓存或读取顺序。
class Terrain {
public:
    // 描述非法时抛出 std::invalid_argument；调用方可先用 validate_terrain 取得结构化错误。
    explicit Terrain(const TerrainDescription& description);

    const Space& space() const noexcept { return spaces_.spaces().front(); }
    const SpaceSet& spaces() const noexcept { return spaces_; }
    const TerrainDescription& description() const noexcept { return description_; }

    std::size_t columns() const noexcept { return columns_; }
    std::size_t rows() const noexcept { return rows_; }
    Tick cell_ticks() const noexcept { return terrain_cell_ticks; }

    bool in_bounds(std::size_t column, std::size_t row) const noexcept {
        return column < columns_ && row < rows_;
    }
    // 绝对位置所在格；空间外返回空。格内位置按左闭右开归属。
    std::optional<std::pair<std::size_t, std::size_t>> cell_of(
        const AbsolutePosition& point) const noexcept;
    // 格左上角（局部坐标与绝对坐标）；越界返回空。
    std::optional<LocalPosition> cell_origin(std::size_t column, std::size_t row) const noexcept;
    std::optional<AbsolutePosition> cell_origin_absolute(std::size_t column,
                                                         std::size_t row) const noexcept;
    // 行优先索引：index = row * columns + column；越界返回空。
    std::optional<std::uint64_t> flat_index(std::size_t column,
                                            std::size_t row) const noexcept;
    std::optional<std::pair<std::size_t, std::size_t>> cell_of_flat(
        std::uint64_t index) const noexcept;

    // 基础高程：只由 seed、参数与空间决定，不受修改影响。越界返回空。
    std::optional<Elevation> base_elevation(std::size_t column,
                                            std::size_t row) const noexcept;
    // 有效地形：修改覆盖优先，否则返回基础高程。越界返回空。
    std::optional<Elevation> elevation(std::size_t column, std::size_t row) const noexcept;
    // 四邻域最大高程差，单位毫米/米（即千分比）；边界格只比较存在的邻居。
    // 单格世界与没有邻居的格返回 0。
    std::optional<std::int64_t> slope(std::size_t column, std::size_t row) const noexcept;

    // 逐格绝对覆盖；格越界返回 false。重复设置覆盖旧值。
    bool set_elevation(std::size_t column, std::size_t row, Elevation value);
    // 清除覆盖后回落到基础高程；没有覆盖时返回 false。
    bool clear_elevation(std::size_t column, std::size_t row);

    // 行优先的稀疏修改层；索引为 flat_index。
    const std::map<std::uint64_t, Elevation>& edits() const noexcept { return edits_; }
    std::size_t edit_count() const noexcept { return edits_.size(); }

    // 基础高程统计与校验和；首次访问时计算并缓存，修改不影响这些值。
    Elevation base_min_elevation() const;
    Elevation base_max_elevation() const;
    // 高程总和使用饱和累计；溢出时 pinned 到 64 位边界并由 saturated 标记。
    std::int64_t base_elevation_sum() const;
    bool base_elevation_sum_saturated() const;
    std::uint64_t base_checksum() const;
    std::size_t base_cell_count() const noexcept { return columns_ * rows_; }

    // 行优先物化基础高程；供测试、预览与性能缓存使用。结果不进入状态。
    std::vector<Elevation> materialize_base() const;

private:
    Elevation generate_base(std::size_t column, std::size_t row) const noexcept;
    void ensure_base_statistics() const;

    TerrainDescription description_;
    SpaceSet spaces_;
    std::size_t columns_ = 0;
    std::size_t rows_ = 0;
    std::map<std::uint64_t, Elevation> edits_;

    mutable bool base_statistics_ready_ = false;
    mutable Elevation base_min_elevation_ = 0;
    mutable Elevation base_max_elevation_ = 0;
    mutable std::int64_t base_elevation_sum_ = 0;
    mutable bool base_elevation_sum_saturated_ = false;
    mutable std::uint64_t base_checksum_ = 0;
};

}  // namespace olam
