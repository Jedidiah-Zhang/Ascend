#include "workbench.hpp"

#include <QApplication>
#include <QCoreApplication>

int main(int argc, char** argv) {
    QApplication application(argc, argv);
    ascend::workbench::Workbench workbench(
        ascend::workbench::example_template(ascend::workbench::default_resource_paths()));
    const auto& texts = workbench.ui_texts();
    QCoreApplication::setApplicationName(ui_text(texts, "workbench.app.name", "Ascend Workbench"));

    workbench.start();
    return application.exec();
}
