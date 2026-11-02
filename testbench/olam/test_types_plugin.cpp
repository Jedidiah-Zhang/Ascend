#include "test_types_module.hpp"

#include <olam/plugin/plugin_support.hpp>

extern "C" ASCEND_PLUGIN_EXPORT const AscendPluginCallbacks* ascend_plugin_callbacks(void) {
    return olam::plugin::callbacks(
        {test_types::definition, test_types::implementation, test_types::version,
         test_types::default_config(), test_types::module});
}
