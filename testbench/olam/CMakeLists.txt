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
endif()
