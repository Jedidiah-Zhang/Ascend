#include <olam/module/space_module.hpp>
#include <olam/plugin/plugin_support.hpp>

extern "C" ASCEND_PLUGIN_EXPORT const AscendPluginCallbacks* ascend_plugin_callbacks(void) {
    return olam::plugin::callbacks(
        {olam::module::space_definition, olam::module::space_implementation,
         olam::module::space_version, olam::module::space_preset(),
         olam::module::make_space_module});
}
