#pragma once

// 面板上下文：面板只读工作区状态与界面文案；命令、对话框与文件流程都通过
// 回调交给外壳实现，面板之间不互相引用。设计规则见设计文档 WB-19。

#include "ui_text.hpp"
#include "workspace/workspace_model.hpp"

#include <ascend/config.hpp>
#include <ascend/session/adapter.hpp>

#include <QString>
#include <QStringList>

#include <cstdint>
#include <functional>

namespace ascend::workbench {

struct PanelContext {
    const UiTexts* texts = nullptr;
    WorkspaceModel* workspace = nullptr;
    const session::AdapterRegistry* adapters = nullptr;
    const session::ValueAdapter* int_adapter = nullptr;

    // 身份与文件流程（对话框在外壳）。
    std::function<void()> new_research;
    std::function<void()> open_research;
    std::function<void()> open_example;
    std::function<void()> new_system;
    std::function<void()> open_system;
    std::function<void()> save_system;
    std::function<void()> save_system_as;

    // 系统编辑器命令：参数由面板从控件读出，外壳负责忙碌簿记与队列提交。
    std::function<void(const QString& definition, const QString& instance)> add_module;
    std::function<void(const QString& instance)> remove_module;
    std::function<void(const QString& module, const QString& symbol, const QString& provider_module,
                       const QString& provider_symbol)>
        connect_requirement;
    std::function<void(const QString& module, const QString& symbol)> disconnect_requirement;
    std::function<void(const QString& module, const QString& symbol)> set_spec_advance;
    std::function<void(const QStringList& references)> set_spec_observations;

    // 实例配置编辑：参数由 ConfigPanel 从编辑器读出，外壳负责忙碌簿记与队列提交。
    std::function<void(const QString& scope, const QString& name, const ascend::Config& config)> set_instance_config;

    // 模块包流程（文件对话框在外壳）；卸载按定义标识直接执行（按钮恢复原“卸载选中行”行为）。
    std::function<void()> load_packages;
    std::function<void()> load_package_folder;
    std::function<void(const QString& definition)> unload_package;

    // 会话命令：外壳负责忙碌簿记与队列提交。
    std::function<void(const char*)> dispatch;
    std::function<void(const char*)> dispatch_readonly;

    // 跨面板展示请求：选中项详情与采样详情由外壳转交右侧状态面板。
    std::function<void(const QString& html)> show_details;
    std::function<void(int series, std::int64_t frame)> request_sample_detail;
};

}  // namespace ascend::workbench
