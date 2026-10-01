#pragma once

#include "session_controller.hpp"
#include "ui_text.hpp"

#include <ascend/session/session.hpp>

#include <QObject>

#include <memory>
#include <string>

class QThread;

namespace ascend::workbench {

class MainWindow;

// 工作台运行资源：语言资源不嵌入二进制，构建时复制到可执行文件同目录的
// `resources/`（保持源码树相对布局）；运行时优先使用运行目录副本，缺失时
// 回退编译期源码路径，便于开发构建直接运行。
struct ResourcePaths {
    std::string engine_i18n;
    std::string session_i18n;
    std::string ui_i18n;
    std::string example_root;
};

// 以可执行文件所在目录为基准解析运行资源。
ResourcePaths default_resource_paths();
// 以指定目录为基准解析运行资源；缺失项回退编译期源码路径。
ResourcePaths resource_paths_from(const std::string& base_dir);
// 内置确定性三变量示例的会话模板（装配、规格、实现标识与语言资源）。
session::ModelTemplate example_template(const ResourcePaths& paths);

// 工作台装配：专属执行线程持有会话控制层；界面线程只提交命令并接收值快照。
class Workbench : public QObject {
    Q_OBJECT

public:
    explicit Workbench(session::ModelTemplate model, QObject* parent = nullptr);
    ~Workbench() override;

    MainWindow* window() const { return window_.get(); }
    // 界面文案上下文（默认界面语言资源，开发构建路径）。
    const UiTexts& ui_texts() const { return *texts_; }
    SessionController* controller() const { return controller_; }
    // 启动专属线程并打开内置示例。
    void start();
    // 停止当前操作、结束专属线程并回收控制层；重复调用无副作用。
    void shutdown();

private:
    std::shared_ptr<const UiTexts> texts_;
    std::unique_ptr<MainWindow> window_;
    QThread* thread_ = nullptr;
    SessionController* controller_ = nullptr;
    bool stopped_ = false;
};

}  // namespace ascend::workbench
