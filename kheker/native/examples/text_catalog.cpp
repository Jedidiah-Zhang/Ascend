// 模块自带文本域与语言资源；宿主在展示边界选择语言。
#include <ascend/assembly.hpp>
#include <ascend/i18n.hpp>

#include <iostream>

int main(int argc, char** argv) {
    if (argc != 3) {
        std::cerr << "usage: ascend_text_catalog <engine-catalog.json> <module-catalog.json>\n";
        return 2;
    }
    try {
        ascend::ModuleFactoryDirectory factories;
        factories.add_definition("example.accumulator", [](const std::string& name, const ascend::Config&) {
            ascend::Module module(name);
            module.add_value<int>("value", [] { return 0; },
                {{"example.accumulator", "state.value"}, "Accumulated output"});
            return module;
        }, {{"example.accumulator", argv[2]}});
        ascend::TextCatalog catalog;
        catalog.load({ascend::engine_text_domain, argv[1]});
        for (const auto& resource : factories.i18n_resources("example.accumulator")) catalog.load(resource);
        ascend::Engine engine;
        engine.add(factories.create("example.accumulator", "one", {}));
        engine.add(factories.create("example.accumulator", "two", {}));
        engine.seal();
        bool valid = true;
        for (const auto& declaration : engine.catalog()) {
            valid = valid && catalog.resolve("zh-CN", declaration.description) == "累计输出";
            valid = valid && catalog.resolve("en", declaration.description) == "Accumulated output";
        }
        bool diagnosed = false;
        try { engine.call({"one", "value"}, {}); }
        catch (const ascend::EngineError& error) {
            diagnosed = true;
            valid = valid && catalog.resolve("zh-CN", error.diagnostic().text) == "预期一个公开方法";
        }
        valid = valid && diagnosed;
        std::cout << "catalog=" << (valid ? "ok" : "failed") << '\n';
        return valid ? 0 : 1;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
