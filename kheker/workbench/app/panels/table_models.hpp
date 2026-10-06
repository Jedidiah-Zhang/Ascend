#pragma once

// 底部面板的只读表模型：检查报告、实际输入记录与诊断日志。
// 由面板在状态层相应变更时供数，仅依赖 QtCore（展示文本经 ui_text／ui_format），
// 供 QTableView 呈现。设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "ui_text.hpp"

#include <ascend/session/session.hpp>

#include <QAbstractTableModel>
#include <QString>

#include <vector>

namespace ascend::workbench {

// 检查报告表：区域／对象／结果／说明；未检查时无行，说明文字由面板标签给出。
class CheckTableModel : public QAbstractTableModel {
    Q_OBJECT

public:
    explicit CheckTableModel(const UiTexts& texts, QObject* parent = nullptr);

    void set_report(const session::CheckReport& report, bool has_check);

    int rowCount(const QModelIndex& parent = QModelIndex()) const override;
    int columnCount(const QModelIndex& parent = QModelIndex()) const override;
    QVariant data(const QModelIndex& index, int role = Qt::DisplayRole) const override;
    QVariant headerData(int section, Qt::Orientation orientation,
                        int role = Qt::DisplayRole) const override;

private:
    const UiTexts& texts_;
    session::CheckReport report_;
    bool has_check_ = false;
};

// 实际输入记录表：驱动前逻辑帧／输入／值。
class RecordTableModel : public QAbstractTableModel {
    Q_OBJECT

public:
    explicit RecordTableModel(const UiTexts& texts, QObject* parent = nullptr);

    void set_record(const session::RecordView& record);

    int rowCount(const QModelIndex& parent = QModelIndex()) const override;
    int columnCount(const QModelIndex& parent = QModelIndex()) const override;
    QVariant data(const QModelIndex& index, int role = Qt::DisplayRole) const override;
    QVariant headerData(int section, Qt::Orientation orientation,
                        int role = Qt::DisplayRole) const override;

private:
    const UiTexts& texts_;
    session::RecordView record_;
};

// 诊断表：类别／目标／来源／消息；语言资源加载错误排在结构化诊断之前。
// entry_role 返回诊断下标：资源错误行为 -1；面板据此决定是否展示原因链。
class DiagnosticTableModel : public QAbstractTableModel {
    Q_OBJECT

public:
    static constexpr int entry_role = Qt::UserRole + 1;

    explicit DiagnosticTableModel(const UiTexts& texts, QObject* parent = nullptr);

    void set_entries(const std::vector<session::DiagnosticView>& diagnostics,
                     const std::vector<QString>& resource_errors);
    // 诊断下标：资源错误行与越界返回 -1，否则为在 diagnostics 中的位置。
    int entry_index(const QModelIndex& index) const;

    int rowCount(const QModelIndex& parent = QModelIndex()) const override;
    int columnCount(const QModelIndex& parent = QModelIndex()) const override;
    QVariant data(const QModelIndex& index, int role = Qt::DisplayRole) const override;
    QVariant headerData(int section, Qt::Orientation orientation,
                        int role = Qt::DisplayRole) const override;

private:
    const UiTexts& texts_;
    std::vector<session::DiagnosticView> diagnostics_;
    std::vector<QString> resource_errors_;
};

}  // namespace ascend::workbench
