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
endif()
