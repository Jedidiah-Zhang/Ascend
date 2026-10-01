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
    scopes
)
    add_test(NAME engine.${case} COMMAND ascend_engine_tests ${case})
endforeach()

add_executable(ascend_assembly_tests test_assembly.cpp)
target_link_libraries(ascend_assembly_tests PRIVATE ascend::engine)
foreach(case IN ITEMS
    model
    discovery
    stimulus
    missing
    mismatches
    names
    edge_cases
    exports
    forwarding
    order
    cycles
    lifetime
    closed
    context_identity
    failures
)
    add_test(NAME assembly.${case} COMMAND ascend_assembly_tests ${case})
endforeach()

add_executable(ascend_assembly_record_tests test_assembly_record.cpp)
target_link_libraries(ascend_assembly_record_tests PRIVATE ascend::engine)
foreach(case IN ITEMS
    factories
    definition
    definition_access
    roundtrip
    config_change
    wiring
    independence
    config_values
    record_errors
    factory_errors
    factory_target
    factory_causes
    assembly_errors
    builder_errors
    rollback
    file
    diagnostic_locations
    forward_transaction
    json_depth
    utf8_serialization
    pointer_paths
)
    add_test(NAME record.${case} COMMAND ascend_assembly_record_tests ${case})
endforeach()

add_executable(ascend_experiment_tests test_experiment.cpp)
target_link_libraries(ascend_experiment_tests PRIVATE ascend::engine ascend::experiment_model)
target_compile_definitions(ascend_experiment_tests PRIVATE
    ASCEND_EXAMPLE_MODEL_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/engine/examples/experiment")
foreach(case IN ITEMS
    state_declarations
    capture_restore
    deep_copy
    branch_isolation
    sampling_stability
    lifetime
    intervention_propagation
    intervention_errors
    nested_fields
    restore_mismatches
    spec_errors
    step_failure
    trace_record
    nested_assembly
    reentrant_operations
    frame_limit
    observation_memory
    trace_replay
    run_directory
    example_model
    experiment_file_round_trip
    experiment_file_depth_limits
    module_package_round_trip
    module_library_load_unload
)
    add_test(NAME experiment.${case} COMMAND ascend_experiment_tests ${case})
endforeach()

add_executable(ascend_text_tests test_text.cpp)
target_link_libraries(ascend_text_tests PRIVATE ascend::engine)
target_compile_definitions(ascend_text_tests PRIVATE
    ASCEND_I18N_DIR="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/engine/i18n")
foreach(case IN ITEMS
    define_resolve
    locale_fallback
    placeholders
    load_merge
    load_errors
    diagnostic_texts
    shipped_pack
    depth_messages
    parameter_literals
    fallback_once
    nested_messages
    nested_diagnostics
    boundary_diagnostics
    domains
    module_resources
)
    add_test(NAME text.${case} COMMAND ascend_text_tests ${case})
endforeach()
