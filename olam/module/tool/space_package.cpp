// 生成工作台可载入的空间模块包：ascend_olam_space_package <输出目录> [语言资源文件]
// 输出目录不存在时创建；包文件名固定为 olam.space.amod。
#include <olam/module/space_module.hpp>

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

}  // namespace

int main(int argc, char** argv) {
    try {
        if (argc > 3) {
            std::cerr << "usage: ascend_olam_space_package <output-directory> [i18n-json]\n";
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
            resources.push_back({olam::module::space_text_domain, "zh-CN", std::move(text)});
        }

        const std::string bytes = olam::module::space_package_bytes(std::move(resources));

        // 先按宿主实际载入路径校验清单与资源，避免生成“成功但不可载入”的包。
        ascend::ModuleLibrary library;
        olam::module::register_space_module(library);
        library.load(bytes);

        // 先写临时文件，再替换目标；失败时不留下半成品。
        const std::filesystem::path target = directory / "olam.space.amod";
        const std::filesystem::path temporary = directory / "olam.space.amod.tmp";
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
            // Windows 不允许 rename 覆盖已有文件；只有目标存在时才尝试替换。
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
        std::cerr << "space package failed: " << error.what() << '\n';
        return 1;
    }
}
