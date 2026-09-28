cmake_minimum_required(VERSION 3.20)
project(AscendOlam LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

add_library(ascend_olam STATIC src/space.cpp)
add_library(ascend::olam ALIAS ascend_olam)
target_compile_features(ascend_olam PUBLIC cxx_std_17)
target_include_directories(ascend_olam PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

if(MSVC)
    target_compile_options(ascend_olam PRIVATE /W4 /permissive-)
    target_compile_options(ascend_olam PUBLIC /utf-8)
else()
    target_compile_options(ascend_olam PRIVATE -Wall -Wextra -Wpedantic)
endif()

include(CTest)
if(BUILD_TESTING)
    add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../testbench/olam"
                     "${CMAKE_CURRENT_BINARY_DIR}/tests")
endif()
