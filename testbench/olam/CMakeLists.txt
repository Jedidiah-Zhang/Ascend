add_executable(ascend_space_tests test_space.cpp)
target_link_libraries(ascend_space_tests PRIVATE ascend::olam)
foreach(case IN ITEMS
    create
    overlap
    locate
    convert
    bounds
    readonly
)
    add_test(NAME space.${case} COMMAND ascend_space_tests ${case})
endforeach()

add_executable(ascend_time_tests test_time.cpp)
target_link_libraries(ascend_time_tests PRIVATE ascend::olam)
foreach(case IN ITEMS
    create
    advance
    rates
    state
    overflow
)
    add_test(NAME time.${case} COMMAND ascend_time_tests ${case})
endforeach()

if(TARGET ascend_olam_module)
    add_executable(ascend_space_module_tests test_space_module.cpp)
    target_link_libraries(ascend_space_module_tests PRIVATE ascend::olam_module)
    target_compile_definitions(ascend_space_module_tests PRIVATE
        OLAM_SPACE_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/zh-CN.json")
    foreach(case IN ITEMS
        values
        queries
        state
        config_errors
        package
    )
        add_test(NAME space_module.${case} COMMAND ascend_space_module_tests ${case})
    endforeach()

    add_executable(ascend_time_module_tests test_time_module.cpp)
    target_link_libraries(ascend_time_module_tests PRIVATE ascend::olam_module)
    target_compile_definitions(ascend_time_module_tests PRIVATE
        OLAM_TIME_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/time.zh-CN.json")
    foreach(case IN ITEMS
        values
        state
        config_errors
        package
    )
        add_test(NAME time_module.${case} COMMAND ascend_time_module_tests ${case})
    endforeach()

    # 包工具的实际输出与进程内编码走同一载入路径。
    set(space_package_dir "${CMAKE_CURRENT_BINARY_DIR}/space-package")
    add_test(NAME space_module.package_tool
             COMMAND ascend_olam_space_package
                     "${space_package_dir}"
                     "${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/zh-CN.json")
    add_test(NAME space_module.package_file
             COMMAND ascend_space_module_tests package_file
                     "${space_package_dir}/olam.space.amod")
    set_tests_properties(space_module.package_file PROPERTIES
                         DEPENDS space_module.package_tool)

    set(time_package_dir "${CMAKE_CURRENT_BINARY_DIR}/time-package")
    add_test(NAME time_module.package_tool
             COMMAND ascend_olam_time_package
                     "${time_package_dir}"
                     "${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/time.zh-CN.json")
    add_test(NAME time_module.package_file
             COMMAND ascend_time_module_tests package_file
                     "${time_package_dir}/olam.clock.amod")
    set_tests_properties(time_module.package_file PROPERTIES
                         DEPENDS time_module.package_tool)

    if(TARGET ascend_olam_space_plugin)
        if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
            set(plugin_arch "arm64")
        else()
            set(plugin_arch "x86_64")
        endif()
        if(WIN32)
            set(plugin_platform "windows-${plugin_arch}")
        elseif(APPLE)
            set(plugin_platform "macos-${plugin_arch}")
        else()
            set(plugin_platform "linux-${plugin_arch}")
        endif()
        add_executable(ascend_plugin_tests test_plugin.cpp)
        target_link_libraries(ascend_plugin_tests PRIVATE ascend::olam_module)
        target_compile_definitions(ascend_plugin_tests PRIVATE
            PLUGIN_PLATFORM="${plugin_platform}")
        add_dependencies(ascend_plugin_tests ascend_olam_space_plugin)
        add_test(NAME plugin.load
                 COMMAND ascend_plugin_tests "$<TARGET_FILE:ascend_olam_space_plugin>")

        set(plugin_package_dir "${CMAKE_CURRENT_BINARY_DIR}/plugin-package")
        add_test(NAME plugin.package_tool
                 COMMAND ascend_olam_space_package
                         "${plugin_package_dir}"
                         "${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/zh-CN.json"
                         "$<TARGET_FILE:ascend_olam_space_plugin>")
        add_test(NAME plugin.package_file
                 COMMAND ascend_plugin_tests package_file
                         "${plugin_package_dir}/olam.space.amod")
        set_tests_properties(plugin.package_file PROPERTIES
                             DEPENDS plugin.package_tool)

        add_library(ascend_test_types_plugin MODULE test_types_plugin.cpp)
        target_link_libraries(ascend_test_types_plugin PRIVATE
                              ascend::olam_plugin_support ascend::engine)
        set_target_properties(ascend_test_types_plugin PROPERTIES
                              PREFIX "" OUTPUT_NAME "test.types.plugin")

        add_executable(ascend_plugin_types_tests test_plugin_types.cpp)
        target_link_libraries(ascend_plugin_types_tests PRIVATE ascend::engine)
        target_compile_definitions(ascend_plugin_types_tests PRIVATE
            PLUGIN_PLATFORM="${plugin_platform}")
        add_dependencies(ascend_plugin_types_tests ascend_test_types_plugin)
        add_test(NAME plugin.types
                 COMMAND ascend_plugin_types_tests "$<TARGET_FILE:ascend_test_types_plugin>")
    endif()
endif()
