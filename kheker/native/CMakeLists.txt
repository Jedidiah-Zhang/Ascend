cmake_minimum_required(VERSION 3.20)
project(AscendEngine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(ascend_engine STATIC src/engine.cpp)
add_library(ascend::engine ALIAS ascend_engine)
target_compile_features(ascend_engine PUBLIC cxx_std_17)
target_include_directories(ascend_engine PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

if(MSVC)
    target_compile_options(ascend_engine PRIVATE /W4 /permissive-)
    target_compile_options(ascend_engine PUBLIC /utf-8)
else()
    target_compile_options(ascend_engine PRIVATE -Wall -Wextra -Wpedantic)
endif()

add_executable(ascend_minimal_model examples/minimal_model.cpp)
target_link_libraries(ascend_minimal_model PRIVATE ascend::engine)

include(CTest)
if(BUILD_TESTING)
    add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../../testbench/native"
                     "${CMAKE_CURRENT_BINARY_DIR}/tests")
    add_test(NAME engine.minimal_model COMMAND ascend_minimal_model)
    set_tests_properties(engine.minimal_model PROPERTIES
        PASS_REGULAR_EXPRESSION "baseline=8 alternate=12")
endif()
