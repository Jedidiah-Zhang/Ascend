cmake_minimum_required(VERSION 3.20)
project(AscendEngine LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(ascend_engine STATIC src/engine.cpp src/config.cpp src/json.cpp src/assembly.cpp
                                   src/experiment.cpp src/text.cpp src/i18n.cpp)
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

add_executable(ascend_assembled_model examples/assembled_model.cpp)
target_link_libraries(ascend_assembled_model PRIVATE ascend::engine)

add_executable(ascend_assembly_record examples/assembly_record.cpp)
target_link_libraries(ascend_assembly_record PRIVATE ascend::engine)

add_executable(ascend_experiment_branch examples/experiment_branch.cpp)
target_link_libraries(ascend_experiment_branch PRIVATE ascend::engine)

add_executable(ascend_text_catalog examples/text_catalog.cpp)
target_link_libraries(ascend_text_catalog PRIVATE ascend::engine)

include(CTest)
if(BUILD_TESTING)
    add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../../testbench/engine"
                     "${CMAKE_CURRENT_BINARY_DIR}/tests")
    add_test(NAME engine.minimal_model COMMAND ascend_minimal_model)
    set_tests_properties(engine.minimal_model PROPERTIES
        PASS_REGULAR_EXPRESSION "baseline=8 alternate=12")
    add_test(NAME assembly.testbench COMMAND ascend_assembled_model)
    set_tests_properties(assembly.testbench PROPERTIES
        PASS_REGULAR_EXPRESSION "pulse=3 baseline=8 alternate=12")
    add_test(NAME record.testbench COMMAND ascend_assembly_record)
    set_tests_properties(record.testbench PROPERTIES
        PASS_REGULAR_EXPRESSION "pulse=3 baseline=8 alternate=12")
    add_test(NAME experiment.branch COMMAND ascend_experiment_branch)
    set_tests_properties(experiment.branch PROPERTIES
        PASS_REGULAR_EXPRESSION "reference=ok")
    add_test(NAME engine.text_catalog COMMAND ascend_text_catalog
             "${CMAKE_CURRENT_SOURCE_DIR}/i18n/zh-CN.json"
             "${CMAKE_CURRENT_SOURCE_DIR}/examples/accumulator/i18n/zh-CN.json")
    set_tests_properties(engine.text_catalog PROPERTIES
        PASS_REGULAR_EXPRESSION "catalog=ok")
endif()
