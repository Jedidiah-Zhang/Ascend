#include <olam/terrain.hpp>

#include <algorithm>
#include <cstdint>
#include <functional>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using namespace olam;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

TerrainDescription description(std::string id, std::int64_t columns, std::int64_t rows,
                               Tick origin_x = 0, Tick origin_y = 0,
                               TerrainParameters parameters = {}) {
    TerrainDescription result;
    result.space.id = SpaceId{std::move(id)};
    result.space.layer = 0;
    result.space.origin = AbsolutePosition{origin_x, origin_y};
    result.space.size = Size{columns * terrain_cell_ticks, rows * terrain_cell_ticks};
    result.parameters = parameters;
    return result;
}

void create() {
    const Terrain terrain(description("field", 3, 2));
    CHECK(terrain.columns() == 3);
    CHECK(terrain.rows() == 2);
    CHECK(terrain.cell_ticks() == 1000);
    CHECK(terrain.space().id() == SpaceId{"field"});
    CHECK(terrain.space().size() == Size{3000, 2000});
    CHECK(terrain.base_cell_count() == 6);
    CHECK(terrain.edit_count() == 0);

    // 1 米格内的小数位置仍属于同一格；格边界左闭右开。
    CHECK(terrain.cell_of({0, 0}) == std::make_pair<std::size_t, std::size_t>(0, 0));
    CHECK(terrain.cell_of({999, 999}) == std::make_pair<std::size_t, std::size_t>(0, 0));
    CHECK(terrain.cell_of({1000, 0}) == std::make_pair<std::size_t, std::size_t>(1, 0));
    CHECK(terrain.cell_of({0, 1000}) == std::make_pair<std::size_t, std::size_t>(0, 1));
    CHECK(!terrain.cell_of({3000, 0}));
    CHECK(!terrain.cell_of({0, 2000}));
    CHECK(terrain.cell_origin(2, 1) == LocalPosition{2000, 1000});
    CHECK(terrain.cell_origin_absolute(2, 1) == AbsolutePosition{2000, 1000});
    CHECK(!terrain.cell_origin(3, 0));
    CHECK(terrain.flat_index(2, 1) == std::uint64_t{5});
    CHECK(terrain.cell_of_flat(5) == std::make_pair<std::size_t, std::size_t>(2, 1));
    CHECK(!terrain.cell_of_flat(6));
    CHECK(terrain.base_elevation(0, 0).has_value());
    CHECK(terrain.elevation(0, 0) == terrain.base_elevation(0, 0));
    CHECK(!terrain.base_elevation(3, 0));
    CHECK(!terrain.elevation(0, 2));

    // 非零原点只影响绝对坐标，格内归属不变。
    const Terrain offset(description("offset", 2, 2, 100, 200, TerrainParameters{0, 1, 2, 0, 0}));
    CHECK(offset.cell_of({100, 200}) == std::make_pair<std::size_t, std::size_t>(0, 0));
    CHECK(offset.cell_of({1099, 1199}) == std::make_pair<std::size_t, std::size_t>(0, 0));
    CHECK(offset.cell_of({1100, 1200}) == std::make_pair<std::size_t, std::size_t>(1, 1));
    CHECK(offset.cell_of({99, 200}) == std::nullopt);
    CHECK(offset.cell_origin_absolute(1, 1) == AbsolutePosition{1100, 1200});
}

