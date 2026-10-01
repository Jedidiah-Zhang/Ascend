#pragma once

#include "ui_text.hpp"

#include <ascend/session/session.hpp>

#include <QDialog>

#include <cstdint>
#include <vector>

class QDialogButtonBox;
class QLabel;
class QLineEdit;
class QTableWidget;

namespace ascend::workbench {

// 建立对照与干预分支的对话框：选择检查点上的一个整数字段并输入新值。
class BranchDialog : public QDialog {
    Q_OBJECT

public:
    BranchDialog(std::int64_t boundary, const std::vector<session::StateFieldView>& fields,
                 const session::ValueAdapter& integer_adapter, const UiTexts& texts, QWidget* parent = nullptr);

    // 干预分支请求；未选择字段时返回空列表。
    std::vector<ascend::Intervention> interventions() const;

private Q_SLOTS:
    void validate();

private:
    int selected_row() const;

    std::vector<session::StateFieldView> fields_;
    const session::ValueAdapter& adapter_;
    const UiTexts& texts_;
    QTableWidget* table_ = nullptr;
    QLineEdit* value_ = nullptr;
    QLabel* hint_ = nullptr;
    QDialogButtonBox* buttons_ = nullptr;
};

}  // namespace ascend::workbench
