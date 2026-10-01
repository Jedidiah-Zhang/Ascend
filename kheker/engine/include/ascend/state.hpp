#pragma once

#include <ascend/config.hpp>

#include <string>
#include <vector>

namespace ascend {

// 快照中的一个模块条目：完整路径、状态契约、无状态标记与状态值。
// stateless 为真时 state 为空值；有状态模块的 state 由模块捕获回调给出。
struct ModuleState {
    std::string path;
    std::string contract;
    bool stateless = false;
    Config state;
};

// 完整运行状态快照：按模块路径排序的全部模块状态，按值保存。
// 捕获结果与后续运行不共享可变状态（要求模块捕获回调返回独立副本）。
struct StateSnapshot {
    std::vector<ModuleState> modules;
};

// 模块声明的运行状态能力：无状态标记与状态契约；封闭前后都可查询。
struct StateCapability {
    bool stateless = false;
    std::string contract;
};

}  // namespace ascend
