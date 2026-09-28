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
