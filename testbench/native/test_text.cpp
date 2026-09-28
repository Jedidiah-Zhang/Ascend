#include <ascend/assembly.hpp>
#include <ascend/i18n.hpp>

#include <chrono>
#include <filesystem>
#include <fstream>
#include <functional>
#include <iostream>
#include <map>
#include <optional>
#include <random>
#include <stdexcept>
#include <string>
#include <vector>

namespace {
using namespace ascend;

void require(bool condition, int line) {
    if (!condition) throw std::runtime_error("check failed at line " + std::to_string(line));
}
#define CHECK(...) require((__VA_ARGS__), __LINE__)

template <class F>
Diagnostic failure(ErrorCode code, F&& function) {
    try { function(); }
    catch (const EngineError& error) {
        CHECK(error.diagnostic().code == code);
        CHECK(!render_diagnostic(error.diagnostic()).empty());
        return error.diagnostic();
    }
    throw std::runtime_error("expected EngineError");
}

template <class F>
void invalid_argument(F&& function) {
    try { function(); }
    catch (const std::invalid_argument&) { return; }
    throw std::runtime_error("expected invalid_argument");
}

class TempDirectory {
public:
    TempDirectory() {
        std::random_device random;
        const auto stamp = std::chrono::steady_clock::now().time_since_epoch().count();
        for (int attempt = 0; attempt < 100; ++attempt) {
            const auto candidate = std::filesystem::temp_directory_path() /
                ("ascend-i18n-" + std::to_string(stamp) + "-" + std::to_string(random()));
            std::error_code error;
            if (std::filesystem::create_directory(candidate, error)) { path = candidate; return; }
        }
        throw std::runtime_error("cannot create temporary directory");
    }
    ~TempDirectory() { std::error_code error; std::filesystem::remove_all(path, error); }
    TempDirectory(const TempDirectory&) = delete;
    TempDirectory& operator=(const TempDirectory&) = delete;
    std::filesystem::path path;
};

void write_file(const std::filesystem::path& path, const std::string& text) {
    std::ofstream stream(path, std::ios::binary | std::ios::trunc);
    stream << text;
    stream.close();
    CHECK(static_cast<bool>(stream));
}

std::string pack(const std::string& domain, const std::string& locale, const std::string& entries) {
    return "{\"format\":\"ascend.i18n\",\"version\":1,\"domain\":\"" + domain +
        "\",\"locale\":\"" + locale + "\",\"entries\":" + entries + "}";
}

TextRef message(std::string key, std::optional<std::string> fallback = std::nullopt,
                TextRef::Arguments args = {}) {
    return {{"test", std::move(key)}, std::move(fallback), std::move(args)};
}

void define_resolve() {
    TextCatalog catalog;
    catalog.define("xx", {"test", "greeting"}, "hello");
    CHECK(catalog.contains("xx", {"test", "greeting"}));
    CHECK(catalog.resolve("xx", message("greeting")) == "hello");
    CHECK(catalog.resolve("yy", message("greeting", "fallback")) == "fallback");
    CHECK(catalog.resolve("yy", message("greeting")) == "test:greeting");
    CHECK(catalog.resolve("yy", message("greeting", "")).empty());
    CHECK(catalog.resolve("xx", TextRef("{greeting}")) == "{greeting}");
    catalog.define("xx", {"test", "blank"}, "");
    CHECK(catalog.resolve("xx", message("blank", "fallback")).empty());
    CHECK(catalog.locales() == std::vector<std::string>{"xx"});
    failure(ErrorCode::invalid_i18n, [&] { catalog.define("", {"test", "key"}, "x"); });
    failure(ErrorCode::invalid_i18n, [&] { catalog.define("xx", {"", "key"}, "x"); });
    failure(ErrorCode::invalid_i18n, [&] { catalog.define("xx", {"test", ""}, "x"); });
}

void locale_fallback() {
    TextCatalog catalog;
    catalog.define("zh-CN", {"test", "key"}, "exact");
    catalog.define("zh", {"test", "key"}, "primary");
    catalog.define("en-US", {"test", "default"}, "default");
    catalog.define("en", {"test", "base"}, "default-primary");
    catalog.set_default_locale("en-US");
    CHECK(catalog.resolve("zh-CN", message("key")) == "exact");
    CHECK(catalog.resolve("zh-TW", message("key")) == "primary");
    CHECK(catalog.resolve("fr", message("default")) == "default");
    CHECK(catalog.resolve("fr", message("base")) == "default-primary");
    CHECK(catalog.resolve("en-US", message("base")) == "default-primary");
    CHECK(catalog.resolve("", message("default")) == "default");
    catalog.set_default_locale("");
    CHECK(catalog.resolve("fr", message("default", "fallback")) == "fallback");
}

void placeholders() {
    CHECK(render_text(message("pair", "{a}|{b}|{a}|{missing}", {{"a", "A"}, {"b", "B"}})) ==
          "A|B|A|{missing}");
    CHECK(render_text(message("braces", "literal { and } then {x}", {{"x", "X"}})) ==
          "literal { and } then X");
    invalid_argument([] { message("x", "{a}", {{"a", "1"}, {"a", "2"}}); });
    invalid_argument([] { message("x", "", {{"", "1"}}); });
    invalid_argument([] { message("x", "", {{"a{b}", "1"}}); });
    invalid_argument([] { TextRef(TextKey{"", "x"}, "fallback"); });
}

void parameter_literals() {
    const auto first = message("pair", "{a}|{b}", {{"a", "{b}"}, {"b", "B"}});
    const auto second = message("pair", "{a}|{b}", {{"b", "B"}, {"a", "{b}"}});
    CHECK(render_text(first) == "{b}|B");
    CHECK(render_text(second) == render_text(first));
    const auto diagnostic = failure(ErrorCode::invalid_json, [] {
        AssemblyDefinition::parse(R"({"{line}":1,"{line}":2})");
    });
    CHECK(render_text(diagnostic.text).find("'{line}'") != std::string::npos);
    CHECK(render_text(message("unicode", "{x}", {{"x", "路径/{line}/~"}})) == "路径/{line}/~");
}

void fallback_once() {
    const auto d = failure(ErrorCode::unknown_definition, [] {
        ModuleFactoryDirectory{}.create("custom.{definition}", "instance", {});
    });
    TextCatalog catalog;
    CHECK(catalog.resolve("en", d.text) == render_text(d.text));
    CHECK(render_text(d.text) == "Module definition 'custom.{definition}' is not registered");
    catalog.define("xx", d.text.key(), "unknown: {definition}");
    CHECK(catalog.resolve("xx", d.text) == "unknown: custom.{definition}");
    CHECK(catalog.resolve("en", d.text) == render_text(d.text));
    CHECK(catalog.resolve("xx", TextRef("author {definition}")) == "author {definition}");
}

void load_merge() {
    TempDirectory temp;
    const auto first = temp.path / "first.json", second = temp.path / "second.json";
    write_file(first, pack("test", "xx", R"({"one":"1","two":"2"})"));
    write_file(second, pack("test", "xx", R"({"new":"N","two":"new2"})"));
    TextCatalog catalog;
    catalog.load({"test", first.string()});
    auto d = failure(ErrorCode::invalid_i18n, [&] { catalog.load({"test", second.string()}); });
    CHECK(d.path == "/entries/two");
    CHECK(!catalog.contains("xx", {"test", "new"}));
    CHECK(catalog.resolve("xx", message("two")) == "2");
    catalog.load({"test", second.string()}, TextConflict::replace);
    CHECK(catalog.resolve("xx", message("one")) == "1");
    CHECK(catalog.resolve("xx", message("two")) == "new2");
    CHECK(catalog.resolve("xx", message("new")) == "N");
    failure(ErrorCode::invalid_i18n, [&] { catalog.define("xx", {"test", "one"}, "other"); });
    catalog.define("xx", {"test", "one"}, "other", TextConflict::replace);
    CHECK(catalog.resolve("xx", message("one")) == "other");

    // 空包登记语言，但不承诺该语言已有条目或完整翻译。
    const auto empty = temp.path / "empty.json";
    write_file(empty, pack("test", "fr", "{}"));
    catalog.load({"test", empty.string()});
    CHECK(catalog.locales() == std::vector<std::string>({"fr", "xx"}));
    CHECK(!catalog.contains("fr", {"test", "one"}));
    CHECK(catalog.resolve("fr", message("one", "fallback")) == "fallback");
    catalog.set_default_locale("xx");
    CHECK(catalog.resolve("fr", message("one", "fallback")) == "other");
    catalog.set_default_locale("de");
    CHECK(catalog.locales() == std::vector<std::string>({"fr", "xx"}));
}

void load_errors() {
    TempDirectory temp;
    TextCatalog catalog;
    const auto file = temp.path / "catalog.json";
    CHECK(failure(ErrorCode::io_failure, [&] { catalog.load({"test", file.string()}); }).source == file.string());
    // 目录可以在部分平台打开，但不能作为普通文件读取；均应报告 I/O 错误。
    failure(ErrorCode::io_failure, [&] { catalog.load({"test", temp.path.string()}); });
    write_file(file, "{\"format\":");
    CHECK(failure(ErrorCode::invalid_json, [&] { catalog.load({"test", file.string()}); }).source == file.string());
    const std::vector<std::pair<std::string, std::string>> bad = {
        {"[]", ""}, {R"({"format":"other"})", "/format"},
        {R"({"format":"ascend.i18n","version":"1"})", "/version"},
        {R"({"format":"ascend.i18n","version":1,"domain":"other"})", "/domain"},
        {pack("test", "", "{}"), "/locale"}, {pack("test", "xx", "[]"), "/entries"},
        {pack("test", "xx", R"({"good":"G","bad/~":1})"), "/entries/bad~1~0"},
        {pack("test", "xx", R"({"":"empty key"})"), "/entries/"},
        {R"({"extra":1})", "/extra"}, {"{}", "/format"},
        {R"({"format":"ascend.i18n"})", "/version"},
        {R"({"format":"ascend.i18n","version":1})", "/domain"},
        {R"({"format":"ascend.i18n","version":1,"domain":"test"})", "/locale"},
        {R"({"format":"ascend.i18n","version":1,"domain":"test","locale":"xx"})", "/entries"}
    };
    for (const auto& item : bad) {
        write_file(file, item.first);
        const auto d = failure(ErrorCode::invalid_i18n, [&] { catalog.load({"test", file.string()}); });
        CHECK(d.path == item.second);
        CHECK(!catalog.contains("xx", {"test", "good"}));
    }
    write_file(file, R"({"format":"ascend.i18n","version":2})");
    CHECK(failure(ErrorCode::unsupported_format_version, [&] { catalog.load({"test", file.string()}); }).path == "/version");
}

void diagnostic_texts() {
    Module module("m");
    module.add_method<void, int>("run", {"x"}, [](int) {});
    module.add_value<int>("value", [] { return 1; }, message("value", "Value"));
    Engine engine;
    engine.add(std::move(module));
    engine.seal();
    const auto d = failure(ErrorCode::argument_count, [&] { engine.call({"m", "run"}, {}); });
    CHECK(d.text.key() == TextKey{engine_text_domain, "engine.execution.argument_count"});
    CHECK(render_text(d.text) == "Expected 1 arguments, received 0");
    TextCatalog catalog;
    catalog.define("xx", d.text.key(), "{expected}|{received}");
    CHECK(catalog.resolve("xx", d.text) == "1|0");
    CHECK(render_diagnostic(d, &catalog, "xx") == "m/run: 1|0");
    CHECK(std::string(EngineError(d).what()) == render_diagnostic(d));
    for (const auto& declaration : engine.catalog()) {
        if (declaration.reference.symbol == "value") CHECK(render_text(declaration.description) == "Value");
    }
}

void nested_messages() {
    TextCatalog catalog;
    catalog.define("xx", {"outer", "failure"}, "outer={reason}");
    catalog.define("xx", {"inner", "failure"}, "inner={name}");
    TextRef inner({"inner", "failure"}, "Inner {name}", {{"name", "{reason}"}});
    TextRef outer({"outer", "failure"}, "Outer {reason}", {{"reason", inner}});
    CHECK(catalog.resolve("xx", outer) == "outer=inner={reason}");
    CHECK(catalog.resolve("yy", outer) == "Outer Inner {reason}");
    TextRef deep = "leaf";
    for (std::size_t i = 0; i < text_max_depth; ++i) deep = message("nested", "{x}", {{"x", deep}});
    invalid_argument([&] { message("nested", "{x}", {{"x", deep}}); });
    CHECK(render_text(deep) == "leaf");
}

void nested_diagnostics() {
    Module provider("provider");
    MethodOptions options;
    options.contract = "fail";
    provider.add_method<void>("fail", {}, [] { throw 1; }, options);
    Module consumer("consumer");
    auto fail = consumer.require_method<void>("fail", "fail");
    consumer.add_method<void>("run", {}, [fail](const Context& context) { fail(context); });
    Engine engine;
    engine.add(std::move(provider)); engine.add(std::move(consumer));
    engine.connect({"consumer", "fail"}, {"provider", "fail"}); engine.seal();
    const auto direct = failure(ErrorCode::execution_failed, [&] { engine.call({"provider", "fail"}, {}); });
    const auto nested = failure(ErrorCode::execution_failed, [&] { engine.call({"consumer", "run"}, {}); });
    CHECK(nested.target == Reference{"consumer", "run"});
    CHECK(nested.cause && nested.cause->target == direct.target);
    CHECK(nested.cause->text.key() == direct.text.key());
    TextCatalog catalog;
    catalog.load({engine_text_domain, std::string(ASCEND_I18N_DIR) + "/zh-CN.json"});
    const auto localized = render_diagnostic(nested, &catalog, "zh-CN");
    CHECK(localized == "consumer/run: 方法执行失败\n原因：provider/fail: 方法抛出了非标准异常");
    CHECK(render_diagnostic(nested) == std::string(EngineError(nested).what()));
    CHECK(nested.cause->code == direct.code);
    CHECK(nested.cause->text.key() == direct.text.key());
}

struct ThrowsOnCopy {
    ThrowsOnCopy() = default;
    ThrowsOnCopy(const ThrowsOnCopy&) {
        throw EngineError({ErrorCode::invalid_config, {"copy", "data"}, message("copy", "copy failed")});
    }
};

void boundary_diagnostics() {
    Diagnostic original{ErrorCode::invalid_config, {"inner", "value"},
                        message("invalid", "bad {value}", {{"value", "{value}"}}), "module.json", "/value"};
    Engine engine;
    const auto validation = failure(ErrorCode::validation_failed, [&] {
        engine.add(Module("m", [original] { throw EngineError(original); }));
    });
    CHECK(validation.cause && validation.cause->source == original.source);
    CHECK(validation.cause->path == original.path);
    Module module("m");
    module.add_value<int>("value", [original]() -> int { throw EngineError(original); });
    module.add_method<void, ThrowsOnCopy>("copy", {"value"}, [](const ThrowsOnCopy&) {});
    engine.add(std::move(module)); engine.seal();
    const auto read = failure(ErrorCode::execution_failed, [&] { engine.read({"m", "value"}); });
    CHECK(read.cause && read.cause->text.key() == original.text.key());
    const auto copy = engine.bind_method<void, ThrowsOnCopy>({"m", "copy"});
    ThrowsOnCopy value;
    const auto transported = failure(ErrorCode::execution_failed, [&] { copy(value); });
    CHECK(transported.cause && transported.cause->target == Reference{"copy", "data"});
    ModuleFactoryDirectory factories;
    factories.add_definition("broken", [original](const std::string&, const Config&) -> Module { throw EngineError(original); });
    auto outer = failure(ErrorCode::invalid_config, [&] { factories.create("broken", "instance", {}); });
    CHECK(outer.cause && outer.cause->text.key() == original.text.key());
    CHECK(render_text(outer.cause->text) == "bad {value}");
}

void domains() {
    TextCatalog catalog;
    catalog.define("xx", {"a", "value"}, "A");
    catalog.define("xx", {"b", "value"}, "B");
    CHECK(catalog.resolve("xx", TextRef(TextKey{"a", "value"})) == "A");
    CHECK(catalog.resolve("xx", TextRef(TextKey{"b", "value"})) == "B");
    CHECK(catalog.resolve("xx", TextRef({"c", "value"}, "C")) == "C");
    TempDirectory temp;
    const auto file = temp.path / "a.json";
    write_file(file, pack("b", "xx", R"({"value":"BAD"})"));
    failure(ErrorCode::invalid_i18n, [&] { catalog.load({"a", file.string()}, TextConflict::replace); });
    CHECK(catalog.resolve("xx", TextRef(TextKey{"b", "value"})) == "B");
}

void module_resources() {
    TempDirectory temp;
    const auto file = temp.path / "module.json";
    write_file(file, pack("module", "xx", R"({"value":"VALUE"})"));
    ModuleFactoryDirectory factories;
    int constructions = 0;
    factories.add_definition("module", [&](const std::string& name, const Config&) {
        ++constructions;
        Module module(name);
        module.add_value<int>("value", [] { return 1; }, TextRef({"module", "value"}, "Value"));
        return module;
    }, {{"module", file.string()}});
    CHECK(constructions == 0);
    CHECK(factories.i18n_resources("module").size() == 1);
    TextCatalog catalog;
    for (const auto& resource : factories.i18n_resources("module")) catalog.load(resource);
    CHECK(constructions == 0);
    Engine engine;
    engine.add(factories.create("module", "one", {}));
    engine.add(factories.create("module", "two", {}));
    engine.seal();
    CHECK(constructions == 2);
    for (const auto& declaration : engine.catalog()) {
        CHECK(declaration.description.key() == TextKey{"module", "value"});
        CHECK(catalog.resolve("xx", declaration.description) == "VALUE");
    }
    failure(ErrorCode::invalid_declaration, [&] {
        factories.add_definition("invalid", [](const std::string& name, const Config&) { return Module(name); }, {{"", file.string()}});
    });
    CHECK(!factories.contains("invalid"));
    failure(ErrorCode::unknown_definition, [&] { factories.i18n_resources("missing"); });
}

void shipped_pack() {
    TextCatalog catalog;
    catalog.load({engine_text_domain, std::string(ASCEND_I18N_DIR) + "/zh-CN.json"});
    CHECK(catalog.locales() == std::vector<std::string>{"zh-CN"});
    for (const char* key : {"engine.execution.argument_count", "assembly.scope.missing", "json.number.range", "i18n.key.empty"})
        CHECK(catalog.contains("zh-CN", {engine_text_domain, key}));
    // 提供方不匹配的原因也是结构化消息，中文显示不残留默认英文。
    Module p("p"), c("c");
    p.add_value<int>("value", [] { return 1; }, {}, "scalar");
    c.require_value<double>("value", "scalar");
    Engine engine;
    engine.add(std::move(p)); engine.add(std::move(c));
    engine.connect({"c", "value"}, {"p", "value"});
    const auto diagnostics = engine.check();
    CHECK(diagnostics.size() == 1);
    CHECK(catalog.resolve("zh-CN", diagnostics.front().text) == "提供方 p/value：结果类型不匹配");

    Module group("group"), child("child");
    group.require_value<int>("value", "scalar");
    child.require_value<double>("value", "scalar");
    group.add(std::move(child));
    group.forward("value", {"child", "value"});
    Engine forwarded;
    Module source("source");
    source.add_value<int>("value", [] { return 1; }, {}, "scalar");
    forwarded.add(std::move(group)); forwarded.add(std::move(source));
    forwarded.connect({"group", "value"}, {"source", "value"});
    const auto errors = forwarded.check();
    CHECK(errors.size() == 1);
    CHECK(catalog.resolve("zh-CN", errors.front().text) == "转接自 group/value：结果类型不匹配");
}

void depth_messages() {
    TextCatalog catalog;
    catalog.load({engine_text_domain, std::string(ASCEND_I18N_DIR) + "/zh-CN.json"});
    Config config = Config::integer(0);
    for (int i = 0; i < 126; ++i) config = Config::array({std::move(config)});
    AssemblyDefinition definition;
    definition.add_instance("example", "e", std::move(config));
    auto d = failure(ErrorCode::invalid_json, [&] { definition.to_json(); });
    CHECK(catalog.resolve("zh-CN", d.text).find('{') == std::string::npos);
    const auto text = std::string(129, '[') + "0" + std::string(129, ']');
    d = failure(ErrorCode::invalid_json, [&] { AssemblyDefinition::parse(text); });
    CHECK(catalog.resolve("zh-CN", d.text).find('{') == std::string::npos);
}
}  // namespace

int main(int argc, char** argv) {
    const std::map<std::string, std::function<void()>> tests = {
        {"define_resolve", define_resolve}, {"locale_fallback", locale_fallback},
        {"placeholders", placeholders}, {"load_merge", load_merge}, {"load_errors", load_errors},
        {"diagnostic_texts", diagnostic_texts}, {"shipped_pack", shipped_pack}, {"depth_messages", depth_messages},
        {"parameter_literals", parameter_literals}, {"fallback_once", fallback_once},
        {"nested_messages", nested_messages}, {"nested_diagnostics", nested_diagnostics},
        {"boundary_diagnostics", boundary_diagnostics}, {"domains", domains}, {"module_resources", module_resources}
    };
    if (argc != 2 || !tests.count(argv[1])) return 2;
    try { tests.at(argv[1])(); std::cout << argv[1] << ": passed\n"; return 0; }
    catch (const std::exception& error) { std::cerr << argv[1] << ": " << error.what() << '\n'; return 1; }
}
