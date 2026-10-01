#include "workbench.hpp"

#include "main_window.hpp"

#include <ascend/example/experiment_model.hpp>

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QMetaObject>
#include <QThread>

#include <utility>

#ifndef ASCEND_WORKBENCH_ENGINE_I18N
#error "ASCEND_WORKBENCH_ENGINE_I18N must be defined by the build"
#endif
#ifndef ASCEND_WORKBENCH_SESSION_I18N
#error "ASCEND_WORKBENCH_SESSION_I18N must be defined by the build"
#endif
#ifndef ASCEND_WORKBENCH_UI_I18N
#error "ASCEND_WORKBENCH_UI_I18N must be defined by the build"
#endif
#ifndef ASCEND_WORKBENCH_EXAMPLE_ROOT
#error "ASCEND_WORKBENCH_EXAMPLE_ROOT must be defined by the build"
#endif

namespace ascend::workbench {
namespace {

// 跨线程信号与阻塞调用使用的类型在创建控制层前注册一次。
void register_metatypes() {
    static const bool once = [] {
        qRegisterMetaType<ModelSnapshot>("ascend::workbench::ModelSnapshot");
        qRegisterMetaType<ascend::Config>("ascend::Config");
        qRegisterMetaType<session::OperationResult>("ascend::session::OperationResult");
        qRegisterMetaType<session::Status>("ascend::session::Status");
        qRegisterMetaType<session::CheckReport>("ascend::session::CheckReport");
        qRegisterMetaType<session::ReplayReport>("ascend::session::ReplayReport");
        qRegisterMetaType<session::ComparisonView>("ascend::session::ComparisonView");
        qRegisterMetaType<session::TrackTraceView>("ascend::session::TrackTraceView");
        qRegisterMetaType<session::SampleDetailView>("ascend::session::SampleDetailView");
        qRegisterMetaType<session::DiagnosticView>("ascend::session::DiagnosticView");
        qRegisterMetaType<session::BranchRequest>("ascend::session::BranchRequest");
        qRegisterMetaType<std::vector<session::TrackTraceView>>("std::vector<ascend::session::TrackTraceView>");
        qRegisterMetaType<std::vector<session::SampleView>>("std::vector<ascend::session::SampleView>");
        qRegisterMetaType<std::vector<session::StepEvent>>("std::vector<ascend::session::StepEvent>");
        qRegisterMetaType<std::vector<session::BranchRequest>>("std::vector<ascend::session::BranchRequest>");
        qRegisterMetaType<std::vector<session::DiagnosticView>>("std::vector<ascend::session::DiagnosticView>");
        qRegisterMetaType<std::vector<session::BranchRequest>>();
        qRegisterMetaType<std::any>("std::any");
        return true;
    }();
    (void)once;
}

// 运行目录相对路径 → 绝对路径；文件或目录存在则使用运行目录副本，
// 否则回退编译期源码路径（开发构建不复制资源也能直接运行）。
std::string resolve_runtime_path(const std::string& base_dir, const char* relative, const char* source_path) {
    if (base_dir.empty()) return source_path;
    const QString candidate = QDir(QString::fromStdString(base_dir)).filePath(QString::fromUtf8(relative));
    return QFileInfo::exists(candidate) ? candidate.toStdString() : std::string(source_path);
}

}  // namespace

ResourcePaths resource_paths_from(const std::string& base_dir) {
    return ResourcePaths{
        resolve_runtime_path(base_dir, "resources/engine/i18n/zh-CN.json", ASCEND_WORKBENCH_ENGINE_I18N),
        resolve_runtime_path(base_dir, "resources/workbench/i18n/zh-CN.json", ASCEND_WORKBENCH_SESSION_I18N),
        resolve_runtime_path(base_dir, "resources/workbench/app/i18n/zh-CN.json", ASCEND_WORKBENCH_UI_I18N),
        resolve_runtime_path(base_dir, "resources/engine/examples/experiment", ASCEND_WORKBENCH_EXAMPLE_ROOT),
    };
}

ResourcePaths default_resource_paths() {
    return resource_paths_from(QCoreApplication::applicationDirPath().toStdString());
}

session::ModelTemplate example_template(const ResourcePaths& paths) {
    session::ModelTemplate model;
    model.name = "确定性三变量";
    model.assembly = example::environment();
    model.spec = example::specification();
    model.factories = example::factories(paths.example_root);
    model.implementations = {{example::plant_definition, example::plant_implementation},
                             {example::stimulus_definition, example::stimulus_implementation}};
    model.resources = {{engine_text_domain, paths.engine_i18n},
                       {session::session_text_domain, paths.session_i18n}};
    const auto module_resources = example::i18n_resources(paths.example_root);
    model.resources.insert(model.resources.end(), module_resources.begin(), module_resources.end());
    model.locale = "zh-CN";
    return model;
}

Workbench::Workbench(session::ModelTemplate model, QObject* parent) : QObject(parent) {
    register_metatypes();
    texts_ = std::make_shared<const UiTexts>(
        load_ui_texts(default_resource_paths().ui_i18n, "zh-CN"));
    auto adapters = std::make_shared<const session::AdapterRegistry>();
    controller_ = new SessionController(std::move(model), adapters);
    thread_ = new QThread(this);
    controller_->moveToThread(thread_);
    connect(thread_, &QThread::finished, controller_, &QObject::deleteLater);
    window_ = std::make_unique<MainWindow>(controller_, adapters, texts_);
    window_->setCloseHandler([this] { shutdown(); });
}

Workbench::~Workbench() { shutdown(); }

void Workbench::start() {
    if (stopped_) return;
    window_->show();
    thread_->start();
    window_->beginInitialLoad();
}

void Workbench::shutdown() {
    if (stopped_) return;
    stopped_ = true;
    if (controller_) controller_->requestStop();
    if (thread_ && thread_->isRunning()) {
        // 阻塞等待当前命令在步骤边界结束后返回；随后退出线程事件循环。
        QMetaObject::invokeMethod(controller_, "prepareShutdown", Qt::BlockingQueuedConnection);
        thread_->quit();
        thread_->wait();
    } else {
        delete controller_;
    }
    controller_ = nullptr;
}

}  // namespace ascend::workbench
