#pragma once

#include <ascend/engine.hpp>
#include <ascend/text.hpp>

#include <any>
#include <functional>
#include <map>
#include <memory>
#include <string>
#include <vector>

namespace ascend::detail {

// 已登记公开项的实现：声明、读取回调与调用回调。回调已擦除类型，
// 由公共头的模板在登记时封装；这里只保存可调用的形式。
struct Entry {
    Declaration declaration;
    std::function<std::any(const Context&)> getter;
    std::function<std::any(const Context&, const std::vector<std::any>&)> method;
};

// 一项已解析的连接输入：需求声明与提供方实现。
struct Input {
    std::shared_ptr<const Requirement> requirement;
    std::shared_ptr<const Entry> provider;
};

// 一个模块的状态能力在运行装配中的擦除形式；declared 为假表示模块未声明状态能力。
struct StateEntry {
    bool declared = false;
    std::string contract;
    bool stateless = false;
    std::function<Config()> capture;
    std::function<void(const Config&)> restore;
};

// 封闭后的运行装配：公开项、需求解析结果与按模块路径索引的状态能力。
struct Runtime {
    std::map<Reference, std::shared_ptr<const Entry>> outputs;
    std::map<Reference, Input> inputs;
    std::map<std::string, std::shared_ptr<const StateEntry>> states;
};

// 作者文本作为原文进入诊断；内置消息保留模板与参数，不提前渲染。
[[noreturn]] void fail(ErrorCode code, const Reference& target, std::string message);
[[noreturn]] void fail_text(ErrorCode code, const Reference& target, TextRef text);
Diagnostic wrap_diagnostic(ErrorCode code, const Reference& target, TextRef text, const Diagnostic& cause);
enum class FailureBoundary { getter, method, validation, transport, state_capture, state_restore };
// 仅在 catch 内调用；统一保存 EngineError 原因链、作者原文和非标准异常消息。
[[noreturn]] void rethrow_boundary(FailureBoundary boundary, const Reference& target);

}  // namespace ascend::detail
