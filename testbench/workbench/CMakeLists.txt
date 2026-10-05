add_executable(ascend_session_tests test_session.cpp)
target_link_libraries(ascend_session_tests PRIVATE ascend::session ascend::experiment_model)
target_compile_definitions(ascend_session_tests PRIVATE
    WORKBENCH_EXAMPLE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/engine/examples/experiment"
    WORKBENCH_ENGINE_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/engine/i18n/zh-CN.json"
    WORKBENCH_SESSION_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/workbench/i18n/zh-CN.json")
foreach(case IN ITEMS
    adapter_int64
    load_and_check
    config_revision
    step_run_stop
    checkpoint_branches
    branch_order
    intervention_errors
    partial_branch_failure
    failure_paths
    record_inputs
    replay
    state_rules
    limits
    dual_input_replay
    comparison_limits
    localized_texts
    autonomous_replay
    partial_input_records
    exception_boundaries
    save_open_round_trip
    open_record_state
    open_rejects_and_falls_back
    open_exploration_only
    module_packages
    system_assembly
    system_file
)
    add_test(NAME session.${case} COMMAND ascend_session_tests ${case})
endforeach()

if(TARGET ascend_workbench_ui)
    add_executable(ascend_workbench_tests test_workbench_ui.cpp)
    target_link_libraries(ascend_workbench_tests PRIVATE ascend_workbench_ui Qt6::Test)
    # 界面测试同样使用随构建复制的运行目录资源，验证运行时解析路径。
    ascend_workbench_resources_target(ascend_workbench_tests_resources ascend_workbench_tests)
    add_dependencies(ascend_workbench_tests ascend_workbench_tests_resources)
    foreach(case IN ITEMS
        windowLoadsExample
        stepAndVariableSwitch
        stopDuringRun
        runNThroughput
        branchComparisonAndReplay
        failedStateControls
        closeWhileRunning
        abandonedWorkbench
        uiTextFallback
        resourceResolution
        menuBarStructure
        captureScreenshot
        fileSaveOpen
        fileOpenFailure
        modulePackageLoadUnload
        modulePackageMultiLoad
        modulePackageFolderLoad
        systemEditorAssembly
        startPageResearch
        dockTitleDoubleClick
        dockFloatDockStress
    )
        add_test(NAME workbench.${case} COMMAND ascend_workbench_tests ${case})
    endforeach()
    set_tests_properties(
        workbench.windowLoadsExample
        workbench.stepAndVariableSwitch
        workbench.stopDuringRun
        workbench.runNThroughput
        workbench.branchComparisonAndReplay
        workbench.failedStateControls
        workbench.closeWhileRunning
        workbench.abandonedWorkbench
        workbench.uiTextFallback
        workbench.resourceResolution
        workbench.menuBarStructure
        workbench.captureScreenshot
        workbench.fileSaveOpen
        workbench.fileOpenFailure
        workbench.modulePackageLoadUnload
        workbench.modulePackageMultiLoad
        workbench.modulePackageFolderLoad
        workbench.systemEditorAssembly
        workbench.startPageResearch
        workbench.dockTitleDoubleClick
        workbench.dockFloatDockStress
        PROPERTIES ENVIRONMENT
        "QT_QPA_PLATFORM=offscreen;ASCEND_WORKBENCH_SCREENSHOT=${CMAKE_CURRENT_BINARY_DIR}/workbench-ui.png")
endif()
