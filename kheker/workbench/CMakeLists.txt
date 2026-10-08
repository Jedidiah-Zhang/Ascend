cmake_minimum_required(VERSION 3.20)
project(AscendWorkbench LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

# 以子项目方式复用无界面引擎与示例组件；工作台只需要库目标。
set(ASCEND_ENGINE_BUILD_EXAMPLES OFF)
set(ASCEND_ENGINE_BUILD_TESTS OFF)
add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../engine" "${CMAKE_CURRENT_BINARY_DIR}/engine")

option(ASCEND_WORKBENCH_BUILD_GUI "Build the Qt workbench application" ON)

# 语言资源根目录；资源复制函数供本目录与测试子目录共用（子目录继承本作用域变量）。
set(ASCEND_WORKBENCH_RESOURCE_ROOT "${CMAKE_CURRENT_SOURCE_DIR}")

# 无界面会话编排：不依赖 Qt，可由 CTest 独立验证。
add_library(ascend_session STATIC src/adapter.cpp src/session.cpp src/project.cpp)
add_library(ascend::session ALIAS ascend_session)
target_compile_features(ascend_session PUBLIC cxx_std_17)
target_link_libraries(ascend_session PUBLIC ascend::engine)
target_include_directories(ascend_session PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/include")

if(MSVC)
    target_compile_options(ascend_session PRIVATE /W4 /permissive-)
else()
    target_compile_options(ascend_session PRIVATE -Wall -Wextra -Wpedantic)
endif()

if(ASCEND_WORKBENCH_BUILD_GUI)
    # 首版界面采用 Qt 6 Widgets，不引入 Qt Charts 等附加模块。
    find_package(Qt6 6.4 COMPONENTS Widgets Test REQUIRED)
    set(CMAKE_AUTOMOC ON)

    # 工作区状态层：只依赖 QtCore 与会话库，界面与无控件单测共用（WB-19）。
    add_library(ascend_workbench_workspace STATIC
        app/model_snapshot.hpp
        app/workspace/workspace_view.hpp app/workspace/workspace_model.cpp app/workspace/workspace_model.hpp)
    add_library(ascend::workbench_workspace ALIAS ascend_workbench_workspace)
    target_compile_features(ascend_workbench_workspace PUBLIC cxx_std_17)
    target_link_libraries(ascend_workbench_workspace PUBLIC ascend::session Qt6::Core)
    target_include_directories(ascend_workbench_workspace PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/app")
    if(MSVC)
        target_compile_options(ascend_workbench_workspace PRIVATE /W4 /permissive-)
        target_compile_options(ascend_workbench_workspace PUBLIC /utf-8)
    else()
        target_compile_options(ascend_workbench_workspace PRIVATE -Wall -Wextra -Wpedantic)
    endif()

    add_library(ascend_workbench_ui STATIC
        app/branch_dialog.cpp app/branch_dialog.hpp
        app/config_editor.cpp app/config_editor.hpp
        app/waveform_widget.cpp app/waveform_widget.hpp
        app/main_window.cpp app/main_window.hpp
        app/records_dialog.cpp app/records_dialog.hpp
        app/panels/config_panel.cpp app/panels/config_panel.hpp
        app/panels/diagnostics_panel.cpp app/panels/diagnostics_panel.hpp
        app/panels/diff_panel.cpp app/panels/diff_panel.hpp
        app/panels/modules_panel.cpp app/panels/modules_panel.hpp
        app/panels/package_library_panel.cpp app/panels/package_library_panel.hpp
        app/panels/panel_context.hpp
        app/panels/start_page_panel.cpp app/panels/start_page_panel.hpp
        app/panels/state_panel.cpp app/panels/state_panel.hpp
        app/panels/system_editor_panel.cpp app/panels/system_editor_panel.hpp
        app/panels/table_models.cpp app/panels/table_models.hpp
        app/panels/timeline_panel.cpp app/panels/timeline_panel.hpp
        app/session_controller.cpp app/session_controller.hpp
        app/workspace/panel_registry.cpp app/workspace/panel_registry.hpp
        app/ui_text.cpp app/ui_text.hpp
        app/ui_format.cpp app/ui_format.hpp
        app/workbench.cpp app/workbench.hpp)
    target_compile_features(ascend_workbench_ui PUBLIC cxx_std_17)
    target_link_libraries(ascend_workbench_ui PUBLIC ascend::session ascend::experiment_model ascend::workbench_workspace Qt6::Widgets)
    target_include_directories(ascend_workbench_ui PUBLIC "${CMAKE_CURRENT_SOURCE_DIR}/app")
    target_compile_definitions(ascend_workbench_ui PUBLIC
        ASCEND_WORKBENCH_ENGINE_I18N="${CMAKE_CURRENT_SOURCE_DIR}/../engine/i18n/zh-CN.json"
        ASCEND_WORKBENCH_SESSION_I18N="${CMAKE_CURRENT_SOURCE_DIR}/i18n/zh-CN.json"
        ASCEND_WORKBENCH_UI_I18N="${CMAKE_CURRENT_SOURCE_DIR}/app/i18n/zh-CN.json"
        ASCEND_WORKBENCH_EXAMPLE_ROOT="${CMAKE_CURRENT_SOURCE_DIR}/../engine/examples/experiment")
    if(MSVC)
        target_compile_options(ascend_workbench_ui PRIVATE /W4 /permissive-)
        target_compile_options(ascend_workbench_ui PUBLIC /utf-8)
    else()
        target_compile_options(ascend_workbench_ui PRIVATE -Wall -Wextra -Wpedantic)
    endif()

    add_executable(ascend_workbench app/main.cpp)
    target_link_libraries(ascend_workbench PRIVATE ascend_workbench_ui)

    # 语言资源不嵌入二进制：构建时刷新到目标运行目录的 resources/（保持源码树相对布局），
    # 运行时按可执行文件同目录优先解析；复制运行目录即可在另一台机器保持文案。
    # 自定义目标每次构建都执行，因此修改源码语言资源后一次构建即可刷新运行目录副本。
    function(ascend_workbench_resources_target name target)
        add_custom_target(${name} ALL
            COMMAND "${CMAKE_COMMAND}" -E copy_directory
                    "${ASCEND_WORKBENCH_RESOURCE_ROOT}/../engine/i18n"
                    "$<TARGET_FILE_DIR:${target}>/resources/engine/i18n"
            COMMAND "${CMAKE_COMMAND}" -E copy_directory
                    "${ASCEND_WORKBENCH_RESOURCE_ROOT}/../engine/examples/experiment/i18n"
                    "$<TARGET_FILE_DIR:${target}>/resources/engine/examples/experiment/i18n"
            COMMAND "${CMAKE_COMMAND}" -E copy_directory
                    "${ASCEND_WORKBENCH_RESOURCE_ROOT}/i18n"
                    "$<TARGET_FILE_DIR:${target}>/resources/workbench/i18n"
            COMMAND "${CMAKE_COMMAND}" -E copy_directory
                    "${ASCEND_WORKBENCH_RESOURCE_ROOT}/app/i18n"
                    "$<TARGET_FILE_DIR:${target}>/resources/workbench/app/i18n"
            COMMENT "Refreshing language resources next to ${target}")
    endfunction()

    ascend_workbench_resources_target(ascend_workbench_resources ascend_workbench)
    add_dependencies(ascend_workbench ascend_workbench_resources)
endif()

include(CTest)
if(BUILD_TESTING)
    add_subdirectory("${CMAKE_CURRENT_SOURCE_DIR}/../../testbench/workbench"
                     "${CMAKE_CURRENT_BINARY_DIR}/tests")
endif()
