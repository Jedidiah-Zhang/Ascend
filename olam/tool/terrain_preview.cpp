// 地形预览工具：从生成参数物化基础高程并输出 8 位灰度 PGM。
// 用法：ascend_olam_terrain_preview <output.pgm> [columns] [rows] [seed]
//       [octaves] [feature_cells] [base_elevation] [amplitude]
#include <olam/terrain.hpp>

#include <algorithm>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <fstream>
#include <iostream>
#include <limits>
#include <string>
#include <vector>

namespace {

bool parse_integer(const char* text, std::int64_t& value) {
    if (text == nullptr || *text == '\0') return false;
    errno = 0;
    char* end = nullptr;
    const long long parsed = std::strtoll(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0') return false;
    value = static_cast<std::int64_t>(parsed);
    return true;
}

}  // namespace

int main(int argc, char** argv) {
    if (argc < 2 || argc > 9) {
        std::cerr << "usage: ascend_olam_terrain_preview <output.pgm> [columns] [rows] [seed]"
                     " [octaves] [feature_cells] [base_elevation] [amplitude]\n";
        return 2;
    }
    std::int64_t columns = 512;
    std::int64_t rows = 512;
    std::int64_t seed = 0;
    std::int64_t octaves = 6;
    std::int64_t feature_cells = 128;
    std::int64_t base_elevation = 0;
    std::int64_t amplitude = 150000;
    std::int64_t* targets[] = {&columns, &rows, &seed, &octaves,
                               &feature_cells, &base_elevation, &amplitude};
    for (int index = 2; index < argc; ++index) {
        if (!parse_integer(argv[index], *targets[index - 2])) {
            std::cerr << "invalid integer: " << argv[index] << '\n';
            return 2;
        }
    }
    if (columns <= 0 || rows <= 0 || seed < 0 || octaves < 1 || octaves > 8 ||
        feature_cells < 1 || feature_cells > olam::max_terrain_feature_cells ||
        amplitude < 0 || amplitude > std::numeric_limits<olam::Elevation>::max() ||
        base_elevation < std::numeric_limits<olam::Elevation>::min() ||
        base_elevation > std::numeric_limits<olam::Elevation>::max() ||
        columns > std::numeric_limits<std::int64_t>::max() / olam::terrain_cell_ticks ||
        rows > std::numeric_limits<std::int64_t>::max() / olam::terrain_cell_ticks) {
        std::cerr << "terrain parameters are invalid\n";
        return 2;
    }

    olam::TerrainDescription description;
    description.space.id = olam::SpaceId{"preview"};
    description.space.layer = 0;
    description.space.origin = olam::AbsolutePosition{0, 0};
    description.space.size =
        olam::Size{columns * olam::terrain_cell_ticks, rows * olam::terrain_cell_ticks};
    description.parameters.seed = static_cast<std::uint64_t>(seed);
    description.parameters.octaves = static_cast<std::uint32_t>(octaves);
    description.parameters.feature_cells = feature_cells;
    description.parameters.base_elevation = static_cast<olam::Elevation>(base_elevation);
    description.parameters.amplitude = static_cast<olam::Elevation>(amplitude);

    try {
        const olam::Terrain terrain(description);
        const std::vector<olam::Elevation> elevations = terrain.materialize_base();
        const auto lowest = std::min_element(elevations.begin(), elevations.end());
        const auto highest = std::max_element(elevations.begin(), elevations.end());
        const std::int64_t minimum = lowest == elevations.end() ? 0 : *lowest;
        const std::int64_t maximum = highest == elevations.end() ? 0 : *highest;
        const std::int64_t span = maximum - minimum;

        std::ofstream stream(argv[1], std::ios::binary | std::ios::trunc);
        if (!stream.is_open()) {
            std::cerr << "cannot write output: " << argv[1] << '\n';
            return 1;
        }
        stream << "P5\n" << columns << ' ' << rows << "\n255\n";
        for (const olam::Elevation elevation : elevations) {
            const std::int64_t normalized =
                span == 0 ? 128 : (static_cast<std::int64_t>(elevation) - minimum) * 255 / span;
            const auto value = static_cast<unsigned char>(std::clamp<std::int64_t>(normalized, 0, 255));
            stream.put(static_cast<char>(value));
        }
        if (!stream) {
            std::cerr << "cannot write output: " << argv[1] << '\n';
            return 1;
        }
    } catch (const std::exception& error) {
        std::cerr << "terrain preview failed: " << error.what() << '\n';
        return 1;
    }
    return 0;
}
