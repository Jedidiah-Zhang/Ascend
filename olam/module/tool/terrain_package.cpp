// 生成工作台可载入的地形模块包：ascend_olam_terrain_package <输出目录> [语言资源文件] [插件二进制]
// 输出目录不存在时创建；包文件名固定为 olam.terrain.amod。
#include <olam/module/terrain_module.hpp>

#include <ascend/plugin.h>

#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string>
#include <vector>

namespace {

bool read_text(const std::filesystem::path& path, std::string& text) {
    std::ifstream stream(path, std::ios::binary);
    if (!stream.is_open()) return false;
    text.assign(std::istreambuf_iterator<char>(stream), std::istreambuf_iterator<char>());
    return !stream.bad();
}

std::string plugin_platform() {
#if defined(_M_ARM64) || defined(__aarch64__)
    const char* architecture = "arm64";
#else
    const char* architecture = "x86_64";
#endif
#if defined(_WIN32)
    return std::string("windows-") + architecture;
#elif defined(__linux__)
    return std::string("linux-") + architecture;
#elif defined(__APPLE__)
    return std::string("macos-") + architecture;
#else
    return "unknown";
#endif
}

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc > 4) {
            std::cerr << "usage: ascend_olam_terrain_package <output-directory> [i18n-json] [plugin-binary]\n";
            return 2;
        }
        const std::filesystem::path directory =
            argc > 1 ? std::filesystem::u8path(argv[1]) : std::filesystem::current_path();
        std::error_code error;
        std::filesystem::create_directories(directory, error);
        if (error) {
            std::cerr << "cannot create output directory: " << directory.u8string() << '\n';
            return 1;
        }

        std::vector<ascend::ModuleResource> resources;
        if (argc > 2) {
            std::string text;
            if (!read_text(std::filesystem::u8path(argv[2]), text)) {
                std::cerr << "cannot read i18n resource: " << argv[2] << '\n';
                return 1;
            }
            resources.push_back({olam::module::terrain_text_domain, "zh-CN", std::move(text)});
        }

        auto package = olam::module::make_terrain_package(std::move(resources));
        if (argc > 3) {
            std::string binary;
            if (!read_text(std::filesystem::u8path(argv[3]), binary)) {
                std::cerr << "cannot read plugin binary: " << argv[3] << '\n';
                return 1;
            }
            package.binary = ascend::ModuleBinary{
                ASCEND_PLUGIN_BINARY_FORMAT, plugin_platform(), ASCEND_PLUGIN_ENTRY_SYMBOL,
                std::move(binary)};
        }
        const std::string bytes = ascend::encode_module_package(package);

        // 先按宿主实际载入路径校验清单与资源，避免生成“成功但不可载入”的包。
        ascend::ModuleLibrary library;
        if (!package.binary.has_value()) olam::module::register_terrain_module(library);
        library.load(bytes);

        // 先写临时文件，再替换目标；失败时不留下半成品。
        const std::filesystem::path target = directory / "olam.terrain.amod";
        const std::filesystem::path temporary = directory / "olam.terrain.amod.tmp";
        {
            std::ofstream stream(temporary, std::ios::binary | std::ios::trunc);
            if (!stream.is_open()) {
                std::cerr << "cannot write module package: " << temporary.u8string() << '\n';
                return 1;
            }
            stream.write(bytes.data(), static_cast<std::streamsize>(bytes.size()));
            stream.close();
            if (!stream) {
                std::filesystem::remove(temporary, error);
                std::cerr << "cannot write module package: " << temporary.u8string() << '\n';
                return 1;
            }
        }
        std::filesystem::rename(temporary, target, error);
        if (error) {
            error.clear();
            if (std::filesystem::exists(target)) std::filesystem::remove(target, error);
            if (!error) std::filesystem::rename(temporary, target, error);
        }
        if (error) {
            std::filesystem::remove(temporary, error);
            std::cerr << "cannot write module package: " << target.u8string() << '\n';
            return 1;
        }
        std::cout << target.u8string() << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "terrain package failed: " << error.what() << '\n';
        return 1;
    }
}
