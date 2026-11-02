#include <olam/module/time_module.hpp>
#include <olam/plugin/plugin_support.hpp>

extern "C" ASCEND_PLUGIN_EXPORT const AscendPluginCallbacks* ascend_plugin_callbacks(void) {
    return olam::plugin::callbacks(
        {olam::module::time_definition, olam::module::time_implementation,
         olam::module::time_version, olam::module::time_preset(),
         olam::module::make_time_module});
}
