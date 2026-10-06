#pragma once

// 底部面板：诊断日志与原因链、检查报告、实际输入记录。
// 只读工作区状态渲染，不访问会话；请求记录经 record_requested 交给外壳。
// 表数据由面板持有的 QAbstractTableModel 呈现（table_models.hpp）。
// 设计规则见 docs/研究平台/因果建模工作台/设计.md 的 WB-19。

#include "table_models.hpp"
#include "ui_text.hpp"

#include <ascend/session/session.hpp>

#include <QModelIndex>
#include <QString>
#include <QWidget>

#include <vector>

class QLabel;
class QTabWidget;
class QTableView;
class QTextBrowser;

namespace ascend::workbench {

class DiagnosticsPanel : public QWidget {
    Q_OBJECT

public:
    explicit DiagnosticsPanel(const UiTexts& texts, QWidget* parent = nullptr);

    // 渲染入口：由外壳在状态层相应变更时调用。
    void render_diagnostics(const std::vector<session::DiagnosticView>& diagnostics);
    void render_check(const session::CheckReport& report, bool has_check);
    void render_record(const session::RecordView& record, const QString& file_path);
    // 启动阶段的语言资源加载错误（结构化诊断之外的展示条目）。
    void append_resource_error(const QString& message);
    void show_check_tab();

Q_SIGNALS:
    void record_requested();

private:
    void refresh_diagnostics_model();
    void update_causes(const QModelIndex& index);

    const UiTexts& texts_;
    QTabWidget* tabs_ = nullptr;
    QTableView* diagnostics_view_ = nullptr;
    QTextBrowser* causes_view_ = nullptr;
    QLabel* check_note_ = nullptr;
    QTableView* check_view_ = nullptr;
    QLabel* record_label_ = nullptr;
    QTableView* record_view_ = nullptr;
    CheckTableModel* check_model_ = nullptr;
    RecordTableModel* record_model_ = nullptr;
    DiagnosticTableModel* diagnostic_model_ = nullptr;
    const std::vector<session::DiagnosticView>* diagnostics_ = nullptr;
    std::vector<QString> resource_errors_;
};

}  // namespace ascend::workbench