void generate() {
    // 相同描述逐位一致；不同种子不同。
    const auto first = description("first", 32, 24, 0, 0,
                                   TerrainParameters{12345, 4, 8, 0, 5000});
    const auto second = description("second", 32, 24, 0, 0,
                                    TerrainParameters{12345, 4, 8, 0, 5000});
    Terrain a(first);
    Terrain b(second);
    CHECK(a.base_checksum() == b.base_checksum());
    CHECK(a.materialize_base() == b.materialize_base());
    CHECK(a.base_min_elevation() == b.base_min_elevation());
    CHECK(a.base_max_elevation() == b.base_max_elevation());
    CHECK(a.base_elevation_sum() == b.base_elevation_sum());

    auto other = first;
    other.parameters.seed = 54321;
    Terrain c(other);
    CHECK(c.base_checksum() != a.base_checksum());

    // 零振幅时所有格等于基准高程。
    auto flat = description("flat", 7, 3, 0, 0,
                            TerrainParameters{9, 1, 4, -250, 0});
    Terrain constant(flat);
    CHECK(constant.base_min_elevation() == -250);
    CHECK(constant.base_max_elevation() == -250);
    CHECK(constant.base_elevation_sum() == -250 * 21);
    for (std::size_t row = 0; row < constant.rows(); ++row) {
        for (std::size_t column = 0; column < constant.columns(); ++column) {
            CHECK(constant.base_elevation(column, row) == Elevation{-250});
        }
    }

    // 按需查询与物化结果一致；世界尺寸不写死。
    for (const auto& dimensions : {std::pair<std::int64_t, std::int64_t>{1, 1},
                                   {7, 3}, {128, 64}}) {
        const Terrain terrain(description("size", dimensions.first, dimensions.second, 0, 0,
                                          TerrainParameters{77, 5, 4, 100, 2000}));
        const auto materialized = terrain.materialize_base();
        CHECK(materialized.size() ==
              static_cast<std::size_t>(dimensions.first * dimensions.second));
        for (std::size_t row = 0; row < terrain.rows(); ++row) {
            for (std::size_t column = 0; column < terrain.columns(); ++column) {
                CHECK(materialized[row * terrain.columns() + column] ==
                      terrain.base_elevation(column, row));
            }
        }
    }
}

void edits() {
    // 零振幅提供确定的基础面，便于验证覆盖与坡度。
    auto base = description("edits", 3, 3, 0, 0,
                            TerrainParameters{1, 1, 1, 0, 0});
    Terrain terrain(base);
    CHECK(terrain.elevation(1, 1) == Elevation{0});
    CHECK(terrain.set_elevation(1, 1, 1000));
    CHECK(terrain.elevation(1, 1) == Elevation{1000});
    CHECK(terrain.base_elevation(1, 1) == Elevation{0});
    CHECK(terrain.edit_count() == 1);
    CHECK(terrain.edits().count(4) == 1);
    CHECK(terrain.slope(1, 1) == 1000);
    CHECK(terrain.slope(0, 0) == 0);

    // 相邻格覆盖会影响两边坡度；清除后回落。
    CHECK(terrain.set_elevation(2, 1, 3000));
    CHECK(terrain.slope(1, 1) == 2000);
    CHECK(terrain.clear_elevation(2, 1));
    CHECK(!terrain.clear_elevation(2, 1));
    CHECK(terrain.slope(1, 1) == 1000);
    CHECK(terrain.elevation(2, 1) == Elevation{0});

    // 越界修改被拒绝且不改变状态。
    CHECK(!terrain.set_elevation(3, 0, 1));
    CHECK(!terrain.set_elevation(0, 3, 1));
    CHECK(!terrain.clear_elevation(3, 0));
    CHECK(terrain.edit_count() == 1);

    // 修改不改变基础统计与校验和。
    const auto minimum = terrain.base_min_elevation();
    const auto maximum = terrain.base_max_elevation();
    const auto sum = terrain.base_elevation_sum();
    const auto checksum = terrain.base_checksum();
    CHECK(terrain.set_elevation(0, 0, -5000));
    CHECK(terrain.base_min_elevation() == minimum);
    CHECK(terrain.base_max_elevation() == maximum);
    CHECK(terrain.base_elevation_sum() == sum);
    CHECK(terrain.base_checksum() == checksum);
    CHECK(terrain.base_elevation(0, 0) == Elevation{0});
}

