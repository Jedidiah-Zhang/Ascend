#pragma once

#include <ascend/session/session.hpp>

#include <QDialog>
#include <QString>

#include <vector>

class QTableWidget;

namespace ascend::workbench {

struct UiTexts;

// 运行记录列表：名称、模型与序列数；不可读记录标注原因且不可打开。
// 对话框只呈现与选择；打开动作由外壳提交给控制层（WB-16）。
class RecordsDialog : public QDialog {
    Q_OBJECT

public:
    RecordsDialog(const UiTexts& texts, std::vector<ascend::session::RecordEntryView> records,
                  QWidget* parent = nullptr);

    // 选中的记录路径；未选择或记录不可读时为空。
    QString selected_path() const;

private:
    std::vector<ascend::session::RecordEntryView> records_;
    QTableWidget* table_ = nullptr;
};

}  // namespace ascend::workbench
