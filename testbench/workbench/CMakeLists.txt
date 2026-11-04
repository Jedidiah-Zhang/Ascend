add_executable(ascend_session_tests test_session.cpp)
target_link_libraries(ascend_session_tests PRIVATE ascend::session ascend::experiment_model ascend::olam_module)
if(CMAKE_SYSTEM_PROCESSOR MATCHES "aarch64|arm64|ARM64")
    set(workbench_plugin_arch "arm64")
else()
    set(workbench_plugin_arch "x86_64")
endif()
if(WIN32)
    set(workbench_plugin_platform "windows-${workbench_plugin_arch}")
elseif(APPLE)
    set(workbench_plugin_platform "macos-${workbench_plugin_arch}")
else()
    set(workbench_plugin_platform "linux-${workbench_plugin_arch}")
endif()
target_compile_definitions(ascend_session_tests PRIVATE
    WORKBENCH_EXAMPLE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/engine/examples/experiment"
    WORKBENCH_ENGINE_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/engine/i18n/zh-CN.json"
    WORKBENCH_SESSION_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../kheker/workbench/i18n/zh-CN.json"
    WORKBENCH_OLAM_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/zh-CN.json"
    WORKBENCH_OLAM_TIME_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../../olam/module/i18n/time.zh-CN.json"
    WORKBENCH_SPACE_PLUGIN="$<TARGET_FILE:ascend_olam_space_plugin>"
    WORKBENCH_SPACE_PACKAGE="$<TARGET_FILE_DIR:ascend_workbench>/modules/olam.space.amod"
    WORKBENCH_PLUGIN_PLATFORM="${workbench_plugin_platform}")
if(TARGET ascend_olam_space_plugin)
    add_dependencies(ascend_session_tests ascend_olam_space_plugin)
endif()
if(TARGET ascend_workbench_space_package)
    add_dependencies(ascend_session_tests ascend_workbench_space_package)
endif()
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
    space_module
    time_module
    dynamic_module_package
    system_assembly
    system_file
    project_workflow
    project_module_packages
    project_record_history
    project_record_lineage
    project_errors
)
    add_test(NAME session.${case} COMMAND ascend_session_tests ${case})
endforeach()

if(TARGET ascend_workbench_ui)
    # 工作区状态层单测：无控件（QCoreApplication），链接仅 QtCore 的状态层目标。
    add_executable(ascend_workspace_tests test_workspace_model.cpp)
    target_link_libraries(ascend_workspace_tests PRIVATE ascend::workbench_workspace Qt6::Test)
    foreach(case IN ITEMS
        identity_notice
        busy_controls
        results_availability
        results_panels
        samples_append
        observations_changed
        comparison_stored
        check_store
        diagnostics_log
        record_store
        controls_matrix
        packages_controls
    )
        add_test(NAME workspace.${case} COMMAND ascend_workspace_tests ${case})
    endforeach()

    add_executable(ascend_workbench_tests test_workbench_ui.cpp)
    target_link_libraries(ascend_workbench_tests PRIVATE ascend_workbench_ui Qt6::Test)
    target_compile_definitions(ascend_workbench_tests PRIVATE
        WORKBENCH_SPACE_PACKAGE="$<TARGET_FILE_DIR:ascend_workbench>/modules/olam.space.amod")
    # 界面测试同样使用随构建复制的运行目录资源，验证运行时解析路径。
    ascend_workbench_resources_target(ascend_workbench_tests_resources ascend_workbench_tests)
    add_dependencies(ascend_workbench_tests ascend_workbench_tests_resources)
    if(TARGET ascend_workbench_space_package)
        add_dependencies(ascend_workbench_tests ascend_workbench_space_package)
    endif()
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
        projectRecordRoundTrip
        openProjectFailure
        openProjectPackages
        modulePackageLoadUnload
        spaceModulePackage
        dynamicPackageCarrier
        modulePackageMultiLoad
        modulePackageFolderLoad
        systemEditorAssembly
        startPageResearch
        recordTabKeepsSampleDetail
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
        workbench.projectRecordRoundTrip
        workbench.openProjectFailure
        workbench.openProjectPackages
        workbench.modulePackageLoadUnload
        workbench.spaceModulePackage
        workbench.dynamicPackageCarrier
        workbench.modulePackageMultiLoad
        workbench.modulePackageFolderLoad
        workbench.systemEditorAssembly
        workbench.startPageResearch
        workbench.recordTabKeepsSampleDetail
        workbench.dockTitleDoubleClick
        workbench.dockFloatDockStress
        PROPERTIES ENVIRONMENT
        "QT_QPA_PLATFORM=offscreen;ASCEND_WORKBENCH_SCREENSHOT=${CMAKE_CURRENT_BINARY_DIR}/workbench-ui.png")
endif()