void bounds() {
    Terrain terrain(description("bounds", 4, 4, 0, 0,
                                TerrainParameters{1, 1, 1, 0, 0}));
    // 四邻域与边界：角落只有两个邻居；单格世界坡度为 0。
    CHECK(terrain.set_elevation(0, 0, 500));
    CHECK(terrain.set_elevation(1, 0, 1000));
    CHECK(terrain.set_elevation(0, 1, 1500));
    CHECK(terrain.slope(0, 0) == 1000);  // 与 (1,0) 差 500、(0,1) 差 1000
    CHECK(terrain.slope(1, 0) == 1000);
    CHECK(terrain.slope(3, 3) == 0);

    const Terrain single(description("single", 1, 1, 0, 0,
                                     TerrainParameters{1, 1, 1, 10, 0}));
    CHECK(single.slope(0, 0) == 0);
    CHECK(single.cell_of({0, 0}) == std::make_pair<std::size_t, std::size_t>(0, 0));
    CHECK(single.cell_of({999, 999}) == std::make_pair<std::size_t, std::size_t>(0, 0));
    CHECK(!single.cell_of({1000, 0}));
}

void errors() {
    const auto expect = [](TerrainErrorCode code, TerrainDescription value) {
        const auto error = validate_terrain(value);
        CHECK(error.has_value());
        CHECK(error->code == code);
        CHECK(!error->message.empty());
    };

    auto invalid = description("invalid", 2, 2);
    invalid.space.size.width = 2500;  // 不是 1 米整数倍
    expect(TerrainErrorCode::invalid_space_size, invalid);

    auto invalid_height = description("invalid-height", 2, 2);
    invalid_height.space.size.height = 2500;
    expect(TerrainErrorCode::invalid_space_size, invalid_height);

    auto zero = description("zero", 2, 2);
    zero.space.size.width = 0;
    expect(TerrainErrorCode::invalid_space, zero);

    auto zero_height = description("zero-height", 2, 2);
    zero_height.space.size.height = 0;
    expect(TerrainErrorCode::invalid_space, zero_height);

    // 空间尺寸合法但格数超出可寻址范围。
    auto overflow = description("overflow", 1, 1);
    constexpr Tick largest = std::numeric_limits<Tick>::max();
    const Tick edge = largest - (largest % terrain_cell_ticks);
    overflow.space.size = Size{edge, edge};
    expect(TerrainErrorCode::grid_overflow, overflow);

    auto negative = description("negative", 2, 2);
    negative.space.origin.x = -1;
    expect(TerrainErrorCode::invalid_space, negative);

    auto no_id = description("", 2, 2);
    expect(TerrainErrorCode::invalid_space, no_id);

    auto octaves = description("octaves", 2, 2);
    octaves.parameters.octaves = 0;
    expect(TerrainErrorCode::invalid_parameters, octaves);
    octaves.parameters.octaves = 9;
    expect(TerrainErrorCode::invalid_parameters, octaves);

    auto feature = description("feature", 2, 2);
    feature.parameters.feature_cells = 0;
    expect(TerrainErrorCode::invalid_parameters, feature);

    auto amplitude = description("amplitude", 2, 2);
    amplitude.parameters.amplitude = -1;
    expect(TerrainErrorCode::invalid_parameters, amplitude);

    auto range = description("range", 2, 2);
    range.parameters.base_elevation = std::numeric_limits<Elevation>::max();
    range.parameters.amplitude = 1000;
    expect(TerrainErrorCode::elevation_range, range);

    // 合法描述不报错。
    CHECK(!validate_terrain(description("valid", 3, 2)));
}

}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"create", create}, {"generate", generate}, {"edits", edits},
        {"bounds", bounds}, {"errors", errors},
    };
    if (argc != 2 || tests.count(argv[1]) == 0) return 2;
    try {
        tests.at(argv[1])();
        std::cout << argv[1] << ": passed\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << argv[1] << ": " << error.what() << '\n';
        return 1;
    }
}
