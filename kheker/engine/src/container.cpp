#include "container.hpp"

#include <ascend/engine.hpp>

#include <cstddef>
#include <set>
#include <string>
#include <utility>

namespace ascend::detail {
namespace {

constexpr char kMagic[] = {'A', 'S', 'C', 'E', 'N', 'D'};
constexpr std::uint8_t kContainerVersion = 1;
constexpr std::size_t kMaxVarintBytes = 10;

[[noreturn]] void fail(ErrorCode code, const std::string& key, const char* fallback) {
    throw EngineError({code, {}, TextRef(TextKey{engine_text_domain, key}, std::string(fallback))});
}

void write_varint(std::string& out, std::uint64_t value) {
    while (value >= 0x80) {
        out.push_back(static_cast<char>((value & 0x7f) | 0x80));
        value >>= 7;
    }
    out.push_back(static_cast<char>(value));
}

struct Reader {
    const std::string& data;
    std::size_t pos = 0;
    const std::string& prefix;

    [[noreturn]] void corrupt() const {
        fail(ErrorCode::invalid_json, prefix + ".corrupt", "File is truncated");
    }

    void need(std::size_t count) const {
        if (pos > data.size() || count > data.size() - pos) corrupt();
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
                fail(ErrorCode::invalid_json, prefix + ".corrupt", "File varint overflows 64 bits");
            }
            value |= static_cast<std::uint64_t>(byte & 0x7f) << (7 * index);
            if ((byte & 0x80) == 0) return value;
        }
        fail(ErrorCode::invalid_json, prefix + ".corrupt", "File varint is too long");
    }

    std::string bytes_n(std::uint64_t size) {
        need(size);
        std::string value = data.substr(pos, static_cast<std::size_t>(size));
        pos += static_cast<std::size_t>(size);
        return value;
    }
};

}  // namespace

std::string write_container(std::uint8_t kind, const std::vector<ContainerSection>& sections) {
    std::string out(kMagic, sizeof(kMagic));
    out.push_back(static_cast<char>(kContainerVersion));
    out.push_back(static_cast<char>(kind));
    write_varint(out, sections.size());
    for (const auto& section : sections) {
        write_varint(out, section.id);
        out.push_back(static_cast<char>(section.flags));
        write_varint(out, section.data.size());
        out += section.data;
    }
    return out;
}

std::vector<ContainerSection> read_container(const std::string& bytes, std::uint8_t expected_kind,
                                             const char* key_prefix) {
    const std::string prefix(key_prefix);
    Reader reader{bytes, 0, prefix};
    for (const char expected : kMagic) {
        if (reader.u8() != static_cast<std::uint8_t>(expected)) {
            fail(ErrorCode::invalid_json, prefix + ".magic",
                 "File does not start with the Ascend magic");
        }
    }
    if (reader.u8() != kContainerVersion) {
        fail(ErrorCode::unsupported_format_version, prefix + ".version",
             "File container version is not supported");
    }
    if (reader.u8() != expected_kind) {
        fail(ErrorCode::invalid_json, prefix + ".kind", "File kind is not supported");
    }
    const auto count = reader.varint();
    std::vector<ContainerSection> sections;
    std::set<std::uint64_t> seen;
    for (std::uint64_t index = 0; index < count; ++index) {
        ContainerSection section;
        section.id = reader.varint();
        section.flags = reader.u8();
        const auto size = reader.varint();
        section.data = reader.bytes_n(size);
        if (!seen.insert(section.id).second) {
            fail(ErrorCode::invalid_json, prefix + ".section", "File repeats a section");
        }
        sections.push_back(std::move(section));
    }
    if (reader.pos != bytes.size()) {
        fail(ErrorCode::invalid_json, prefix + ".section", "File has trailing bytes");
    }
    return sections;
}

}  // namespace ascend::detail
