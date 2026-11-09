cmake_minimum_required(VERSION 3.20)
project(AscendOlam LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

if(MSVC)
    # 引擎与插件代理包含较多模板实例；MSVC 目标文件需要大对象支持。
    add_compile_options(/bigobj)
endif()

add_library(ascend_olam STATIC src/space.cpp src/time.cpp)
add_library(ascend::olam ALIAS ascend_olam)
target_compile_features(ascend_olam PUBLIC cxx_std_17)
target_include_directories(ascend_olam PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

if(MSVC)
    target_compile_options(ascend_olam PRIVATE /W4 /permissive-)
    target_compile_options(ascend_olam PUBLIC /utf-8)
else()
    target_compile_options(ascend_olam PRIVATE -Wall -Wextra -Wpedantic)
endif()

# 声明接入是独立目标：核心空间与时间库仍不依赖声明引擎，只有该目标链接引擎。
if(EXISTS "${CMAKE_CURRENT_SOURCE_DIR}/../kheker/engine/CMakeLists.txt")
    set(ASCEND_OLAM_MODULE_DEFAULT ON)
else()
    set(ASCEND_OLAM_MODULE_DEFAULT OFF)
endif()
option(ASCEND_OLAM_BUILD_MODULE
       "Build the declaration-engine module adapters and package tools"
       ${ASCEND_OLAM_MODULE_DEFAULT})
option(ASCEND_OLAM_BUILD_TESTS "Register olam tests with CTest" ON)

if(ASCEND_OLAM_BUILD_MODULE)
    # 作为更大构建的子项目消费时，声明引擎通常已由宿主加入；独立构建时按需引入。
    if(NOT TARGET ascend::engine)
        set(ASCEND_ENGINE_BUILD_EXAMPLES OFF)
        set(ASCEND_ENGINE_BUILD_TESTS OFF)
        add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../kheker/engine"
                         "${CMAKE_CURRENT_BINARY_DIR}/engine")
    endif()
    # 插件目标会链接静态引擎和世界库，因此这些目标必须可按位置无关方式链接。
    set_target_properties(ascend_engine PROPERTIES POSITION_INDEPENDENT_CODE ON)
    set_target_properties(ascend_olam PROPERTIES POSITION_INDEPENDENT_CODE ON)

    add_library(ascend_olam_module STATIC
        module/src/space_module.cpp
        module/src/time_module.cpp)
    add_library(ascend::olam_module ALIAS ascend_olam_module)
    set_target_properties(ascend_olam_module PROPERTIES POSITION_INDEPENDENT_CODE ON)
    target_compile_features(ascend_olam_module PUBLIC cxx_std_17)
    target_link_libraries(ascend_olam_module PUBLIC ascend::engine ascend::olam)
    target_include_directories(ascend_olam_module PUBLIC
                               "${CMAKE_CURRENT_SOURCE_DIR}/module/include")

    if(MSVC)
        target_compile_options(ascend_olam_module PRIVATE /W4 /permissive-)
        target_compile_options(ascend_olam_module PUBLIC /utf-8)
    else()
        target_compile_options(ascend_olam_module PRIVATE -Wall -Wextra -Wpedantic)
    endif()

    add_executable(ascend_olam_space_package module/tool/space_package.cpp)
    target_link_libraries(ascend_olam_space_package PRIVATE ascend::olam_module)

    add_executable(ascend_olam_time_package module/tool/time_package.cpp)
    target_link_libraries(ascend_olam_time_package PRIVATE ascend::olam_module)

    # C ABI 插件：C++ 模块实现留在插件内部，DSO 边界只暴露 ascend_plugin_callbacks。
    add_library(ascend_olam_plugin_support STATIC plugin/src/plugin_support.cpp)
    add_library(ascend::olam_plugin_support ALIAS ascend_olam_plugin_support)
    set_target_properties(ascend_olam_plugin_support PROPERTIES POSITION_INDEPENDENT_CODE ON)
    target_compile_features(ascend_olam_plugin_support PUBLIC cxx_std_17)
    target_link_libraries(ascend_olam_plugin_support PUBLIC ascend::engine)
    target_include_directories(ascend_olam_plugin_support PUBLIC
                               "${CMAKE_CURRENT_SOURCE_DIR}/plugin/include")
    if(MSVC)
        target_compile_options(ascend_olam_plugin_support PRIVATE /W4 /permissive-)
        target_compile_options(ascend_olam_plugin_support PUBLIC /utf-8)
    else()
        target_compile_options(ascend_olam_plugin_support PRIVATE -Wall -Wextra -Wpedantic)
    endif()

    add_library(ascend_olam_space_plugin MODULE plugin/src/space_plugin.cpp)
    target_link_libraries(ascend_olam_space_plugin PRIVATE
                          ascend::olam_plugin_support ascend::olam_module)
    set_target_properties(ascend_olam_space_plugin PROPERTIES
                          PREFIX "" OUTPUT_NAME "olam.space.plugin"
                          CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)

    add_library(ascend_olam_time_plugin MODULE plugin/src/time_plugin.cpp)
    target_link_libraries(ascend_olam_time_plugin PRIVATE
                          ascend::olam_plugin_support ascend::olam_module)
    set_target_properties(ascend_olam_time_plugin PROPERTIES
                          PREFIX "" OUTPUT_NAME "olam.clock.plugin"
                          CXX_VISIBILITY_PRESET hidden VISIBILITY_INLINES_HIDDEN ON)
endif()

include(CTest)
if(BUILD_TESTING AND ASCEND_OLAM_BUILD_TESTS)
    add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../testbench/olam"
                     "${CMAKE_CURRENT_BINARY_DIR}/tests")
endif()
