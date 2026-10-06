#pragma once

// 控制器向界面发布的值模型：一次命令后的目录、草稿实例、输入、状态字段与
// 序列元数据。仅使用标准库与会话视图类型，界面与状态层共用。

#include <ascend/session/session.hpp>

#include <string>
#include <vector>

namespace ascend::workbench {

struct ModelSnapshot {
    session::CatalogView catalog;
    session::SpecView spec;
    bool catalog_stale = false;
    std::vector<session::InstanceView> instances;
    std::vector<session::StateFieldView> state_fields;
    std::vector<std::string> observations;
    std::vector<std::string> series_labels;
    std::vector<std::string> available_modules;  // 可用模块定义（宿主内置与模块库）
};

}  // namespace ascend::workbench
