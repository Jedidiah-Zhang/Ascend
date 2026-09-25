add_executable(ascend_engine_tests test_engine.cpp)
target_link_libraries(ascend_engine_tests PRIVATE ascend::engine)

foreach(case IN ITEMS
    non_spatial_model
    custom_values
    transport_values
    catalog_is_passive
    scoped_references
    invalid_declarations
    duplicate_registration
    configuration_validation
    validation_reentry
    unresolved_dependencies
    dependency_kind
    lifecycle
    binding_checks
    invocation_checks
    callback_failures
    binding_argument_failures
    binding_value_failures
    binding_result_failures
    binding_lifetime
    stateful_callbacks
)
    add_test(NAME engine.${case} COMMAND ascend_engine_tests ${case})
endforeach()
