#pragma once

#include "ui_text.hpp"

#include <ascend/config.hpp>
#include <ascend/session/adapter.hpp>

#include <QString>
#include <QStringList>
#include <QWidget>

#include <vector>

class QFormLayout;
class QLineEdit;

namespace ascend::workbench {

// 实例构造配置编辑器：对象和嵌套对象展开为字段行，数组整体只读；
// 只有整数叶可编辑，解析按适配器进行并保持 64 位精度。
// 空配置（null 或空对象）没有可推导字段，提供一个 JSON 值输入（与装配文件同一解析器）。
class ConfigEditor : public QWidget {
    Q_OBJECT

public:
    ConfigEditor(const session::ValueAdapter& adapter, const UiTexts& texts, QWidget* parent = nullptr);

    void setConfig(const ascend::Config& config);
    const ascend::Config& config() const noexcept { return config_; }

Q_SIGNALS:
    void configEdited(const ascend::Config& config);

private:
    struct Leaf {
        QStringList path;
        QLineEdit* edit = nullptr;
        QString committed;
        bool json = false;  // 空配置：整份配置的 JSON 值输入
    };

    void rebuild();
    void addValue(const QString& prefix, const ascend::Config& value);
    void commit(Leaf& leaf);
    void markError(QLineEdit* edit, const QString& message);
    void clearError(QLineEdit* edit);

    const session::ValueAdapter& adapter_;
    const UiTexts& texts_;
    ascend::Config config_;
    QFormLayout* form_ = nullptr;
    bool built_ = false;
    std::vector<std::unique_ptr<Leaf>> leaves_;
};

}  // namespace ascend::workbench
